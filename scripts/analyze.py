#!/usr/bin/env python3
"""Analyze a target and save complete results before opening the Web."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analyzers.rizin import analyze
from workspace import WorkspaceReader


def main():
    parser = argparse.ArgumentParser(description="用 Rizin 导出函数与完整汇编到 workspace")
    parser.add_argument("target", type=Path, help="SO 或输入目录")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--name")
    parser.add_argument("--force", action="store_true", help="重新导出已有结果")
    args = parser.parse_args()
    directory = analyze(args.target, args.workspace, args.name, args.force)
    count = WorkspaceReader(directory).catalog()["sample"]["function_count"]
    print(f"SO workspace: {directory}\n已导出 {count} 个函数及其完整汇编", flush=True)


if __name__ == "__main__":
    main()
