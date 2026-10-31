#!/usr/bin/env python3
"""Browse a completed workspace; no binary import or analysis occurs here."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workspace import WorkspaceReader
from webui.app import create_app
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="LightRE 函数和汇编浏览器")
    parser.add_argument("workspace", type=Path, help="已导出的 SO 分析目录；只有一个 SO 时也可给外层 workspace")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    reader = WorkspaceReader(args.workspace)
    print(f"Workspace: {reader.directory}", flush=True)
    uvicorn.run(create_app(reader), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
