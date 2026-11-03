"""Agent 管理器 - 管理 Codex sessions 和 runs。

本模块是 agents 库的核心，提供：

## 核心类

- AgentManager  - 管理所有 sessions（基于 SQLite）
- AgentSession  - 一个持久的 Codex 会话
- AgentRun      - 一次具体的执行

## 主要函数

- run_blocking(root, directory, cwd, prompt, timeout)  - 顺序执行（阻塞）
- run_async(root, directory, cwd, prompt, timeout)     - 并发执行（非阻塞）
- collect_blocking(directory)                          - 收集输出文件

## 使用示例

```python
from agents import run_blocking, run_async

# 顺序执行（阻塞到完成）
result = run_blocking(
    root=agents_root,
    directory=output_dir,
    cwd=workspace,
    prompt=task_prompt
)

# 并发执行（立即返回）
run = run_async(root, dir, cwd=workspace, prompt=task_prompt)
# ... 做其他事情 ...
result = run.wait()  # 等待完成
```

## 持久化

所有状态保存在 SQLite 数据库中：
- {root}/agents.db - sessions 和 runs 的状态
- {root}/sessions/{id}/ - 每个 session 的文件

## 后台 Worker

run_async() 会自动启动后台 worker 进程执行任务。
"""

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

    def _ensure_worker(self, db, session_id, *, pass_fds=()):
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
            for fd in pass_fds:
                command += ['--pass-fd', str(fd)]
            with (self._directory(session_id) / 'worker.log').open('ab') as log:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           start_new_session=True, pass_fds=(lease.fileno(), *pass_fds))
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

    def send_message(self, message, *, timeout=600, output_schema=None, request_id=None, delivery='next_turn',
                     _pass_fds=()):
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
            manager._ensure_worker(db, self.id, pass_fds=_pass_fds)
        return AgentRun(manager, self.id, run_id)

    # Explicit name for callers that want to emphasize queueing semantics.
    submit = send_message
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

    def iter_states(self, *, timeout=None, interval=0.2):
        """Yield changed durable states until this run reaches a terminal state.

        The iterator is the non-blocking lifecycle abstraction for callers that
        need progress updates. It never starts or retries a run; it only reads
        the persisted state and ends with the terminal state.
        """
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError('Wait timeout must be nonnegative and finite')
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError('Poll interval must be positive and finite')
        deadline = time.monotonic() + timeout if timeout is not None else None
        previous = None
        while True:
            value = self.poll()
            state = tuple(value.get(key) for key in ('status', 'started_at', 'finished_at', 'error', 'exit_code'))
            if state != previous:
                previous = state
                yield value
            if value['terminal']:
                return
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Waiting ended; the agent run has not been cancelled')
            time.sleep(interval)

    def wait(self, timeout=None, interval=0.2, *, on_update=None):
        """Wait for completion and return the final result.

        ``send_message``/``submit`` remains non-blocking. Use this method when
        the caller owns a synchronous boundary; use ``iter_states`` or
        ``poll`` when it owns an event loop. ``on_update`` receives each
        changed persisted state, including the terminal state.
        """
        final = None
        for value in self.iter_states(timeout=timeout, interval=interval):
            final = value
            if on_update is not None:
                on_update(value)
        return self.result() if final is not None else self.result()

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


# Blocking execution helpers (formerly in turns.py)

def _execution_record(record, directory):
    """Keep existing business execution records readable during the transition."""
    result = directory / 'result.md'
    if not result.is_file():
        result = directory / 'result.txt'
    result_exists = result.is_file()

    # 兼容 rehost/loop.py 的格式要求
    return {
        **record,
        'agent_status': record['status'],
        'status': 'exited' if record['status'] == 'completed' else record['status'],
        'exit_code': 0 if record['status'] == 'completed' else (record.get('exit_code') or 1),
        'result': {
            'path': str(result),
            'exists': result_exists,
            'bytes': result.stat().st_size if result_exists else 0
        }
    }


def run_blocking(root, directory, *, cwd, prompt, timeout=600, cancelled=lambda: False,
                 pass_fds=(), output_schema=None, **session_options):
    """Execute Codex in foreground (blocking). Returns when complete.

    Formerly named run_turn. Use for sequential tasks that need to wait for completion.
    For concurrent tasks, use AgentManager.create() + session.send_message() + run.poll().

    Args:
        root: Agents root directory
        directory: Output directory for results
        cwd: Working directory for execution
        prompt: Prompt to send to Codex
        timeout: Timeout in seconds
        cancelled: Callable that returns True if cancellation requested
        pass_fds: File descriptors to pass
        output_schema: Optional output schema path
        **session_options: Additional session options (model, profile, etc.)

    Returns:
        Execution record dict with status, exit_code, etc.
    """
    directory = Path(directory)
    if 'sandbox' not in session_options and os.environ.get('LIGHTRE_HARNESS_SANDBOX'):
        session_options['sandbox'] = os.environ['LIGHTRE_HARNESS_SANDBOX']
    session = AgentManager(root).create(cwd, persistent=False, **session_options)
    run = session.send_message(prompt, timeout=timeout, output_schema=output_schema, _pass_fds=pass_fds)
    _json(directory / 'agent.json', {'root': str(run.manager.root),
                                    'session_id': session.id, 'run_id': run.id})
    try:
        deadline = time.monotonic() + timeout
        while True:
            state = run.poll()
            raw_path = run.directory / 'execution.json'
            if raw_path.is_file():
                try:
                    state = {**json.loads(raw_path.read_text(encoding='utf-8')), **state}
                except (OSError, ValueError):
                    pass
            _json(directory / 'execution.json', _execution_record(state, directory))
            if state['terminal']:
                break
            if cancelled():
                run.cancel()
            elif time.monotonic() >= deadline:
                run.cancel()
            time.sleep(0.05)
    finally:
        collect_blocking(directory)
    return json.loads((directory / 'execution.json').read_text(encoding='utf-8'))


def collect_blocking(directory):
    """Copy outputs from agent run directory to business directory.

    Agent outputs are stored in agents/sessions/.../runs/xxx/.
    Business code expects them in their own directory (e.g., tasks/round-0001/).
    This function copies the agent event/log files and normalizes the Codex
    ``result.txt`` output to the business-facing ``result.md`` name.

    Args:
        directory: Business output directory containing agent.json

    Returns:
        Execution record dict
    """
    directory = Path(directory)
    reference = json.loads((directory / 'agent.json').read_text(encoding='utf-8'))
    run = AgentManager(reference['root']).get(reference['session_id']).get_run(reference['run_id'])
    state = run.poll()
    if not state['terminal']:
        raise RuntimeError('Execution has not finished')
    run_dir = Path(run.directory)
    for name in ('events.jsonl', 'result.json', 'stderr.log'):
        source = run_dir / name
        if source.exists():
            shutil.copy2(source, directory / name)
    result = run_dir / 'result.txt'
    if not result.exists():
        result = run_dir / 'result.md'
    if result.exists():
        shutil.copy2(result, directory / 'result.md')
    execution = {}
    saved_execution = run_dir / 'execution.json'
    if saved_execution.exists():
        try:
            execution.update(json.loads(saved_execution.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
    execution.update(state)
    execution = _execution_record(execution, directory)
    _json(directory / 'execution.json', execution)
    return execution


def run_async(root, directory, *, cwd, prompt, timeout=600, **session_options):
    """Execute Codex asynchronously (non-blocking). Returns immediately.

    Use for concurrent tasks. For sequential tasks, use run_blocking().

    Args:
        root: Agents root directory
        directory: Output directory for results
        cwd: Working directory for execution
        prompt: Prompt to send to Codex
        timeout: Timeout in seconds
        **session_options: Additional session options (model, profile, etc.)

    Returns:
        AgentRun object (call .wait() to block until completion)

    Example:
        # Launch multiple runs
        runs = []
        for task in tasks:
            run = run_async(root, task_dir, cwd=..., prompt=task_prompt)
            runs.append(run)

        # Wait for all
        results = [run.wait() for run in runs]
    """
    directory = Path(directory)
    session = AgentManager(root).create(cwd, persistent=False, **session_options)

    # Start background execution (non-blocking)
    run = session.send_message(prompt, timeout=timeout)

    # Save reference for later collection
    _json(directory / 'agent.json', {'root': str(run.manager.root),
                                    'session_id': session.id, 'run_id': run.id})
    return run
