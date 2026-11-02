"""File-backed outputs and a small SQLite registry shared by Python and CLI clients."""

import fcntl
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .codex import process_identity, read_events, stop_group

TERMINAL = frozenset(('completed', 'failed', 'cancelled', 'timeout', 'interrupted'))

SCHEMA = '''
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, cwd TEXT NOT NULL, options TEXT NOT NULL,
    created_at REAL NOT NULL, thread_id TEXT UNIQUE,
    paused INTEGER NOT NULL DEFAULT 0, pause_reason TEXT,
    worker_pid INTEGER, worker_identity TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    request TEXT NOT NULL, request_id TEXT,
    status TEXT NOT NULL, created_at REAL NOT NULL,
    started_at REAL, finished_at REAL, exit_code INTEGER, error TEXT,
    cli_pid INTEGER, cli_identity TEXT, thread_id TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    UNIQUE(session_id, request_id)
);
CREATE INDEX IF NOT EXISTS runs_by_session ON runs(session_id, seq);
'''


def _json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


class AgentManager:
    """Handles are re-created by ID; this object owns no running subprocess."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.root / 'agents.sqlite', timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _directory(self, session_id):
        return self.root / 'sessions' / session_id

    def _run_directory(self, session_id, run_id):
        return self._directory(session_id) / 'runs' / run_id

    def create(self, cwd, *, executable=None, model=None, profile=None, persistent=True,
               sandbox=None, disabled_features=None, overrides=None):
        cwd = Path(cwd).resolve()
        if not cwd.is_dir():
            raise ValueError(f'Working directory does not exist: {cwd}')
        if any(value is not None and (not isinstance(value, str) or not value) for value in (model, profile)):
            raise ValueError('model and profile must be nonempty strings')
        executable = shutil.which(str(executable or os.environ.get('LIGHTRE_CODEX_BIN', 'codex')))
        if not executable:
            raise ValueError('Codex executable not found')
        sandbox = sandbox or os.environ.get('LIGHTRE_CODEX_SANDBOX', 'danger-full-access')
        if sandbox not in ('read-only', 'workspace-write', 'danger-full-access'):
            raise ValueError('Unsupported sandbox')
        overrides = dict(overrides or {})
        if any(not isinstance(key, str) or not key or not isinstance(value, (str, int, float, bool))
               or isinstance(value, float) and not math.isfinite(value) for key, value in overrides.items()):
            raise ValueError('Config overrides must map nonempty string keys to scalar TOML values')
        features = list(disabled_features if disabled_features is not None else ('plugins', 'apps', 'multi_agent'))
        if not all(isinstance(feature, str) and feature for feature in features):
            raise ValueError('Feature names must be nonempty strings')
        options = {'executable': str(Path(executable).resolve()), 'model': model,
                   'profile': profile or os.environ.get('LIGHTRE_CODEX_PROFILE'),
                   'persistent': bool(persistent), 'sandbox': sandbox,
                   'disabled_features': features, 'overrides': overrides}
        session_id = uuid4().hex
        self._directory(session_id).mkdir(parents=True)
        with self._db() as db:
            db.execute('INSERT INTO sessions(id,cwd,options,created_at) VALUES (?,?,?,?)',
                       (session_id, str(cwd), json.dumps(options), time.time()))
        return AgentSession(self, session_id)

    def get(self, session_id):
        """Resolve our stable session ID, or an already registered Codex thread ID."""
        with self._db() as db:
            row = db.execute('SELECT id FROM sessions WHERE id=? OR thread_id=?',
                             (session_id, session_id)).fetchone()
        if row is None:
            raise KeyError(f'Unknown agent session: {session_id}')
        return AgentSession(self, row['id'])

    def get_run(self, run_id):
        with self._db() as db:
            row = db.execute('SELECT session_id FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise KeyError(f'Unknown agent run: {run_id}')
        return AgentRun(self, row['session_id'], run_id)

    def list(self):
        with self._db() as db:
            ids = [row['id'] for row in db.execute('SELECT id FROM sessions ORDER BY created_at,id')]
        return [self.get(session_id).snapshot() for session_id in ids]

    def _session(self, db, session_id):
        row = db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
        if row is None:
            raise KeyError(f'Unknown agent session: {session_id}')
        value = dict(row)
        value['options'] = json.loads(value['options'])
        return value

    def _lease(self, session_id):
        lease = (self._directory(session_id) / '.worker.lock').open('a+')
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lease.close()
            return None
        return lease

    def _reconcile(self, db, session_id):
        """Never infer completion or replay a write from a dead worker's PID."""
        lease = self._lease(session_id)
        if lease is None:
            return
        try:
            stale = db.execute("SELECT id FROM runs WHERE session_id=? AND status='running'",
                               (session_id,)).fetchall()
            if stale:
                reason = 'Executor ended without a completion record; inspect outputs before resuming queued messages'
                db.execute("UPDATE runs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'interrupted' END,error=?,finished_at=? "
                           "WHERE session_id=? AND status='running'", (reason, time.time(), session_id))
                db.execute('UPDATE sessions SET paused=1,pause_reason=? WHERE id=?', (reason, session_id))
            db.execute('UPDATE sessions SET worker_pid=NULL,worker_identity=NULL WHERE id=?', (session_id,))
        finally:
            lease.close()

    def _ensure_worker(self, db, session_id):
        session = self._session(db, session_id)
        if session['paused'] or not db.execute("SELECT 1 FROM runs WHERE session_id=? AND status='queued'",
                                               (session_id,)).fetchone():
            return
        lease = self._lease(session_id)
        if lease is None:
            return
        try:
            command = [sys.executable, '-m', 'agents.worker', '--root', str(self.root),
                       '--session', session_id, '--lease-fd', str(lease.fileno())]
            with (self._directory(session_id) / 'worker.log').open('ab') as log:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           start_new_session=True, pass_fds=(lease.fileno(),))
            db.execute('UPDATE sessions SET worker_pid=?,worker_identity=? WHERE id=?',
                       (process.pid, process_identity(process.pid), session_id))
            threading.Thread(target=process.wait, daemon=True).start()
        except OSError as error:
            db.execute('UPDATE sessions SET paused=1,pause_reason=? WHERE id=?',
                       (f'Cannot start executor: {error}', session_id))
        finally:
            # flock ownership is shared with the child; do not explicitly unlock.
            lease.close()


class AgentSession:
    def __init__(self, manager, session_id):
        self.manager, self.id = manager, session_id

    def send_message(self, message, *, timeout=600, output_schema=None, request_id=None, delivery='next_turn'):
        """Persist a message as one queued run. Reusing a request ID is idempotent."""
        if delivery != 'next_turn':
            raise NotImplementedError('codex exec supports queued next_turn messages; live steering requires another transport')
        if not isinstance(message, str) or not message.strip():
            raise ValueError('Message must be a nonempty string')
        if not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Execution timeout must be a positive finite number')
        if output_schema is not None and not isinstance(output_schema, dict):
            raise ValueError('output_schema must be a JSON Schema object')
        if request_id is not None and (not isinstance(request_id, str) or not request_id):
            raise ValueError('request_id must be a nonempty string')
        request = {'message': message, 'timeout': float(timeout), 'output_schema': output_schema}
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, allow_nan=False)
        manager = self.manager
        with manager._db() as db:
            db.execute('BEGIN IMMEDIATE')
            session = manager._session(db, self.id)
            manager._reconcile(db, self.id)
            old = (db.execute('SELECT id,request FROM runs WHERE session_id=? AND request_id=?',
                              (self.id, request_id)).fetchone() if request_id is not None else None)
            if old:
                if old['request'] != encoded:
                    raise ValueError('request_id already belongs to a different message or execution options')
                run_id = old['id']
            else:
                run_id = uuid4().hex
                directory = manager._run_directory(self.id, run_id)
                directory.mkdir(parents=True)
                (directory / 'prompt.md').write_text(message, encoding='utf-8')
                _json(directory / 'request.json', {'session_id': self.id, 'run_id': run_id,
                                                  'cwd': session['cwd'], 'options': session['options'], **request})
                if output_schema is not None:
                    _json(directory / 'schema.json', output_schema)
                db.execute('INSERT INTO runs(id,session_id,request,request_id,status,created_at) '
                           "VALUES (?,?,?,?,'queued',?)", (run_id, self.id, encoded, request_id, time.time()))
            manager._ensure_worker(db, self.id)
        return AgentRun(manager, self.id, run_id)

    start = send_message

    def run(self, message, *, wait_timeout=None, **options):
        return self.send_message(message, **options).wait(wait_timeout)

    def get_run(self, run_id=None):
        """Without an ID, return the active run, or the most recently submitted one."""
        with self.manager._db() as db:
            self.manager._session(db, self.id)
            if run_id is None:
                row = db.execute("SELECT id FROM runs WHERE session_id=? ORDER BY status='running' DESC,seq DESC LIMIT 1",
                                 (self.id,)).fetchone()
            else:
                row = db.execute('SELECT id FROM runs WHERE session_id=? AND id=?', (self.id, run_id)).fetchone()
        if row is None:
            raise KeyError('No matching agent run')
        return AgentRun(self.manager, self.id, row['id'])

    def snapshot(self):
        manager = self.manager
        with manager._db() as db:
            db.execute('BEGIN IMMEDIATE')
            manager._session(db, self.id)
            manager._reconcile(db, self.id)
            session = manager._session(db, self.id)
            rows = [dict(row) for row in db.execute(
                'SELECT id,status,created_at,started_at,finished_at,error FROM runs WHERE session_id=? ORDER BY seq',
                (self.id,))]
        active = next((row['id'] for row in rows if row['status'] == 'running'), None)
        queued = sum(row['status'] == 'queued' for row in rows)
        worker_alive = (session['worker_pid'] is not None and session['worker_identity'] is not None
                        and process_identity(session['worker_pid']) == session['worker_identity'])
        session.update(status='running' if active else 'paused' if session['paused'] else 'queued' if queued else 'idle',
                       manager_root=str(manager.root),
                       paused=bool(session['paused']),
                       active_run_id=active, latest_run_id=rows[-1]['id'] if rows else None,
                       queued=queued, runs=rows, worker_alive=worker_alive,
                       capabilities={'live_messages': False, 'next_turn_messages': True,
                                     'conversation_context': session['options']['persistent']})
        return session

    def resume(self):
        """Explicitly release a pause; never retry a terminal/interrupted run."""
        with self.manager._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.manager._session(db, self.id)
            self.manager._reconcile(db, self.id)
            db.execute('UPDATE sessions SET paused=0,pause_reason=NULL WHERE id=?', (self.id,))
            self.manager._ensure_worker(db, self.id)
        return self

    def pause(self):
        """Prevent claiming another queued message; leave the active run alone."""
        with self.manager._db() as db:
            db.execute('UPDATE sessions SET paused=1,pause_reason=? WHERE id=?', ('Paused by caller', self.id))
        return self

    def cancel(self):
        """Cancel queued messages and request cancellation of the active run."""
        with self.manager._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.manager._session(db, self.id)
            db.execute('UPDATE sessions SET paused=1,pause_reason=? WHERE id=?', ('Session cancellation requested', self.id))
            db.execute("UPDATE runs SET status='cancelled',cancel_requested=1,finished_at=? "
                       "WHERE session_id=? AND status='queued'", (time.time(), self.id))
            db.execute("UPDATE runs SET cancel_requested=1 WHERE session_id=? AND status='running'", (self.id,))
            active = db.execute("SELECT id FROM runs WHERE session_id=? AND status='running'", (self.id,)).fetchone()
        if active:
            AgentRun(self.manager, self.id, active['id'])._stop_orphan()


class AgentRun:
    """A serializable reference, safe to reconstruct in another Python process."""

    def __init__(self, manager, session_id, run_id):
        self.manager, self.session_id, self.id = manager, session_id, run_id

    @property
    def directory(self):
        return self.manager._run_directory(self.session_id, self.id)

    def poll(self):
        with self.manager._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.manager._session(db, self.session_id)
            self.manager._reconcile(db, self.session_id)
            row = db.execute('SELECT * FROM runs WHERE id=? AND session_id=?', (self.id, self.session_id)).fetchone()
            if row is None:
                raise KeyError(f'Unknown agent run: {self.id}')
            value = dict(row)
        value.pop('request')
        value.update(directory=str(self.directory), terminal=value['status'] in TERMINAL,
                     cancel_requested=bool(value['cancel_requested']))
        return value

    snapshot = poll

    def read_events(self, cursor=0, limit=100):
        return read_events(self.directory / 'events.jsonl', cursor, limit)

    def result(self):
        value = self.poll()
        if not value['terminal']:
            raise RuntimeError('Run has not ended')
        path = self.directory / 'result.txt'
        value['text'] = path.read_text(encoding='utf-8', errors='replace') if path.exists() else None
        request = json.loads((self.directory / 'request.json').read_text())
        value['parsed'] = None
        if request['output_schema'] is not None and value['text']:
            try:
                value['parsed'] = json.loads(value['text'])
            except ValueError:
                pass  # Raw output and failure remain available for diagnosis.
        return value

    def wait(self, timeout=None, interval=0.1):
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError('Wait timeout must be nonnegative and finite')
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError('Poll interval must be positive and finite')
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            value = self.poll()
            if value['terminal']:
                return self.result()
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Waiting ended; the agent run has not been cancelled')
            time.sleep(interval)

    def cancel(self):
        with self.manager._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM runs WHERE id=? AND session_id=?', (self.id, self.session_id)).fetchone()
            if row is None:
                raise KeyError(f'Unknown agent run: {self.id}')
            if row['status'] == 'queued':
                db.execute("UPDATE runs SET status='cancelled',cancel_requested=1,finished_at=? WHERE id=?",
                           (time.time(), self.id))
            elif row['status'] == 'running':
                db.execute('UPDATE runs SET cancel_requested=1 WHERE id=?', (self.id,))
        self._stop_orphan()

    def _stop_orphan(self):
        """A dead worker cannot consume cancellation; stop only a proven CLI owner."""
        with self.manager._db() as db:
            session = self.manager._session(db, self.session_id)
            row = db.execute('SELECT * FROM runs WHERE id=?', (self.id,)).fetchone()
        alive = (session['worker_pid'] is not None and session['worker_identity'] is not None
                 and process_identity(session['worker_pid']) == session['worker_identity'])
        if (not alive and row['status'] == 'running' and row['cancel_requested'] and row['cli_pid']
                and row['cli_identity'] and process_identity(row['cli_pid']) == row['cli_identity']):
            stop_group(row['cli_pid'])
