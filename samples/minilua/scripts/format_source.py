#!/usr/bin/env python3
"""Format the editable source and regenerate its patch against upstream."""
import difflib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / "src/minilua.c"
    formatted = subprocess.check_output(
        ["clang-format-14", "--style=file", str(source)], text=True)
    original = (ROOT / "src/minilua.original.c").read_text()
    patch = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), formatted.splitlines(keepends=True),
        fromfile="minilua.original.c", tofile="minilua.c"))
    source.write_text(formatted)
    (ROOT / "src/modifications.patch").write_text(patch)
    print("Formatted src/minilua.c and updated src/modifications.patch")


if __name__ == "__main__":
    main()
