"""Logged subprocesses with deadlines, cancellation and process-group cleanup."""

import os
import signal
import subprocess
import time
from pathlib import Path


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
    from .storage import write_json
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
