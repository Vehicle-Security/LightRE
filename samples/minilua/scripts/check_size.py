#!/usr/bin/env python3
"""Count actual regular-file bytes; reject symlinks and special files."""
import json
import stat
import sys
from pathlib import Path


def check_size(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"not a real directory: {directory}")
    files = {}
    for path in sorted(directory.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f"symlink/special file forbidden: {path}")
        if stat.S_ISREG(mode):
            files[str(path.relative_to(directory))] = path.stat().st_size
    total = sum(files.values())
    if total > 300_000:
        raise ValueError(f"deliverable too large: {total} > 300000 bytes")
    return {"files": files, "total_bytes": total, "limit_bytes": 300_000}


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(check_size(sys.argv[1] if len(sys.argv) > 1 else root / "deliverable"), indent=2))
