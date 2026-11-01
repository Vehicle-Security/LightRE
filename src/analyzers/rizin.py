"""Optional Rizin command session for Agent queries; Ghidra owns the catalog."""

import shutil
import subprocess
from pathlib import Path

from workspace import PROJECT_DIR


def find_rizin() -> str:
    local = PROJECT_DIR / "tools/rizin/bin/rizin"
    found = str(local) if local.is_file() else shutil.which("rizin")
    if not found:
        raise RuntimeError("找不到 Rizin，请先运行 scripts/install_deps.sh --with-rizin")
    return found


class Rizin:
    def __init__(self, binary: Path):
        self.process = subprocess.Popen(
            [find_rizin(), "-2q0", str(binary)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self._read()

    def _read(self) -> str:
        output = bytearray()
        while True:
            chunk = self.process.stdout.read1(65536)
            if not chunk:
                raise RuntimeError(f"Rizin 提前退出：{self.process.poll()}")
            if chunk.endswith(b"\0"):
                output.extend(chunk[:-1])
                return output.decode("utf-8", errors="replace").strip()
            output.extend(chunk)

    def command(self, command: str) -> str:
        self.process.stdin.write((command + "\n").encode())
        self.process.stdin.flush()
        return self._read()

    def close(self):
        self.process.terminate()
        self.process.wait(timeout=10)
        self.process.stdin.close()
        self.process.stdout.close()
