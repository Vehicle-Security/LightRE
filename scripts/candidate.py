#!/usr/bin/env python3
"""Read or save a function candidate without invoking an analysis tool."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from candidates import read_candidate, save_candidate
from workspace import WorkspaceReader


def main():
    parser = argparse.ArgumentParser(description="读取或保存单函数的未验证 C 候选")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("read", "save"):
        sub = commands.add_parser(command)
        sub.add_argument("workspace", type=Path, help="已完成分析的 SO 结果目录")
        sub.add_argument("rva", type=lambda value: int(value, 0))
        if command == "save":
            sub.add_argument("--file", type=Path, required=True, help="UTF-8 C 候选文件")
    args = parser.parse_args()
    try:
        reader = WorkspaceReader(args.workspace)
        if args.command == "save":
            print(save_candidate(reader, args.rva, args.file.read_text(encoding="utf-8")))
        else:
            candidate = read_candidate(reader, args.rva)
            if candidate["code"] is None:
                parser.exit(1, "尚未还原\n")
            sys.stdout.write(candidate["code"])
    except (KeyError, ValueError, OSError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
