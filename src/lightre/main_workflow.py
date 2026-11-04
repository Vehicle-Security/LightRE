"""Prepare persistent target workspaces and resume their analysis workflows."""

import fcntl
import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path

from analyzers.ghidra import analyze

from lightre.workspace import WorkspaceReader, prepare_workspace, find_target, arm64_libraries, explore_directory
from recovery.jobs import start_task
from recovery.queue import resume as resume_recovery, snapshot
from rehost.storage import WriterBusy, read_json, write_json
from rehost.workflow import continue_analysis as continue_rehost


def _finish_analysis(directory, on_progress=None):
    if on_progress:
        on_progress(phase='static_analysis', message='正在检查并导出 Ghidra 函数、汇编和参考伪代码',
                    directory=str(directory), log_path=str(directory / 'references/ghidra/headless.log'))
    original = directory / 'binary/original.so'
    # Always check the saved binary, even when resuming without the input SO.
    with sqlite3.connect(directory / 'index.sqlite') as conn:
        digest = conn.execute("SELECT value FROM metadata WHERE key='sha256'").fetchone()[0]
    if hashlib.sha256(original.read_bytes()).hexdigest() != digest:
        raise ValueError(f'工作区原始 SO 已发生变化：{original}')
    if not (directory / '.catalog-migration.json').exists():
        try:
            reader = WorkspaceReader(directory)
            state = directory / 'references/ghidra/state.json'
            if not state.exists() or read_json(state)['status'] == 'ready':
                return reader
        except ValueError:
            pass
    print(f'LightRE: 正在静态导出 {directory}；日志：{directory / "references/ghidra/headless.log"}',
          flush=True)
    return WorkspaceReader(analyze(original, directory.parent))


def prepare_analysis(target, *, workspace=None, on_progress=None):
    """Explore an input directory, bind its workspace, and reuse the selected SO."""
    target = Path(target).resolve()
    workspace_input = False
    if not target.exists():
        raise ValueError(f'目标文件或目录不存在：{target}')
    # Existing result paths remain usable for inspection and recovery.
    if target.is_dir() and workspace is None:
        if (target / 'index.sqlite').is_file():
            return _finish_analysis(target, on_progress)
        if (target / 'target.json').is_file():
            saved = read_json(target / 'target.json')
            workspace_input = True
            workspace = target
            target = (workspace / saved['source_dir']).resolve()
        else:
            results = sorted(target.glob('*/index.sqlite'))
            if results and not arm64_libraries(target):
                if len(results) != 1:
                    raise ValueError('workspace 包含多个分析结果，请指定目标分析目录：\n' +
                                     '\n'.join(str(path.parent) for path in results))
                return _finish_analysis(results[0].parent, on_progress)

    source = target.parent if target.is_file() else target
    workspace = Path(workspace).resolve() if workspace is not None else Path.cwd() / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    report = on_progress or (lambda **fields: None)
    report(phase='waiting', message='正在准备 workspace，等待启动锁', workspace=str(workspace))
    with (workspace / '.startup.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = workspace / 'target.json'
        saved = read_json(manifest) if manifest.exists() else None
        if saved and (workspace / saved['source_dir']).resolve() != source:
            raise ValueError(f'workspace 已关联另一目标目录：{workspace / saved["source_dir"]}')
        if target.is_file():
            selection = {'binary': target.name, 'dependencies': []}
        elif saved:
            selection = saved
        else:
            report(phase='exploring', message='正在扫描目标目录并选择 ARM64 SO')
            selection = explore_directory(source, workspace)
        binary = (source / selection['binary']).resolve()
        if not binary.is_relative_to(source):
            raise ValueError('保存的目标 SO 不在输入资料目录中')
        if binary.is_file():
            directory = prepare_workspace(binary, workspace, name=binary.stem)
        elif saved and saved.get('analysis'):
            directory = (workspace / saved['analysis']).resolve()
            if directory.parent != workspace:
                raise ValueError('保存的分析目录不属于当前 workspace')
        else:
            raise ValueError(f'保存的目标 SO 不存在：{binary}')
        # Save before export, so even a failed first export resumes the same result.
        write_json(manifest, {
            'source_dir': os.path.relpath(source, workspace),
            'binary': selection['binary'],
            'dependencies': selection.get('dependencies', []),
            'reason': selection.get('reason', ''),
            'analysis': directory.name,
        })
        print(f'LightRE: workspace：{workspace}；目标：{binary}', flush=True)
        return _finish_analysis(directory, on_progress)


def start_workflow(reader, rvas=None, *, workers=None, input_mode='reference', start_rehost_task=True):
    """Keep one coordinator and resume durable Recovery and Rehost tasks."""
    path = reader.directory / 'main-workflow.json'
    lock = (reader.directory / '.workflow.lock').open('a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return read_json(path) if path.exists() else {'status': 'starting'}
    try:
        state = read_json(path) if path.exists() else {'recovery': None, 'rehost': None}
        resume_recovery(reader, retry_errors=True)
        jobs = [job for job in snapshot(reader) if job.get('mode') == 'initial'
                and job.get('input_mode', 'reference') == input_mode]
        if rvas is None and jobs:
            state['recovery'] = max(jobs, key=lambda job: job['created_at'])
        else:
            state['recovery'] = start_task(reader, rvas, workers=workers, input_mode=input_mode)
        state['status'] = 'running'
        write_json(path, state)
        if not start_rehost_task:
            lock.close()
            return state

        _launch_rehost(reader, state, path, lock)
        return state
    except BaseException:
        lock.close()
        raise


def retry_rehost(reader):
    """Retry only Rehost, retaining its harness, contract and round history."""
    path = reader.directory / 'main-workflow.json'
    lock = (reader.directory / '.workflow.lock').open('a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = read_json(path) if path.exists() else {'recovery': None}
        _launch_rehost(reader, state, path, lock, retry=True)
        return {'status': 'accepted'}
    except BlockingIOError:
        lock.close()
        raise WriterBusy('Rehost 已在执行或等待 Recovery，无需重复提交') from None
    except BaseException:
        lock.close()
        raise


def _launch_rehost(reader, state, path, lock, *, retry=False):
    requested_at = time.time()
    state.update(status='running', rehost={'status': 'starting', 'requested_at': requested_at})
    write_json(path, state)

    def run():
        try:
            while True:
                # Recovery and Rehost share one SO writer lock.
                if any(job['status'] in ('queued', 'running') for job in snapshot(reader)):
                    state['rehost'] = {'status': 'waiting_for_recovery', 'requested_at': requested_at}
                    write_json(path, state)
                    time.sleep(0.5)
                    continue
                try:
                    manifest = reader.directory.parent / 'target.json'
                    materials = []
                    if manifest.exists():
                        source = (manifest.parent / read_json(manifest)['source_dir']).resolve()
                        if source.is_dir():
                            materials = [source]
                    state['rehost'] = {'status': 'running', 'requested_at': requested_at}
                    write_json(path, state)
                    options = {'retry': True} if retry else {}
                    state['rehost'] = continue_rehost(
                        reader.directory, materials=materials, max_rounds=None,
                        timeout=1800, **options,
                    )
                    break
                except WriterBusy:
                    state['rehost'] = {'status': 'waiting_for_writer', 'requested_at': requested_at}
                    write_json(path, state)
                    time.sleep(0.5)
        except Exception as error:
            state['rehost'] = {'status': 'error', 'error': str(error)}
        finally:
            try:
                identifier = (state.get('recovery') or {}).get('id')
                state['recovery'] = next((job for job in snapshot(reader) if job['id'] == identifier),
                                         state.get('recovery'))
                state['status'] = 'rehost_finished'
                write_json(path, state)
            finally:
                lock.close()

    threading.Thread(target=run, name='lightre-rehost', daemon=True).start()
