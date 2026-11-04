"""Deployment entry point for the LightRE service."""

import os
import argparse
import json
import fcntl
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import HTTPException

from .api import create_app
from .deps import ensure
from .main_workflow import prepare_analysis, start_workflow


class PendingWorkspace:
    """Let the UI serve files while its workspace is being prepared."""

    def __init__(self):
        self.reader = None
        self.error = None
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._phase_started = self._started
        self._progress = {'phase': 'starting', 'message': '正在启动后台分析'}

    def update(self, **fields):
        with self._lock:
            if fields.get('phase', self._progress['phase']) != self._progress['phase']:
                self._phase_started = time.monotonic()
            self._progress.update(fields)

    def status(self):
        with self._lock:
            result = {**self._progress, 'ready': self.reader is not None,
                      'status': 'error' if self.error else 'ready' if self.reader else 'preparing',
                      'elapsed_seconds': int(time.monotonic() - self._started),
                      'phase_elapsed_seconds': int(time.monotonic() - self._phase_started)}
        # Read only a bounded tail: Ghidra logs can grow very large.
        result['log_tail'] = ''
        if result.get('log_path'):
            try:
                with Path(result['log_path']).open('rb') as stream:
                    stream.seek(0, 2)
                    size = stream.tell()
                    stream.seek(max(0, size - 8192))
                    lines = stream.read(8192).decode('utf-8', errors='replace').splitlines()
                if size > 8192:
                    lines = lines[1:]
                result['log_tail'] = '\n'.join(lines[-12:])
            except FileNotFoundError:
                pass
            except OSError as error:
                result['log_tail'] = f'日志暂不可读：{error}'
        if self.reader is not None:
            from rehost.progress import workflow_status
            result['workflow'] = workflow_status(self.reader.directory)
        return result

    def __getattr__(self, name):
        reader = self.reader
        if reader is None:
            raise HTTPException(503, str(self.error) if self.error else '正在探索目标目录和准备静态分析，请稍候')
        return getattr(reader, name)


def main(argv=None):
    parser = argparse.ArgumentParser(description='LightRE service')
    parser.add_argument('target', type=Path, nargs='?', help='目标资料目录（也可指定 SO 或已有 workspace）')
    parser.add_argument('--prepare', action='store_true', help='自动安装并检查依赖后退出')
    parser.add_argument('--workspace', type=Path, help='指定分析结果目录，缺少时自动创建')
    parser.add_argument('--host', default=os.environ.get('LIGHTRE_HOST', '0.0.0.0'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('LIGHTRE_PORT', '8000')))
    args = parser.parse_args(argv)
    if args.target is None and not args.prepare:
        parser.error('请指定目标资料目录')
    # The default service starts the recovery workflow, which may need Ghidra
    # in addition to the runtime compiler/toolchain.
    dependencies = ensure('analysis')
    if args.prepare:
        print(json.dumps(dependencies, ensure_ascii=False, indent=2))
        return

    pending = PendingWorkspace()
    app = create_app(pending, start_worker=False, status=pending.status)
    app.state.analysis_finished = threading.Event()
    app.state.startup_error = None
    stopped = threading.Event()

    def background_analysis():
        service_lock = None
        try:
            reader = prepare_analysis(args.target, workspace=args.workspace, on_progress=pending.update)
            if stopped.is_set():
                return
            service_lock = (reader.directory / '.service.lock').open('a+')
            try:
                fcntl.flock(service_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                service_lock.close()
                service_lock = None
                raise RuntimeError(f'该目标的 LightRE 服务已运行：{reader.directory}') from None
            pending.reader = reader
            pending.update(phase='recovery_starting', message='静态分析完成，正在启动或恢复 Recovery')
            start_workflow(reader)
            pending.update(phase='recovery', message='静态分析完成，Recovery 已启动')
        except Exception as error:
            pending.error = error
            pending.update(phase='error', message='后台分析失败', error=str(error))
            app.state.startup_error = error
            print(f'LightRE: 后台分析失败：{error}', flush=True)
        finally:
            app.state.analysis_finished.set()
            if service_lock is not None:
                # Keep ownership until Uvicorn exits, including workflow startup errors.
                stopped.wait()
                service_lock.close()

    thread = threading.Thread(target=background_analysis, name='lightre-startup', daemon=True)
    thread.start()
    print(f'LightRE: Web UI：http://{args.host}:{args.port}/；正在后台准备分析', flush=True)
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        stopped.set()
        thread.join(timeout=1)


if __name__ == '__main__':
    main()
