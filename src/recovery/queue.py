"""A durable per-SO request queue, using task files and two local file locks."""

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .candidates import candidate_rvas, read_candidate

ACTIVE = ("queued", "running")
FINISHED = ('complete', 'partial', 'error', 'cancelled')


def write_state(path, state):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


@contextmanager
def locked(reader):
    root = reader.directory / 'tasks'
    root.mkdir(exist_ok=True)
    with (root / '.queue.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield root


def snapshot(reader):
    root = reader.directory / 'tasks'
    dismissed = _dismissed(root)
    jobs = {}
    for path in [root / 'state.json', *root.glob('*/state.json')]:
        try:
            state = json.loads(path.read_text())
            created_at = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if state.get('mode') == 'harness':
            # Native harness tasks have their own foreground executor. Sharing
            # the SO lock must not enqueue them as v0.2 candidate batches.
            continue
        state.setdefault('directory', str(root / state['id']))
        state.setdefault('priority', 0 if state.get('mode') == 'comment' else 10)
        if 'created_at' not in state:
            try:
                created_at = datetime.strptime(state['id'][:15], '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                pass
            state['created_at'] = created_at
        if (Path(state['directory']) / 'cancel').exists() and state['status'] in ('queued', 'interrupted'):
            state['status'] = 'cancelled'
        state['dismissed'] = state['id'] in dismissed and state['status'] in FINISHED
        jobs[state['id']] = state
    return sorted(jobs.values(), key=lambda job: (job['priority'], job['created_at'], job['id']))


def _dismissed(root):
    try:
        return set(json.loads((root / '.dismissed.json').read_text())['ids'])
    except FileNotFoundError:
        return set()


def clear_finished(reader, comment_rva=None):
    """Clear finished task/reply displays; keep durable evidence and knowledge."""
    if comment_rva is not None and comment_rva not in {fn['rva'] for fn in reader.catalog()['functions']}:
        raise KeyError(f'未识别的函数 RVA：{comment_rva:#x}')
    with locked(reader) as root:
        selected = [job['id'] for job in snapshot(reader)
                    if job['status'] in FINISHED and not job['dismissed']
                    and (comment_rva is None or job.get('mode') == 'comment' and comment_rva in job['rvas'])]
        if selected:
            write_state(root / '.dismissed.json', {
                'ids': sorted(_dismissed(root) | set(selected)), 'updated_at': time.time()})
    return {'cleared': len(selected)}


def wait_reason(jobs, selected):
    rvas = set(selected['rvas'])
    for job in jobs:
        if job['id'] == selected['id']:
            break
        if job['status'] == 'queued' and rvas & (set(job['rvas']) - set(job.get('done_rvas', []))):
            return 'same_function'
    for job in jobs:
        if job['status'] == 'running' and rvas & {int(value, 0) for value in job.get('current', [])}:
            return 'same_function'
    used = sum(job.get('active_batches', 0) for job in jobs if job['status'] == 'running')
    return 'capacity' if used >= max(1, int(os.environ.get('LIGHTRE_CODEX_WORKERS', '8'))) else 'dispatch'


def _ensure_worker(reader):
    """Call under .queue.lock, including the worker's idle/exit handshake."""
    root = reader.directory / 'tasks'
    if (root / '.paused').exists() or not any(job['status'] in ACTIVE for job in snapshot(reader)):
        return
    lock = (root / '.dispatcher.lock').open('a+')
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        with (root / 'worker.log').open('ab') as log:
            subprocess.Popen(
                [sys.executable, '-m', 'recovery._worker', str(reader.directory), str(lock.fileno())],
                cwd=reader.directory,
                pass_fds=(lock.fileno(),), stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
    finally:
        lock.close()


def ensure_worker(reader):
    if not any(job['status'] in ACTIVE for job in snapshot(reader)):
        return
    with locked(reader):
        _ensure_worker(reader)


def submit(reader, rvas=None, comment='', expected_sha256=None, workers=None, parent_task=None,
           input_mode='reference'):
    if input_mode not in ('reference', 'assembly'):
        raise ValueError('input_mode 必须为 reference 或 assembly')
    comment = comment.strip()
    with locked(reader) as root:
        jobs = snapshot(reader)
        mode = 'comment' if comment else 'targeted' if rvas is not None else 'initial'
        if mode == 'initial':
            existing = next((job for job in jobs if job['status'] in ACTIVE and
                             job.get('mode', 'initial') == 'initial' and
                             job.get('input_mode', 'reference') == input_mode), None)
            if existing:
                _ensure_worker(reader)
                return existing
        functions = reader.catalog()['functions']
        known = {fn['rva'] for fn in functions}
        selected = set(rvas) if rvas is not None else known - candidate_rvas(reader, optimized_only=True)
        if not selected <= known:
            raise ValueError('包含未识别的函数 RVA')
        if not selected:
            return {'status': 'complete', 'total': 0, 'message': '所有函数已有 LLM 候选'}
        for job in jobs:
            if job['status'] in ACTIVE and job.get('mode') == mode and set(job['rvas']) == selected and \
                    job.get('comment', '') == comment and job.get('expected_sha256') == expected_sha256 and \
                    job.get('input_mode', 'reference') == input_mode:
                _ensure_worker(reader)
                return job
        candidate = None
        if comment:
            if len(selected) != 1:
                raise ValueError('每条评论只能对应一个函数')
            candidate = read_candidate(reader, next(iter(selected)))
            if candidate['sha256'] != expected_sha256:
                raise ValueError('候选已更新，请刷新代码后再提交评论')
        executable = shutil.which(os.environ.get('LIGHTRE_CODEX_BIN', 'codex'))
        if not executable:
            raise RuntimeError('找不到 codex，请先安装并登录本机 Codex CLI')
        workers = int(workers if workers is not None else os.environ.get('LIGHTRE_CODEX_WORKERS', '8'))
        if workers < 1:
            raise ValueError('Codex 并发数必须大于零')
        identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8]
        directory = root / identifier
        directory.mkdir()
        task = directory / 'task.md'
        task.write_text('# LightRE 函数还原任务\n\n'
                        f'SO 结果目录：{reader.directory}\n\n函数 RVA：'
                        + ', '.join(f'{rva:#x}' for rva in sorted(selected))
                        + f'\n\n输入模式：{input_mode}（assembly 仅使用汇编与原 ELF，不读取 Ghidra 伪代码）。'
                        + '\n\n## 用户意见\n\n' + (comment or '完成全部指定函数的一轮还原。')
                        + f'\n\n提交建议时的候选 SHA256：{expected_sha256 or "尚无候选"}\n'
                        + '\n执行时读取最新候选。若与提交时不同，结合 reviewed.c 理解评论，不回滚新结果。\n',
                        encoding='utf-8')
        if parent_task:
            with task.open('a', encoding='utf-8') as stream:
                stream.write(f'\n## 自动续做\n\n首轮任务 {parent_task} 未能给出这些函数的完整候选。'
                             '读取已保存的未解决原因，按需核对完整汇编与本地工具结果，补全有依据的代码。'
                             '不能用空函数、跳过副作用或照抄已知截断的初稿冒充还原；材料仍不足则记录具体缺口。\n')
        try:
            if candidate is not None:
                if candidate['code']:
                    (directory / 'reviewed.c').write_text(candidate['code'], encoding='utf-8')
            state = {'id': identifier, 'status': 'queued', 'pid': None, 'total': len(selected),
                     'processed': 0, 'generated': 0, 'unresolved': {}, 'done_rvas': [], 'current': [],
                     'created_at': time.time(), 'priority': 0 if comment else 10,
                     'workers': workers, 'active_batches': 0, 'rvas': sorted(selected),
                     'directory': str(directory), 'mode': mode, 'comment': comment,
                     'expected_sha256': expected_sha256, 'codex': executable, 'input_mode': input_mode}
            # V0.2 produces one conservative Ghidra + LLM pass. Existing jobs
            # with auto_refine=true still retain their recorded continuation.
            state['auto_refine'] = False
            if parent_task:
                state['parent_task'] = parent_task
            write_state(directory / 'state.json', state)
        except BaseException:
            shutil.rmtree(directory)
            raise
        # A failed spawn leaves a durable queued request, available on resume.
        _ensure_worker(reader)
        return state


def has_priority(reader, state):
    return any(job['status'] == 'queued' and job['priority'] < state.get('priority', 10)
               for job in snapshot(reader))


def _signal(reader, state):
    pid = state.get('pid')
    if not pid:
        return
    try:
        args = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        if args[1:4] != [b'-m', b'recovery._worker', str(reader.directory).encode()]:
            raise RuntimeError('任务进程身份不匹配')
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, FileNotFoundError):
        pass


def pause(reader):
    with locked(reader) as root:
        (root / '.paused').touch()
        running = next((job for job in snapshot(reader) if job['status'] == 'running'), None)
        if running:
            _signal(reader, running)


def resume(reader):
    with locked(reader) as root:
        (root / '.paused').unlink(missing_ok=True)
        for job in snapshot(reader):
            if job['status'] == 'interrupted':
                job.update(status='queued', current=[], active_batches=0)
                _legacy_progress(reader, job)
                write_state(Path(job['directory']) / 'state.json', job)
        _ensure_worker(reader)


def cancel(reader, identifier):
    with locked(reader):
        job = next((job for job in snapshot(reader) if job['id'] == identifier), None)
        if job is None:
            raise KeyError('任务不存在')
        if job['status'] in (*ACTIVE, 'interrupted'):
            (Path(job['directory']) / 'cancel').touch()
            return {'id': identifier, 'status': 'cancelling' if job['status'] == 'running' else 'cancelled'}
        return {'id': identifier, 'status': job['status']}


def _legacy_progress(reader, state):
    if 'done_rvas' in state:
        return
    optimized = candidate_rvas(reader, optimized_only=True)
    published = optimized | {int(rva, 0) for rva in state.get('unresolved', {})}
    state['done_rvas'] = sorted(set(state['rvas']) & published)
    state['processed'] = len(state['done_rvas'])
    if state.get('mode') == 'initial':
        state['generated'] = len(set(state['rvas']) & optimized)
    state.setdefault('started_at', state['created_at'])


class BatchPool:
    """One dispatcher owns all slots and serializes publication and RVA leases."""

    def __init__(self, limit):
        self.limit = limit
        self.active = {}
        self.jobs = []

    def available(self, state, batch):
        if len(self.active) >= self.limit:
            return []
        blocked = {rva for rvas in self.active.values() for rva in rvas}
        for job in self.jobs:
            if job['id'] == state['id']:
                break
            if job['status'] in ACTIVE:
                blocked.update(set(job['rvas']) - set(job.get('done_rvas', [])))
        return [fn for fn in batch if fn['rva'] not in blocked]

    def acquire(self, identifier, number, batch):
        self.active[(identifier, number)] = {fn['rva'] for fn in batch}

    def release(self, identifier, number):
        self.active.pop((identifier, number), None)


def run_queue(reader, dispatcher_fd):
    from .jobs import task_steps
    root = reader.directory / 'tasks'
    # Older workers only own .lock. A new dispatcher can wait for one safely.
    with (root / '.lock').open('a+') as worker_lock:
        fcntl.flock(worker_lock, fcntl.LOCK_EX)
        with locked(reader):
            legacy = root / 'state.json'
            if legacy.exists():
                state = json.loads(legacy.read_text())
                directory = Path(state['directory'])
                if not (directory / 'state.json').exists():
                    state.setdefault('created_at', datetime.strptime(state['id'][:15], '%Y%m%dT%H%M%S')
                                     .replace(tzinfo=timezone.utc).timestamp())
                    if state['status'] in ACTIVE:
                        state['status'] = 'queued'
                    _legacy_progress(reader, state)
                    write_state(directory / 'state.json', state)
                legacy.unlink()
            # Holding .lock proves no older worker still owns these jobs.
            for job in snapshot(reader):
                if job['status'] == 'running':
                    job.update(status='queued', current=[], active_batches=0)
                    write_state(Path(job['directory']) / 'state.json', job)
        pool = BatchPool(max(1, int(os.environ.get('LIGHTRE_CODEX_WORKERS', '8'))))
        runners = {}
        stopping = False

        def stop(signum, frame):
            nonlocal stopping
            stopping = True

        previous = {signum: signal.signal(signum, stop) for signum in (signal.SIGTERM, signal.SIGINT)}
        try:
            while True:
                with locked(reader):
                    jobs = snapshot(reader)
                    pool.jobs = jobs
                    if stopping or (root / '.paused').exists() or (not runners and not any(
                            job['status'] == 'queued' for job in jobs)):
                        # Enqueue and idle exit share this lock. Keep dispatcher
                        # ownership until all active CLIs have been stopped.
                        for steps in runners.values():
                            steps.close()
                        runners.clear()
                        fcntl.flock(dispatcher_fd, fcntl.LOCK_UN)
                        return
                for job in jobs:
                    if stopping or (root / '.paused').exists():
                        break
                    identifier = job['id']
                    if identifier not in runners:
                        todo = [{'rva': rva} for rva in job['rvas'] if rva not in job.get('done_rvas', [])]
                        if job['status'] != 'queued' or (todo and not pool.available(job, todo)):
                            continue
                        runners[identifier] = task_steps(reader, identifier, pool)
                    try:
                        next(runners[identifier])
                    except StopIteration:
                        del runners[identifier]
                time.sleep(0.1)
        finally:
            for steps in runners.values():
                steps.close()
            for signum, handler in previous.items():
                signal.signal(signum, handler)
