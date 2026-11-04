"""Read bounded, live Rehost evidence without taking the executor's writer lock."""

import fcntl
import json
import time
from pathlib import Path


ACTIVE = ('starting', 'running', 'waiting_for_recovery', 'waiting_for_writer')


def _path(root, value):
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Progress evidence must belong to this workspace')
    return path


def _json(root, value):
    try:
        path = _path(root, value)
        if path.stat().st_size > 4 * 1024 * 1024:
            return {}
        result = json.loads(path.read_text(encoding='utf-8'))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _tail(root, value, limit=8192):
    try:
        path = _path(root, value)
        with path.open('rb') as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - limit))
            text = stream.read(limit).decode('utf-8', errors='replace')
        return ('…\n' if size > limit else '') + text
    except (OSError, ValueError, TypeError):
        return ''


def _process(root, directory, fallback=None):
    record = _json(root, directory / 'execution.json') or fallback or {}
    return {key: record.get(key) for key in ('status', 'exit_code', 'duration', 'error')} | {
        'stdout': _tail(root, directory / 'stdout.log'),
        'stderr': _tail(root, directory / 'stderr.log'),
    }


def _run(root, report):
    if not report.get('run'):
        return None
    try:
        directory = _path(root, report['run'])
    except (ValueError, TypeError):
        return None
    report = _json(root, directory / 'verification.json') or report
    observation = report.get('observation') or _json(root, directory / 'observation.json')
    if not isinstance(observation, dict):
        observation = {}
    build = _process(root, directory / 'build', report.get('build'))
    execution = _process(root, directory / 'execute', report.get('execution'))
    returned = observation.get('status') == 'returned' and type(observation.get('native_calls')) is int and observation['native_calls'] > 0
    return {key: report.get(key) for key in ('status', 'phase', 'phase_started_at', 'error', 'differences', 'passed', 'observed')} | {
        'id': directory.name, 'build': build, 'execution': execution,
        'native_returned': returned if observation else None,
        'native_calls': observation.get('native_calls'), 'entry': str(observation.get('entry', ''))[:2000],
        'missing_environment': observation.get('missing_environment'),
        'observation': json.dumps(observation, ensure_ascii=False, indent=2)[:8192] if observation else '',
    }


def _writer_active(root):
    try:
        with (root / 'tasks/.lock').open('rb') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
    except FileNotFoundError:
        pass
    return False


def progress(directory, coordinator=None):
    root = Path(directory).resolve()
    if coordinator is None:
        coordinator = _json(root, root / 'main-workflow.json').get('rehost') or {}
    tasks = []
    for path in (root / 'tasks').glob('*/state.json'):
        state = _json(root, path)
        if state.get('mode') == 'harness':
            tasks.append((path.parent, state))
    task, state = max(tasks, key=lambda item: item[1].get('created_at', 0), default=(None, {}))
    status = 'error' if coordinator.get('status') == 'error' else state.get('status') or coordinator.get('status', 'not_started')
    phase = state.get('phase') or ('repairing' if state.get('latest_round') else 'checking')
    # A new request must not briefly display the previous attempt as completed.
    starting = coordinator.get('status') in ACTIVE and state.get('run_started_at', 0) < coordinator.get('requested_at', 0)
    if coordinator.get('status') in ('waiting_for_recovery', 'waiting_for_writer', 'starting') or starting:
        status, phase = coordinator['status'], 'starting'
    elif status == 'running' and task and not _writer_active(root):
        status = 'interrupted'
    error = coordinator.get('error') or (state.get('stopped_reason') if status in ('error', 'interrupted', 'limit') else None)
    verification = _json(root, state['latest_verification']) if state.get('latest_verification') else {}
    runs = verification.get('runs') or []
    current = verification.get('current_run')
    current_report = {'run': current} if current else runs[-1] if runs else {}
    run = _run(root, current_report)
    if status == 'running' and phase == 'checking' and run and run.get('phase') != 'finished':
        phase = run.get('phase') or phase
    phase_started = (run or {}).get('phase_started_at') if phase in ('preparing', 'checking_environment', 'building', 'executing', 'validating') else state.get('phase_started_at')
    result = {'status': status, 'phase': phase if status in ACTIVE else 'finished',
              'id': state.get('id'), 'round': state.get('round', 0),
              'error': error, 'can_retry': status not in ACTIVE,
              'phase_elapsed_seconds': max(0, int(time.time() - phase_started)) if phase_started and status == 'running' else None,
              'verification': {key: verification.get(key) for key in ('status', 'total_cases', 'current_case', 'error')},
              'completed_cases': len(runs), 'run': run, 'reply': ''}
    if state.get('latest_round'):
        try:
            result['reply'] = _tail(root, _path(root, state['latest_round']) / 'result.md')
        except (ValueError, TypeError):
            pass
    return result


def workflow_status(directory):
    root = Path(directory).resolve()
    state = _json(root, root / 'main-workflow.json') or {'status': 'not_started'}
    return {**state, 'rehost': progress(root, state.get('rehost') or {})}
