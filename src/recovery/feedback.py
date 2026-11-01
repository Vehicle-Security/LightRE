"""Per-suggestion replies and exact publication changes, independent of later edits."""

import difflib
import json
import os
import time
from pathlib import Path

from .candidates import read_candidate
from .queue import ACTIVE, snapshot, wait_reason, write_state


def _elapsed(job, rva=None):
    start = job.get('run_started_at', job.get('started_at', job['created_at']))
    if rva is not None and job['status'] == 'running':
        for batch in job.get('batches', {}).values():
            if batch.get('status') == 'running' and rva in batch.get('rvas', []):
                start = batch.get('started_at', start)
                break
    end = time.time() if job['status'] in ACTIVE else job.get('finished_at') or time.time()
    return max(0, int(end - start))


def _activity(job, rva):
    """Summarize actual CLI events without exposing output or inventing a percent."""
    if job['status'] != 'running':
        return None
    if job.get('phase') in ('ghidra', 'source'):
        return {'stage': 'preparing', 'label': '准备反编译参考' if job['phase'] == 'ghidra'
                else '整理源码上下文', 'detail': '', 'silent_seconds': 0}
    batches = [(number, batch) for number, batch in job.get('batches', {}).items()
               if batch.get('status') == 'running' and rva in batch.get('rvas', [])]
    if not batches:
        retrying = any(batch.get('status') == 'retry_wait' and rva in batch.get('rvas', [])
                       for batch in job.get('batches', {}).values())
        return {'stage': 'retry_wait' if retrying else 'starting',
                'label': '等待重试' if retrying else '准备模型调用', 'detail': '', 'silent_seconds': 0}
    number, batch = max(batches, key=lambda item: item[1].get('started_at', 0))
    path = Path(job['directory']) / f'batch-{int(number):04d}.events.jsonl'
    activity = {'stage': 'starting', 'label': '等待模型响应', 'detail': '', 'silent_seconds': 0}
    try:
        with path.open('rb') as stream:
            # Commands may emit large disassembly dumps. Only inspect a bounded
            # tail; discard a partial first/last line while the worker appends.
            size = stream.seek(0, 2)
            offset = max(0, size - 256 * 1024)
            stream.seek(offset)
            lines = stream.read(min(size, 256 * 1024)).splitlines(keepends=True)
            if offset:
                lines = lines[1:]
                activity.update(stage='model', label='模型运行中，等待新活动')
            updated_at = os.fstat(stream.fileno()).st_mtime
        for line in lines:
            if not line.endswith(b'\n'):
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(event, dict):
                continue
            item = event.get('item') or {}
            kind = event.get('type')
            if kind == 'turn.started':
                activity.update(stage='model', label='模型正在分析或生成回复', detail='')
            elif kind == 'turn.completed':
                activity.update(stage='publishing', label='模型已返回，正在检查并保存结果', detail='')
            elif isinstance(item, dict) and item.get('type') == 'command_execution':
                command = item.get('command') or ''
                detail = command.splitlines()[0][:180] if isinstance(command, str) and command else ''
                if kind == 'item.started':
                    activity.update(stage='tool', label='正在执行工具命令', detail=detail)
                elif kind == 'item.completed':
                    activity.update(stage='model', label='模型正在处理工具结果', detail=detail)
            elif isinstance(item, dict) and item.get('type') == 'agent_message' and kind == 'item.completed':
                activity.update(stage='model', label='模型正在整理分析与回复', detail='')
        if size:
            activity['silent_seconds'] = max(0, int(time.time() - updated_at))
    except OSError:
        pass
    return activity


def record_result(state, rva, before, after, reply, outcome=None):
    if before is None:
        outcome = outcome or 'unrecorded'
    elif outcome is None:
        outcome = 'updated' if before['display_code'] != after['display_code'] else 'unchanged'
    write_state(Path(state['directory']) / 'feedback-result.json', {
        'rva': rva, 'outcome': outcome, 'reply': reply, 'recorded_at': time.time(),
        'before': before, 'after': {key: after[key] for key in ('display_code', 'sha256', 'path')},
    })


def _legacy_result(directory, rva):
    # Old model notes can be shown, but their proposed code is not proof that
    # publication succeeded. Never attribute today's source to an old task.
    reply = None
    for path in sorted(directory.glob('batch-*.result.json')):
        try:
            items = json.loads(path.read_text()).get('results', [])
            for item in items:
                value = item.get('reply') or item.get('notes')
                if int(item['rva'], 0) == rva and isinstance(value, str):
                    reply = value
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    if reply is None:
        return None
    return {'rva': rva, 'outcome': 'unrecorded', 'reply': reply, 'before': None, 'after': None}


def _source_progress(jobs, rva, paused):
    relevant = [job for job in jobs if rva in job.get('rvas', [])]
    pending = [job for job in relevant if job['status'] in ACTIVE and rva not in job.get('done_rvas', [])]
    job = next((job for job in pending if job['status'] == 'running'),
               pending[0] if pending else max(relevant, key=lambda job: job['created_at'], default=None))
    if job is None:
        return {'state': 'idle', 'value': 0, 'indeterminate': False}
    state = job['status']
    key = f'{rva:#x}'
    error = job.get('error')
    if key in job.get('failed_rvas', {}):
        state, error = 'error', job['failed_rvas'][key]
    elif key in job.get('unresolved', {}):
        state, error = 'unresolved', job['unresolved'][key]
    elif rva in job.get('done_rvas', []):
        state = 'complete'
    elif state == 'running':
        if job.get('phase') in ('ghidra', 'source'):
            state = 'preparing'
        elif rva not in {int(value, 0) for value in job.get('current', [])}:
            state = 'waiting'
    if paused and state in ('queued', 'waiting'):
        state = 'paused'
    queued = [item['id'] for item in jobs if item['status'] == 'queued']
    ahead = (sum(item['status'] == 'running' for item in jobs) + queued.index(job['id'])
             if job['status'] == 'queued' else 0)
    return {'state': state, 'task_id': job['id'], 'mode': job.get('mode'), 'ahead': ahead,
            'elapsed_seconds': _elapsed(job, rva),
            'indeterminate': state in ('running', 'preparing'),
            'value': 1 if state in ('complete', 'partial', 'unresolved', 'error', 'cancelled') else 0,
            'error': error}


def history(reader, rva, limit=5):
    if rva not in {fn['rva'] for fn in reader.catalog()['functions']}:
        raise KeyError(f'未识别的函数 RVA：{rva:#x}')
    jobs = snapshot(reader)
    if any(job['status'] == 'running' for job in jobs):
        from .jobs import task_state
        observed = task_state(reader)
        for job in jobs:
            if job['id'] == observed.get('id') or (observed['status'] == 'interrupted' and job['status'] == 'running'):
                job.update(status=observed['status'])
                if observed.get('error'):
                    job['error'] = observed['error']
    jobs = [job for job in jobs if not job.get('dismissed')]
    comments = sorted((job for job in jobs if job.get('mode') == 'comment'
                       and rva in job.get('rvas', [])),
                      key=lambda job: (job['created_at'], job['id']), reverse=True)[:limit]
    queued = [job['id'] for job in jobs if job['status'] == 'queued']
    running_count = sum(job['status'] == 'running' for job in jobs)
    try:
        current_sha = read_candidate(reader, rva)['sha256'] if comments else None
    except (OSError, ValueError):
        current_sha = None
    items = []
    for job in comments:
        item = {key: job[key] for key in ('id', 'status', 'comment', 'created_at', 'started_at',
                                         'finished_at', 'error', 'phase', 'processed', 'total') if key in job}
        if f'{rva:#x}' in job.get('failed_rvas', {}):
            item['error'] = job['failed_rvas'][f'{rva:#x}']
        item['ahead'] = running_count + queued.index(job['id']) if job['status'] == 'queued' else 0
        item['elapsed_seconds'] = _elapsed(job)
        if job['status'] == 'queued':
            item['wait_reason'] = wait_reason(jobs, job)
        item['activity'] = _activity(job, rva)
        directory = Path(job['directory'])
        path = directory / 'feedback-result.json'
        result = None
        if path.exists():
            try:
                result = json.loads(path.read_text())
                if result['rva'] != rva:
                    raise ValueError('回复记录的函数 RVA 不匹配')
                if (result['outcome'] not in ('updated', 'unchanged', 'unresolved', 'conflict', 'unrecorded')
                        or not isinstance(result['reply'], str)):
                    raise ValueError('回复记录格式无效')
                for value in (result['before'], result['after']):
                    if value is not None and (not isinstance(value, dict)
                            or not {'display_code', 'sha256', 'path'} <= value.keys()
                            or value['display_code'] is not None and not isinstance(value['display_code'], str)):
                        raise ValueError('回复的源码快照无效')
            except (OSError, ValueError, KeyError, TypeError) as error:
                item['result_error'] = f'回复记录读取失败：{error}'
                result = None
        elif job['status'] not in ACTIVE:
            result = _legacy_result(directory, rva)
        if result:
            before, after = result['before'], result['after']
            lines = []
            if result['outcome'] == 'updated' and before and after:
                lines = list(difflib.unified_diff(
                    (before['display_code'] or '').splitlines(),
                    (after['display_code'] or '').splitlines(),
                    fromfile='修改前', tofile='修改后', lineterm=''))
                if not lines:
                    lines = ['仅换行格式或文件末尾换行发生变化']
            item['result'] = {
                'outcome': result['outcome'], 'reply': result['reply'],
                'path': after['path'] if after else None,
                'before_sha256': before['sha256'] if before else None,
                'after_sha256': after['sha256'] if after else None,
                'current_matches': current_sha == after['sha256'] if after and current_sha else None,
                'diff': '\n'.join(lines),
                'added_lines': sum(line.startswith('+') for line in lines[2:]),
                'removed_lines': sum(line.startswith('-') for line in lines[2:]),
            }
        else:
            item['result'] = None
        items.append(item)
    paused = (reader.directory / 'tasks/.paused').exists()
    return {'rva': rva, 'paused': paused, 'progress': _source_progress(jobs, rva, paused),
            'current_sha256': current_sha, 'items': items}
