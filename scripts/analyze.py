#!/usr/bin/env python3
"""Analyze a target and save complete results before opening the Web."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analyzers.ghidra import analyze
from workspace import WorkspaceReader
from recovery.jobs import start_task


def main():
    parser = argparse.ArgumentParser(description="导出静态结果并生成候选，或从已有工作区的汇编直接生成候选")
    parser.add_argument("target", type=Path, help="SO 或输入目录；assembly 模式为已导出的 SO 工作区")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--name")
    parser.add_argument("--force", action="store_true", help="重新导出已有结果")
    parser.add_argument("--static-only", action="store_true", help="只导出汇编，不启动 Codex")
    parser.add_argument("--rva", type=lambda value: int(value, 0), action="append", help="仅还原指定函数，可重复；默认全部未完成函数")
    parser.add_argument("--jobs", type=int, help="Codex 同时运行的批次数，默认 8")
    parser.add_argument('--input-mode', choices=('reference', 'assembly'), default='reference',
                        help='reference 使用 Ghidra 初稿；assembly 直接分析已有工作区的汇编，target 须为工作区目录')
    args = parser.parse_args()
    if args.input_mode == 'assembly':
        if args.force or args.static_only or args.workspace or args.name:
            parser.error('assembly 模式直接读取已有工作区，不使用 --force/--static-only/--workspace/--name')
        directory = WorkspaceReader(args.target).directory
    else:
        directory = analyze(args.target, args.workspace, args.name, args.force)
    count = WorkspaceReader(directory).catalog()["sample"]["function_count"]
    print(f"SO workspace: {directory}\n已保存 {count} 个函数；模型输入模式：{args.input_mode}", flush=True)
    if not args.static_only:
        try:
            task = start_task(WorkspaceReader(directory), args.rva, workers=args.jobs, input_mode=args.input_mode)
        except (RuntimeError, ValueError, OSError) as error:
            parser.exit(1, f"error: {error}；静态结果已保存，可先打开 Web 浏览。\n")
        print(f"候选还原任务：{task['status']}；进度将在 Web 展示", flush=True)


if __name__ == "__main__":
    main()
