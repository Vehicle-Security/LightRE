"""Rehost 编排：工程创建、静态检查、任务生命周期与验证轮次循环。

流程：start() 为新 SO 建 harness 工程并固定契约；run_rounds() 创建/恢复任务，
驱动 [真实构建+运行+契约检查 → 未过则组 prompt（知识库/上轮记录）交 agent
修改 harness → 再验证]。终态：complete / observed / error / cancelled /
连续两次 agent 失败 / limit（轮次上限）。结果写入任务 state.json。
"""

import hashlib
import os
import shutil
import time
from contextlib import ExitStack
from pathlib import Path

from agents import collect_blocking, run_blocking
from lightre.paths import SOURCE_DIR
from lightre.prompt import render_prompt
from lightre.workspace import PROJECT_DIR, prepare_workspace

from .contract import Contract, baseline_files, case_request, cases
from .knowledge import directory as knowledge_directory, ensure_guide, record_check, summary as knowledge_summary
from .runner import run, source_files, target_info
from .storage import (WriterBusy, association, digest, execute, exclusive, identifier,
                      read_json, succeeded, write_json)


WORKFLOWS = Path(__file__).parent / 'workflows/unidbg'
WORKFLOW = WORKFLOWS / 'WORKFLOW.md'
MAIN_CLASS = 'lightre.RehostHarness'
PROMPT = Path(__file__).parent / 'prompts/REPAIR_HARNESS.md'
EXECUTOR = Path(__file__).parent


# ============================================================================
# 任务生命周期：受保护清单、校验、创建与恢复
# ============================================================================

def _protected_sections(task):
    """读取受保护清单，返回 (任务输入, 执行器代码) 两个 SHA256 字典。

    兼容旧版扁平格式：rehost 模块目录下的路径视为执行器代码。
    """
    protected = read_json(task / 'protected.json')
    if 'inputs' in protected:
        return protected['inputs'], protected['executor']
    inputs, executor = {}, {}
    for path, sha in protected.items():
        section = executor if path.startswith(str(EXECUTOR)) else inputs
        section[path] = sha
    return inputs, executor


def _check_immutable(task):
    """校验受保护输入（任务、契约、SO、执行器代码）自任务创建后未被改动。"""
    for section in _protected_sections(task):
        for filename, sha in section.items():
            if digest(filename) != sha:
                raise RuntimeError(f'Fixed task/checker input changed: {filename}')


def _accept_executor_update(task):
    """显式恢复任务时接受执行器代码升级：仅重置执行器基线，任务输入照常严格校验。"""
    inputs, executor = _protected_sections(task)
    executor = {filename: digest(filename) for filename in executor
                if Path(filename).exists()}  # 重构可能删除模块文件。
    write_json(task / 'protected.json', {'inputs': inputs, 'executor': executor})


def _new_task(directory, harness, parameters, description, workflow=None):
    """创建任务目录：固定任务描述、契约快照、受保护输入清单和初始状态。"""
    task = directory / 'tasks' / ('harness-' + identifier())
    task.mkdir(parents=True)
    (task / 'task.md').write_text(description, encoding='utf-8')
    baseline = baseline_files(directory)
    verification = task / 'verification-contract'
    verification.mkdir()
    checker = Contract.current(directory).save(verification)
    fixed_cases = []
    input_paths = []
    for index, row in enumerate(cases(directory)):
        request = case_request(directory, row['id'])
        normalized, data, origin, wanted = checker.prepare(request)
        if origin:
            input_paths.append(Path(origin['path']))
        if 'input_file' in normalized:
            saved_input = task / f'input-{index:04d}.bin'
            saved_input.write_bytes(data)
            normalized['input_file'] = str(saved_input)
            input_paths.append(saved_input)
        fixed_cases.append({'id': row['id'], 'request': normalized, 'expected': wanted})
    if baseline_files(directory) != baseline:
        raise RuntimeError('Fixed verification contract changed while creating task')
    contract = {'binary': target_info(directory), 'harness': str(harness),
                'parameters': parameters, 'cases': fixed_cases,
                'verification_contract': str(verification / 'contract.json'),
                'invocation': 'java -cp <classes:dependencies> <main_class> SO request.json observation.json'}
    write_json(task / 'contract.json', contract)
    # Fixed task, immutable binary and executor code are baselines, not model-editable.
    executor_paths = [*EXECUTOR.glob('*.py'), *(EXECUTOR / 'prompts').glob('*.md')]
    if workflow:
        executor_paths.extend(path for path in Path(workflow).parent.rglob('*') if path.is_file())
    input_paths.extend([task / 'task.md', task / 'contract.json',
                        Path(contract['binary']['path']), *map(Path, baseline),
                        *verification.iterdir()])
    config = read_json(directory / 'harness.json')
    input_paths.extend((directory / path).resolve() for path in config.get('protected_files', []))
    write_json(task / 'protected.json', {
        'inputs': {str(path): digest(path) for path in input_paths},
        'executor': {str(path): digest(path) for path in executor_paths},
    })
    state = {'id': task.name, 'directory': str(task), 'mode': 'harness', 'status': 'created',
             'round': 0, 'created_at': time.time(), 'latest_round': None}
    if workflow:
        state['workflow'] = str(Path(workflow).resolve())
    write_json(task / 'state.json', state)
    return task, state


def run_rounds(directory, *, task=None, description=None,
               max_rounds=3, timeout=300, cancelled=lambda: False, workflow=None, bootstrap=False):
    """入口：创建或恢复任务，执行验证与 agent 轮次循环，返回最终任务状态。"""
    if (max_rounds is not None and max_rounds < 1) or timeout <= 0:
        raise ValueError('max_rounds and timeout must be positive')
    directory = Path(directory).resolve()
    harness, parameters = association(directory)
    with exclusive(directory, harness) as fds:
        ensure_guide(harness, directory.name)
        knowledge_directory(directory)
        # An explicit description demands at least one agent round; otherwise a
        # passing/observed bootstrap verification would return before acting on it.
        min_rounds = 1 if (task is None and description is not None) else 0
        if task is None:
            if description is None:
                description = PROMPT.with_name('REPAIR_TASK.md').read_text(encoding='utf-8').rstrip('\n')
            task, state = _new_task(directory, harness, parameters, description, workflow)
        else:
            task = Path(task).resolve()
            if task.parent != directory / 'tasks':
                raise ValueError('Resume task must belong to this SO workspace')
            state = read_json(task / 'state.json')
            if state.get('workflow') != (str(Path(workflow).resolve()) if workflow else None):
                raise ValueError('Resume with the original workflow entry point')
            contract = read_json(task / 'contract.json')
            if contract['harness'] != str(harness) or contract['parameters'] != parameters:
                raise ValueError('Task harness association has changed')
            # A deliberate resume accepts executor upgrades; task inputs stay strict.
            _accept_executor_update(task)
            _check_immutable(task)
            # The service may have exited while the Agent worker was still finishing.
            # Its writer lock has now been released; recover the durable final reply.
            previous = state.get('latest_round')
            if previous and (Path(previous) / 'agent.json').is_file():
                collect_blocking(Path(previous))
            # A deliberate resume consumes the old cancellation request.
            (task / 'cancel').unlink(missing_ok=True)
        stop = lambda: cancelled() or (task / 'cancel').exists()
        state.update(status='running', pid=os.getpid(), stopped_reason=None)
        write_json(task / 'state.json', state)
        try:
            _round_loop(directory, harness, task, state, max_rounds, timeout, stop, fds, workflow,
                        bootstrap, min_rounds)
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            state.update(status='error', stopped_reason=str(error))
        except BaseException:
            state.update(status='interrupted', stopped_reason='Executor interrupted; verify current files on resume')
            raise
        finally:
            state['updated_at'] = time.time()
            write_json(task / 'state.json', state)
    return state


# ============================================================================
# 验证与轮次循环
# ============================================================================

def _check_harness(directory, harness, task, destination, cancelled, timeout, fds, bootstrap=False):
    """真实构建并运行 harness，按契约逐用例检查；bootstrap 时允许 harness 缺源码。"""
    destination.mkdir(exist_ok=True)
    report = {'status': 'checking', 'passed': False, 'runs': [], 'started_at': time.time()}
    write_json(destination / 'verification.json', report)
    try:
        _check_immutable(task)
        contract = read_json(task / 'contract.json')
        if target_info(directory) != contract['binary']:
            raise RuntimeError('Task target no longer matches fixed SO')
        try:
            before = source_files(harness)
        except (OSError, ValueError) as error:
            if not bootstrap:
                raise
            report.update(status='harness_incomplete', error=str(error))
            return report
        for case in contract['cases']:
            if cancelled():
                report['status'] = 'cancelled'
                break
            current = run(directory, case['request'], timeout=timeout,
                          cancelled=cancelled, _lock_fds=fds, _update_guide=False)
            report['runs'].append(current)
            if current.get('expected') != case['expected']:
                raise RuntimeError('Fixed expected results no longer match checker')
            if not current['passed'] and not current.get('observed'):
                report['status'] = current['status']
                break
        else:
            observed = all(item.get('observed') for item in report['runs'])
            report.update(status='observed' if observed else 'passed', passed=not observed,
                          observed=observed)
        _check_immutable(task)
        if source_files(harness) != before:
            raise RuntimeError('Editable harness changed during verification')
        record_check(harness, report, destination / 'verification.json')
        report['harness_files'] = source_files(harness)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        report.update(status='checker_error', passed=False, error=str(error))
    finally:
        report['finished_at'] = time.time()
        write_json(destination / 'verification.json', report)
    return report


def _conclude(state, verification, rounds, min_rounds):
    """验证达到终态时写入任务状态并返回 True，否则返回 False 继续下一轮。"""
    if verification['passed']:
        state.update(status='complete', stopped_reason=None,
                     verified_harness=verification['harness_files'])
        return True
    if verification.get('observed') and rounds >= min_rounds:
        state.update(status='observed', stopped_reason='Native execution observed; no independent expected results supplied',
                     verified_harness=verification['harness_files'])
        return True
    if verification['status'] in ('checker_error', 'error'):
        state.update(status='error', stopped_reason=f'Fix required resource/checker failure; see {state["latest_verification"]}')
        return True
    return False


def _round_loop(directory, harness, task, state, max_rounds, timeout, stop, fds, workflow=None,
                bootstrap=False, min_rounds=0):
    """验证→（未过则）agent 修改→再验证的循环；退出判定统一走 _conclude()。"""
    check = task / ('check-' + identifier())
    verification = _check_harness(directory, harness, task, check, stop, timeout, fds, bootstrap)
    state['latest_verification'] = str(check / 'verification.json')
    failures = 0
    rounds = 0
    while max_rounds is None or rounds < max_rounds:
        if stop():
            state.update(status='cancelled', stopped_reason='Cancellation requested')
            return
        if _conclude(state, verification, rounds, min_rounds):
            return
        # 准备下一轮
        state['round'] += 1
        rounds += 1
        current = task / f'round-{state["round"]:04d}'
        while current.exists():
            # Recover a crash between directory creation and state publication.
            state['round'] += 1
            current = task / f'round-{state["round"]:04d}'
        current.mkdir()  # Never reuse a previous attempt's result or logs.
        previous = state.get('latest_round')
        prompt = Path(workflow or PROMPT).read_text(encoding='utf-8') + render_prompt(
            PROMPT.with_name('ROUND.md'), HARNESS=harness, GUIDE=harness / 'HARNESS.md',
            TASK=task / 'task.md', CONTRACT=task / 'contract.json',
            VERIFICATION=state['latest_verification'],
            KNOWLEDGE=knowledge_summary(knowledge_directory(directory)))
        if workflow:
            prompt += render_prompt(PROMPT.with_name('WORKFLOW_CONTEXT.md'),
                                    WORKFLOW_DIR=Path(workflow).parent, SOURCE_DIR=SOURCE_DIR)
        if previous:
            prompt += render_prompt(PROMPT.with_name('PREVIOUS_ROUND.md'), PREVIOUS=previous)
        (current / 'prompt.md').write_text(prompt, encoding='utf-8')
        state.update(latest_round=str(current), status='running')
        write_json(task / 'state.json', state)

        try:
            execution = run_blocking(
                root=directory / 'agents',
                directory=current,
                cwd=harness,
                prompt=prompt,
                timeout=timeout,
                cancelled=stop,
                pass_fds=fds,
                sandbox=os.environ.get('LIGHTRE_HARNESS_SANDBOX'),
            )
        except Exception as error:
            execution = {
                'status': 'failed',
                'exit_code': 1,
                'error': str(error),
                'result': {'path': str(current / 'result.md'), 'exists': False, 'bytes': 0}
            }
            write_json(current / 'execution.json', execution)

        # Even CLI errors can leave real edits; verify them after all writers exit.
        verification = _check_harness(directory, harness, task, current, stop, timeout, fds, bootstrap)
        state['latest_verification'] = str(current / 'verification.json')
        write_json(task / 'state.json', state)
        if stop():
            state.update(status='cancelled', stopped_reason='Cancellation requested')
            return
        if _conclude(state, verification, rounds, min_rounds):
            return
        healthy = succeeded(execution) and execution['result']['bytes'] > 0
        failures = 0 if healthy else failures + 1
        if failures >= 2:
            state.update(status='error', stopped_reason='Two consecutive CLI failures/missing final replies; fix environment and resume')
            return
    state.update(status='limit', stopped_reason=f'Caller limit reached ({max_rounds} rounds this invocation); resume with saved task')


# ============================================================================
# 对外入口：新目标、显式恢复、自动接续
# ============================================================================

def start(target, *, description=None, workspace=None, inputs=(), materials=(), cases_file=None,
          checker=None, artifacts=(), max_rounds=None, timeout=600, cancelled=lambda: False):
    """Each invocation creates a fresh project; resume is explicit and keeps its files."""
    if description is not None and not description.strip():
        raise ValueError('Describe the entry/behavior to run, or request entry investigation explicitly')
    if (max_rounds is not None and max_rounds < 1) or timeout <= 0:
        raise ValueError('max_rounds and timeout must be positive')
    target = Path(target).resolve()
    if target.is_file():
        if workspace is None:
            raise ValueError('A new SO requires --workspace; existing analyses reuse their workspace')
        target = prepare_workspace(target, Path(workspace), name=target.stem)
    if target.is_relative_to(PROJECT_DIR):
        raise ValueError('Rehost outputs must use an external workspace')
    binary = target_info(target)
    inputs = [Path(path).resolve(strict=True) for path in inputs]
    materials = [Path(path).resolve(strict=True) for path in materials]
    if any(not path.is_file() for path in inputs):
        raise ValueError('Each seed input must be a file')
    if cases_file is not None and inputs:
        raise ValueError('Use either fixed cases or seed inputs')
    if checker is not None and cases_file is None:
        raise ValueError('An independent checker requires explicit fixed cases')
    # Validate the fixed contract before creating projects or changing associations.
    mode = 'expected' if cases_file is not None else 'observe'
    if cases_file is not None:
        cases_file = Path(cases_file).resolve(strict=True)
        rows = read_json(cases_file)
        base = cases_file.parent
    else:
        rows = [{'id': f'seed-{index + 1}',
                 'request': {'input_file': str(path), 'input_name': path.name},
                 'input_sha256': digest(path)} for index, path in enumerate(inputs)]
        if not rows:
            rows = [{'id': 'entry', 'request': {}, 'input_sha256': hashlib.sha256(b'').hexdigest()}]
        base = target
    checker = Path(checker).resolve(strict=True) if checker else None
    spec = {'parameters': {'main_class': MAIN_CLASS}, 'artifacts': list(artifacts), 'verification': mode}
    contract = Contract(spec, rows, checker)
    for row in rows:
        contract.prepare(row['request'], base)

    key = identifier()
    harness = target.parent / 'harnesses' / f'{target.name}-rehost-{key}'
    harness.mkdir(parents=True)  # Never seed a new target from an existing Java project.
    with ExitStack() as stack:
        try:
            fds = stack.enter_context(exclusive(target, harness))
        except WriterBusy:
            shutil.rmtree(harness)
            raise
        setup = target / 'rehost' / key
        setup.mkdir(parents=True)
        ensure_guide(harness, target.name)  # 任务与协议由 task.md 承载，说明只留通用约定。
        shutil.copyfile(WORKFLOWS / 'pom.xml', harness / 'pom.xml')
        # There is deliberately no Java source, target ABI, JNI stub or RVA here.
        normalized_rows = []
        for row in rows:
            request = dict(row['request'])
            if 'input_file' in request:
                request['input_file'] = str((base / request['input_file']).resolve())
            normalized_rows.append({**row, 'request': request})
        write_json(setup / 'cases.json', normalized_rows)
        inspection = {'binary': binary, 'commands': []}
        for name, flags in [('headers', ['-h', '-l']), ('dependencies', ['-d']), ('symbols', ['-Ws'])]:
            inspection['commands'].append(execute(
                ['readelf', *flags, binary['path']], setup, setup / 'inspection' / name,
                timeout=30, cancelled=cancelled, pass_fds=fds))
        write_json(setup / 'inspection.json', inspection)
        body = render_prompt(WORKFLOWS / 'TASK.md', DESCRIPTION=description,
                             BINARY=binary['path'], SHA256=binary['sha256'],
                             INSPECTION=setup / 'inspection.json', INSPECTION_DIR=setup / 'inspection',
                             MODE=mode, MATERIALS=''.join(f'- {path}\n' for path in materials))
        (setup / 'task.md').write_text(body, encoding='utf-8')
        protected = [setup / 'task.md', setup / 'inspection.json',
                     *[path for path in (setup / 'inspection').rglob('*') if path.is_file()],
                     *[path for path in materials if path.is_file()],
                     *EXECUTOR.glob('*.py')]
        if cases_file:
            protected.append(cases_file)
        config = {'path': str(harness), **spec, 'cases': str(setup / 'cases.json'),
                  'workflow': str(WORKFLOW), 'task_description': str(setup / 'task.md'),
                  'protected_files': [str(path) for path in protected]}
        if checker:
            config['checker'] = str(checker)
        association_file = target / 'harness.json'
        if association_file.exists():
            shutil.copyfile(association_file, setup / 'previous-association.json')
        write_json(association_file, config)
    return run_rounds(target, description=body, workflow=WORKFLOW, bootstrap=True,
                      max_rounds=max_rounds, timeout=timeout, cancelled=cancelled)


def resume(directory, task, *, max_rounds=None, timeout=600, cancelled=lambda: False):
    """Continue the same generated project and full round history after rechecking."""
    return run_rounds(directory, task=task, workflow=WORKFLOW, bootstrap=True,
                      max_rounds=max_rounds, timeout=timeout, cancelled=cancelled)


def continue_analysis(directory, *, description=None, materials=(), max_rounds=None, timeout=600):
    """Reuse the associated workflow task, including a bootstrap interrupted before task creation."""
    directory = Path(directory)
    association_file = directory / 'harness.json'
    if association_file.exists():
        config = read_json(association_file)
        tasks = []
        for path in (directory / 'tasks').glob('*/state.json'):
            state = read_json(path)
            if state.get('mode') != 'harness' or state.get('workflow') != str(WORKFLOW):
                continue
            contract = read_json(path.parent / 'contract.json')
            if contract['harness'] == config['path']:
                tasks.append((path.parent, state))
        if tasks:
            task, state = max(tasks, key=lambda item: item[1]['created_at'])
            if state['status'] in ('complete', 'observed', 'cancelled'):
                return state
            return resume(directory, task, max_rounds=max_rounds, timeout=timeout)
        if config.get('workflow') == str(WORKFLOW):
            body = Path(config['task_description']).read_text(encoding='utf-8')
            return run_rounds(directory, description=body, workflow=WORKFLOW, bootstrap=True,
                              max_rounds=max_rounds, timeout=timeout)
    return start(directory, description=description, materials=materials, max_rounds=max_rounds,
                 timeout=timeout)
