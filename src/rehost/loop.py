"""Harness 任务的验证与轮次循环；以真实运行观察判定通过。

流程：run_rounds() 拿写锁后创建新任务或恢复旧任务，进入 _round_loop()：
每轮先用 _check_harness() 真实构建并运行 harness、按契约逐用例检查；
未通过时组装 prompt（含知识库与上轮记录）交给 agent 修改 harness，再次验证。
退出条件：complete（通过）/ observed（观察到运行、无固定预期）/ error /
cancelled / 连续两次 agent 失败 / 达到轮次上限 limit。结果写入任务 state.json。
"""

import os
import shutil
import time
from pathlib import Path
from lightre.paths import SOURCE_DIR
from lightre.prompt import render_prompt

from agents import collect_blocking, run_blocking
from .guide import ensure, record
from .knowledge import directory as knowledge_directory, summary as knowledge_summary
from .process import succeeded
from .runner import association, run, source_files, _target
from .storage import digest, exclusive, identifier, read_json, write_json
from .contract import Contract, baseline_files, case_request, cases


PROMPT = Path(__file__).parent / 'prompts/REPAIR_HARNESS.md'


def _check_immutable(task):
    """校验受保护输入（任务、契约、代码、workflow）自任务创建后未被改动。"""
    protected = read_json(task / 'protected.json')
    for filename, sha in protected.items():
        if digest(filename) != sha:
            raise RuntimeError(f'Fixed task/checker input changed: {filename}')


def _check_harness(directory, harness, task, destination, cancelled, timeout, fds, bootstrap=False):
    """真实构建并运行 harness，按契约逐用例检查；bootstrap 时允许 harness 缺源码。"""
    destination.mkdir(exist_ok=True)
    report = {'status': 'checking', 'passed': False, 'runs': [], 'started_at': time.time()}
    write_json(destination / 'verification.json', report)
    try:
        _check_immutable(task)
        contract = read_json(task / 'contract.json')
        if _target(directory) != contract['binary']:
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
        record(harness, report, destination / 'verification.json')
        report['harness_files'] = source_files(harness)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        report.update(status='checker_error', passed=False, error=str(error))
    finally:
        report['finished_at'] = time.time()
        write_json(destination / 'verification.json', report)
    return report


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
    contract = {'binary': _target(directory), 'harness': str(harness),
                'parameters': parameters, 'cases': fixed_cases,
                'verification_contract': str(verification / 'contract.json'),
                'invocation': 'java -cp <classes:dependencies> <main_class> SO request.json observation.json'}
    write_json(task / 'contract.json', contract)
    # Code, fixed task and immutable binary are baselines, not model-editable.
    paths = [task / 'task.md', task / 'contract.json', Path(contract['binary']['path']),
             *map(Path, baseline), *verification.iterdir(), *input_paths,
             *Path(__file__).parent.glob('*.py'),
             *(Path(__file__).parent / 'prompts').glob('*.md')]
    if workflow:
        paths.extend(path for path in Path(workflow).parent.rglob('*') if path.is_file())
    config = read_json(directory / 'harness.json')
    paths.extend((directory / path).resolve() for path in config.get('protected_files', []))
    write_json(task / 'protected.json', {str(path): digest(path) for path in paths})
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
        ensure(harness, directory.name)
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
