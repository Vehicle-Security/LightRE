"""运行时基础设施：文件记录、harness 关联、写锁，以及带日志的子进程执行。"""

import fcntl
import hashlib
import json
import os
import signal
import subprocess
import time
from contextlib import contextmanager, ExitStack
from pathlib import Path
from uuid import uuid4

from lightre.workspace import PROJECT_DIR


class WriterBusy(RuntimeError):
    """Another task still owns the workspace or harness writer lock."""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def identifier():
    return time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + '-' + uuid4().hex[:8]


def association(directory):
    """读取 SO 分析目录的 harness 关联（harness.json），返回工程路径和参数。"""
    directory = Path(directory).resolve()
    if directory.is_relative_to(PROJECT_DIR):
        raise ValueError('Run outputs must be outside the LightRE repository')
    config = read_json(directory / 'harness.json')
    harness = (directory / config['path']).resolve()
    if harness.is_relative_to(PROJECT_DIR):
        raise ValueError('Use an editable harness outside the LightRE repository')
    return harness, config.get('parameters', {})


def inventory(root):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Symlinks are not supported in a harness snapshot: {path}')
        if path.is_file():
            result[str(path.relative_to(root))] = digest(path)
    return result


@contextmanager
def exclusive(directory, harness):
    """Share the v0.2 SO writer lock; also exclude other SOs using this harness."""
    paths = {Path(harness) / '.lightre.lock'}
    if directory is not None:
        directory = Path(directory)
        (directory / 'tasks').mkdir(exist_ok=True)
        paths.add(directory / 'tasks/.lock')
    with ExitStack() as stack:
        handles = []
        for path in sorted(paths):
            handle = stack.enter_context(path.open('a+'))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WriterBusy(f'Another writer is active: {path}') from None
            handles.append(handle)
        yield tuple(handle.fileno() for handle in handles)


# ============================================================================
# 子进程执行：日志、超时、取消、进程组清理
# ============================================================================

def _stop_group(pid):
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    # A CLI can exit while its shell/compiler is still writing. Kill remaining
    # group members before snapshots are inspected or another writer starts.
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def execute(command, cwd, directory, *, timeout=120, cancelled=lambda: False,
            stdin=None, stdout='stdout.log', stderr='stderr.log', pass_fds=()):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    record = {'command': [str(arg) for arg in command], 'cwd': str(cwd),
              'started_at': time.time(), 'exit_code': None, 'status': 'starting',
              'stdout': stdout, 'stderr': stderr}
    process = None
    started = time.monotonic()
    try:
        with (directory / stdout).open('wb') as out, (directory / stderr).open('wb') as err:
            if cancelled():
                record['status'] = 'cancelled'
            else:
                process = subprocess.Popen(record['command'], cwd=cwd,
                                           stdin=stdin if stdin is not None else subprocess.DEVNULL,
                                           stdout=out, stderr=err, start_new_session=True,
                                           pass_fds=pass_fds)
                record.update(status='running', pid=process.pid)
                write_json(directory / 'execution.json', record)
                while process.poll() is None:
                    if cancelled():
                        record['status'] = 'cancelled'
                        break
                    if time.monotonic() - started >= timeout:
                        record['status'] = 'timeout'
                        break
                    time.sleep(0.05)
                else:
                    record['status'] = 'exited'
    except OSError as error:
        record.update(status='error', error=str(error))
    except BaseException:
        record['status'] = 'interrupted'
        raise
    finally:
        if process is not None:
            _stop_group(process.pid)
            record['exit_code'] = process.wait()
        record['finished_at'] = time.time()
        record['duration'] = time.monotonic() - started
        write_json(directory / 'execution.json', record)
    return record


def succeeded(record):
    return record['status'] == 'exited' and record['exit_code'] == 0
