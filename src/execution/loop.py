"""A bounded/resumable harness repair loop; actual observations decide passing."""

import os
import shutil
import time
from pathlib import Path
from lightre.paths import SOURCE_DIR

from .guide import ensure, record
from .process import execute, succeeded
from .runner import association, run, source_files, _target
from .storage import digest, exclusive, identifier, read_json, write_json
from .contract import Contract, baseline_files, case_request, cases


PROMPT = Path(__file__).parent / 'prompts/REPAIR_HARNESS.md'


def _protected(task):
    protected = read_json(task / 'protected.json')
    for filename, sha in protected.items():
        if digest(filename) != sha:
            raise RuntimeError(f'Fixed task/checker input changed: {filename}')


def _verify(directory, harness, task, destination, cancelled, timeout, fds, bootstrap=False):
    destination.mkdir(exist_ok=True)
    report = {'status': 'checking', 'passed': False, 'runs': [], 'started_at': time.time()}
    write_json(destination / 'verification.json', report)
    try:
        _protected(task)
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
        _protected(task)
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


def _create(directory, harness, parameters, description, workflow=None):
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


def repair(directory, *, task=None, description='修复目标 harness，真实调用 SO 并通过固定用例。',
           max_rounds=3, timeout=300, cancelled=lambda: False, workflow=None, bootstrap=False):
    """One foreground owner. Restart with the saved task; never trust old passing."""
    if (max_rounds is not None and max_rounds < 1) or timeout <= 0:
        raise ValueError('max_rounds and timeout must be positive')
    directory = Path(directory).resolve()
    harness, parameters = association(directory)
    with exclusive(directory, harness) as fds:
        ensure(harness, directory.name)
        if task is None:
            task, state = _create(directory, harness, parameters, description, workflow)
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
            _protected(task)
            # A deliberate resume consumes the old cancellation request.
            (task / 'cancel').unlink(missing_ok=True)
        stop = lambda: cancelled() or (task / 'cancel').exists()
        state.update(status='running', pid=os.getpid(), stopped_reason=None)
        write_json(task / 'state.json', state)
        try:
            _drive(directory, harness, task, state, max_rounds, timeout, stop, fds, workflow, bootstrap)
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            state.update(status='error', stopped_reason=str(error))
        except BaseException:
            state.update(status='interrupted', stopped_reason='Executor interrupted; verify current files on resume')
            raise
        finally:
            state['updated_at'] = time.time()
            write_json(task / 'state.json', state)
    return state


def _drive(directory, harness, task, state, max_rounds, timeout, stop, fds, workflow=None, bootstrap=False):
    check = task / ('check-' + identifier())
    verification = _verify(directory, harness, task, check, stop, timeout, fds, bootstrap)
    state['latest_verification'] = str(check / 'verification.json')
    failures = 0
    rounds = 0
    while max_rounds is None or rounds < max_rounds:
        if stop():
            state.update(status='cancelled', stopped_reason='Cancellation requested')
            return
        if verification['passed']:
            state.update(status='complete', stopped_reason=None,
                         verified_harness=verification['harness_files'])
            return
        if verification.get('observed'):
            state.update(status='observed', stopped_reason='Native execution observed; no independent expected results supplied',
                         verified_harness=verification['harness_files'])
            return
        if verification['status'] in ('checker_error', 'error'):
            state.update(status='error', stopped_reason=f'Fix required resource/checker failure; see {state["latest_verification"]}')
            return
        executable = shutil.which(os.environ.get('LIGHTRE_CODEX_BIN', 'codex'))
        if not executable:
            state.update(status='error', stopped_reason='Install/login codex, then resume this task')
            return
        state['round'] += 1
        rounds += 1
        current = task / f'round-{state["round"]:04d}'
        while current.exists():
            # Recover a crash between directory creation and state publication.
            state['round'] += 1
            current = task / f'round-{state["round"]:04d}'
        current.mkdir()  # Never reuse a previous attempt's result or logs.
        previous = state.get('latest_round')
        prompt = Path(workflow or PROMPT).read_text() + f'\n可编辑 harness：{harness}\n先读并适配说明：{harness / "HARNESS.md"}\n只读原始任务：{task / "task.md"}\n只读契约与固定用例：{task / "contract.json"}\n最新实际检查：{state["latest_verification"]}\n'
        if workflow:
            prompt += f'只读 workflow 目录：{Path(workflow).parent}\nLightRE Python 模块目录：{SOURCE_DIR}\n'
        if previous:
            prompt += f'上轮完整返回：{previous}/result.md\n上轮执行情况：{previous}/execution.json\n上轮检查：{previous}/verification.json\n缺失返回须按执行记录处理，不能沿用更早的完成声明。\n'
        (current / 'prompt.md').write_text(prompt, encoding='utf-8')
        state.update(latest_round=str(current), status='running')
        write_json(task / 'state.json', state)
        command = [executable, '-a', 'never', 'exec', '--ephemeral', '--skip-git-repo-check',
                   '--sandbox', os.environ.get('LIGHTRE_HARNESS_SANDBOX',
                                               os.environ.get('LIGHTRE_CODEX_SANDBOX', 'danger-full-access')),
                   '--disable', 'plugins', '--disable', 'apps', '--disable', 'multi_agent',
                   '-C', str(harness), '--json', '-o', str(current / 'result.md')]
        profile = os.environ.get('LIGHTRE_CODEX_PROFILE')
        if profile:
            command.extend(['-p', profile])
        command.append('-')
        with (current / 'prompt.md').open('rb') as stdin:
            execution = execute(command, harness, current, stdin=stdin, timeout=timeout,
                                cancelled=stop, stdout='events.jsonl', pass_fds=fds)
        result = current / 'result.md'
        execution['result'] = {'path': str(result), 'exists': result.is_file(),
                               'bytes': result.stat().st_size if result.is_file() else 0}
        write_json(current / 'execution.json', execution)
        # Even CLI errors can leave real edits; verify them after all writers exit.
        verification = _verify(directory, harness, task, current, stop, timeout, fds, bootstrap)
        state['latest_verification'] = str(current / 'verification.json')
        write_json(task / 'state.json', state)
        if stop():
            state.update(status='cancelled', stopped_reason='Cancellation requested')
            return
        if verification['passed']:
            state.update(status='complete', stopped_reason=None,
                         verified_harness=verification['harness_files'])
            return
        if verification.get('observed'):
            state.update(status='observed', stopped_reason='Native execution observed; no independent expected results supplied',
                         verified_harness=verification['harness_files'])
            return
        if verification['status'] in ('checker_error', 'error'):
            state.update(status='error', stopped_reason=f'Fix required resource/checker failure; see {state["latest_verification"]}')
            return
        healthy = succeeded(execution) and execution['result']['bytes'] > 0
        failures = 0 if healthy else failures + 1
        if failures >= 2:
            state.update(status='error', stopped_reason='Two consecutive CLI failures/missing final replies; fix environment and resume')
            return
    state.update(status='limit', stopped_reason=f'Caller limit reached ({max_rounds} rounds this invocation); resume with saved task')
