"""One detached owner drains one session. Other processes only submit/query IDs."""

import argparse
import json
import os
import signal
import time

from .codex import CodexExec, process_identity, read_events, stop_group
from .manager import AgentManager, _json


def _execute(manager, session, row, lease_fd, stopped):
    request = json.loads(row['request'])
    directory = manager._run_directory(session['id'], row['id'])
    transport = CodexExec(session, request, directory)
    process, cursor, thread_id, event_error = None, 0, None, None
    turn_completed = False
    status, error = 'failed', None
    started = time.monotonic()
    execution = {'session_id': session['id'], 'run_id': row['id'], 'command': transport.command(),
                 'cwd': session['cwd'], 'started_at': time.time(), 'status': 'starting'}

    def consume():
        nonlocal cursor, thread_id, event_error, turn_completed
        while True:
            page = read_events(directory / 'events.jsonl', cursor)
            previous, cursor = cursor, page['cursor']
            for event in page['events']:
                if event.get('type') == 'thread.started' and isinstance(event.get('thread_id'), str):
                    found = event['thread_id']
                    if not found.strip():
                        continue
                    if session['thread_id'] and session['thread_id'] != found:
                        event_error = 'Codex returned a different thread ID when resuming'
                        continue
                    thread_id = found
                    with manager._db() as db:
                        db.execute('UPDATE runs SET thread_id=? WHERE id=?', (found, row['id']))
                        if session['options']['persistent']:
                            db.execute('UPDATE sessions SET thread_id=? WHERE id=?', (found, session['id']))
                elif event.get('type') == 'turn.failed':
                    event_error = 'Codex turn failed: ' + json.dumps(event.get('error'), ensure_ascii=False)
                elif event.get('type') == 'turn.completed':
                    turn_completed = True
            if cursor == previous or len(page['events']) + len(page['errors']) < 100:
                break

    try:
        _json(directory / 'execution.json', execution)
        with manager._db() as db:
            cancelled = db.execute('SELECT cancel_requested FROM runs WHERE id=?', (row['id'],)).fetchone()[0]
        if cancelled or stopped():
            status, error = 'cancelled' if cancelled else 'interrupted', 'Execution stopped before launch'
        else:
            process = transport.start(lease_fd)
            execution.update(pid=process.pid, process_identity=process_identity(process.pid), status='running')
            with manager._db() as db:
                db.execute('UPDATE runs SET cli_pid=?,cli_identity=? WHERE id=?',
                           (process.pid, execution['process_identity'], row['id']))
            _json(directory / 'execution.json', execution)
            while True:
                consume()
                code = process.poll()
                with manager._db() as db:
                    cancelled = db.execute('SELECT cancel_requested FROM runs WHERE id=?', (row['id'],)).fetchone()[0]
                if cancelled:
                    status, error = 'cancelled', 'Cancellation requested'
                    break
                if stopped():
                    status, error = 'interrupted', 'Executor received a stop signal'
                    break
                if code is not None:
                    consume()
                    error = event_error or transport.check_result(code, thread_id, turn_completed)
                    status = 'failed' if error else 'completed'
                    break
                if time.monotonic() - started >= request['timeout']:
                    status, error = 'timeout', f'Execution exceeded {request["timeout"]} seconds'
                    break
                time.sleep(0.05)
    except (OSError, ValueError) as problem:
        status, error = 'failed', f'Cannot execute/read Codex: {problem}'
    finally:
        if process is not None:
            # Stop descendants even when the CLI itself has exited successfully.
            stop_group(process.pid)
            process.wait()
            consume()
        execution.update(status=status, error=error, exit_code=process.returncode if process else None,
                         thread_id=thread_id, finished_at=time.time(), duration=time.monotonic() - started)
        _json(directory / 'execution.json', execution)
    with manager._db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE runs SET status=?,error=?,exit_code=?,finished_at=? WHERE id=?',
                   (status, error, execution['exit_code'], execution['finished_at'], row['id']))
        if status != 'completed':
            db.execute('UPDATE sessions SET paused=1,pause_reason=? WHERE id=?', (error, session['id']))


def serve(manager, session_id, lease_fd):
    stopped = False

    def stop(signum, frame):
        nonlocal stopped
        stopped = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    try:
        lease_path = manager._directory(session_id) / '.worker.lock'
        assert os.fstat(lease_fd).st_ino == lease_path.stat().st_ino
        while True:
            with manager._db() as db:
                db.execute('BEGIN IMMEDIATE')
                session = manager._session(db, session_id)
                row = db.execute("SELECT * FROM runs WHERE session_id=? AND status='queued' ORDER BY seq LIMIT 1",
                                 (session_id,)).fetchone()
                if stopped or session['paused'] or row is None:
                    db.execute('UPDATE sessions SET worker_pid=NULL,worker_identity=NULL WHERE id=?', (session_id,))
                    # Serialize release with submit: a sender either sees our
                    # queued work or acquires the lease to start a new owner.
                    os.close(lease_fd)
                    lease_fd = None
                    return
                db.execute("UPDATE runs SET status='running',started_at=? WHERE id=?", (time.time(), row['id']))
            _execute(manager, session, row, lease_fd, lambda: stopped)
    except BaseException as error:
        with manager._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE runs SET status='interrupted',error=?,finished_at=? WHERE session_id=? AND status='running'",
                       (f'Executor interrupted: {error}', time.time(), session_id))
            db.execute('UPDATE sessions SET paused=1,pause_reason=?,worker_pid=NULL,worker_identity=NULL WHERE id=?',
                       (str(error), session_id))
        raise
    finally:
        if lease_fd is not None:
            os.close(lease_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--lease-fd', type=int, required=True)
    args = parser.parse_args()
    serve(AgentManager(args.root), args.session, args.lease_fd)


if __name__ == '__main__':
    main()
