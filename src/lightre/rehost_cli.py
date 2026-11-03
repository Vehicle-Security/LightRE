"""独立运行 Rehost：恢复或推进一个 SO 分析目录的 unidbg harness 任务。

harness 的修改只能通过本命令驱动的 rehost agent 轮次完成。
"""

import argparse
import json
import sys
import threading
import time
from pathlib import Path

from rehost.workflow import WORKFLOW, continue_analysis, run_rounds


def _analysis_dir(raw: str) -> Path:
    directory = Path(raw).resolve()
    if (directory / 'index.sqlite').is_file():
        return directory
    candidates = sorted(directory.glob('*/index.sqlite'))
    if len(candidates) == 1:
        return candidates[0].parent
    listing = '\n'.join(str(path.parent) for path in candidates) or '没有找到 index.sqlite'
    raise ValueError(f'请指定包含 index.sqlite 的 SO 分析目录：\n{listing}')


def _materials(directory: Path) -> list:
    manifest = directory.parent / 'target.json'
    if not manifest.is_file():
        return []
    saved = json.loads(manifest.read_text(encoding='utf-8'))
    source = (manifest.parent / saved['source_dir']).resolve()
    return [source] if source.is_dir() else []


def _watch(directory: Path, stopped: threading.Event):
    """轮询任务状态文件，打印轮次和状态变化。"""
    last = {}
    while not stopped.is_set():
        for path in sorted((directory / 'tasks').glob('*/state.json')):
            try:
                state = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if state.get('workflow') != str(WORKFLOW) or '.' in path.parent.name:
                continue
            key = (path.parent.name, state.get('status'), state.get('round'))
            if last.get(path.parent.name) != key:
                last[path.parent.name] = key
                reason = state.get('stopped_reason') or ''
                print(f'Rehost {path.parent.name}: round {state.get("round")} {state.get("status")} {reason}'.rstrip(),
                      flush=True)
        stopped.wait(2)


def main(argv=None):
    parser = argparse.ArgumentParser(description='LightRE Rehost：独立运行或恢复 unidbg harness 任务')
    parser.add_argument('directory', type=str, help='SO 分析目录（包含 index.sqlite）或其 workspace')
    parser.add_argument('--description', help='发起新任务时的目标描述；缺省时恢复现有任务')
    parser.add_argument('--rounds', type=int, default=None, help='本轮最多推进多少轮（缺省不限）')
    parser.add_argument('--timeout', type=int, default=1800, help='每轮 agent/构建/运行的超时秒数（缺省 1800）')
    args = parser.parse_args(argv)
    if args.rounds is not None and args.rounds < 1:
        parser.error('--rounds 必须为正整数')
    if args.timeout <= 0:
        parser.error('--timeout 必须为正整数')
    try:
        directory = _analysis_dir(args.directory)
    except ValueError as error:
        parser.error(str(error))

    def run():
        if args.description:
            return run_rounds(directory, description=args.description, workflow=WORKFLOW,
                          bootstrap=True, max_rounds=args.rounds, timeout=args.timeout)
        return continue_analysis(directory, materials=_materials(directory),
                                 max_rounds=args.rounds, timeout=args.timeout)

    stopped = threading.Event()
    watcher = threading.Thread(target=_watch, args=(directory, stopped), daemon=True)
    watcher.start()
    try:
        state = run()
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Rehost 失败：{error}', file=sys.stderr, flush=True)
        stopped.set()
        watcher.join(timeout=1)
        raise SystemExit(1)
    stopped.set()
    watcher.join(timeout=1)
    print(json.dumps({key: state[key] for key in ('status', 'round', 'stopped_reason')
                      if key in state}, ensure_ascii=False, indent=2))
    return state


if __name__ == '__main__':
    main()
