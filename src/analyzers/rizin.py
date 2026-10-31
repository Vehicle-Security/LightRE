"""Run Rizin once and publish functions and their full original assembly."""

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

from workspace import PROJECT_DIR, RESULT_SCHEMA, prepare_workspace


ANALYSIS_COMMAND = "aaa"
ANALYSIS_PROFILE = "rizin-aaa-assembly-v1"


def find_rizin() -> str:
    local = PROJECT_DIR / "tools/rizin/bin/rizin"
    found = str(local) if local.is_file() else shutil.which("rizin")
    if not found:
        raise RuntimeError("找不到 Rizin，请先运行 scripts/install_deps.sh")
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


def merged_ranges(blocks: list, base: int) -> list:
    result = []
    for block in sorted(blocks, key=lambda b: b["addr"]):
        start, end = block["addr"] - base, block["addr"] - base + block["size"]
        if end <= start:
            continue
        if (end - start) % 4:
            raise RuntimeError(f"ARM64 函数块大小异常：{start:#x}")
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def _publish(conn, rizin: Rizin, base: int, version: str):
    functions = json.loads(rizin.command("aflj"))
    # One transaction replaces all functions and assembly, including deleted rows.
    with conn:
        conn.execute("DELETE FROM assembly")
        conn.execute("DELETE FROM functions")
        for function in functions:
            address = function["offset"]
            rva = address - base
            ranges = merged_ranges(json.loads(rizin.command(f"afbj @ {address}")), base)
            count = sum((end - start) // 4 for start, end in ranges)
            conn.execute("INSERT INTO functions VALUES (?,?,?,?)",
                         (rva, function["name"], count, json.dumps(ranges)))
            ordinal = 0
            for start, end in ranges:
                for chunk_start in range(start, end, 4 * 2048):
                    size = min(2048, (end - chunk_start) // 4)
                    ops = json.loads(rizin.command(f"pdj {size} @ {chunk_start + base}"))
                    if len(ops) != size or any(
                        op["offset"] != chunk_start + base + i * 4
                        for i, op in enumerate(ops)
                    ):
                        # Rizin may advance one byte for an invalid ARM64 opcode.
                        # Preserve invalid slots without shifting subsequent addresses.
                        ops = [json.loads(rizin.command(f"pdj 1 @ {slot + base}"))[0]
                               for slot in range(chunk_start, chunk_start + size * 4, 4)]
                    conn.executemany("INSERT INTO assembly VALUES (?,?,?,?,?)", [
                        (rva, ordinal + i, op["offset"] - base, op["bytes"], op["opcode"])
                        for i, op in enumerate(ops)
                    ])
                    ordinal += size
        conn.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", {
            "base": str(base), "state": "ready", "error": "",
            "schema_version": RESULT_SCHEMA, "analysis_profile": ANALYSIS_PROFILE,
            "rizin_version": version,
        }.items())
        conn.execute("DELETE FROM metadata WHERE key='export_error'")


def analyze(target: Path, workspace: Path | None = None, name: str | None = None,
            force: bool = False) -> Path:
    """Synchronously export or reuse complete results; return the SO directory."""
    directory = prepare_workspace(target, workspace, name)
    version = subprocess.check_output([find_rizin(), "-v"], text=True).strip()
    with sqlite3.connect(directory / "index.sqlite", timeout=30) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        meta = dict(conn.execute("SELECT key,value FROM metadata"))
        if (not force and meta["state"] == "ready"
                and meta.get("schema_version") == RESULT_SCHEMA
                and meta.get("analysis_profile") == ANALYSIS_PROFILE
                and meta.get("rizin_version") == version):
            return directory
        conn.execute("""CREATE TABLE IF NOT EXISTS assembly (
            function_rva INTEGER NOT NULL REFERENCES functions(rva) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL, rva INTEGER NOT NULL,
            bytes TEXT NOT NULL, opcode TEXT NOT NULL,
            PRIMARY KEY (function_rva, ordinal)
        )""")
        rizin = None
        try:
            rizin = Rizin(directory / "binary/original.so")
            info = json.loads(rizin.command("ij"))["bin"]
            if info.get("machine") != "ARM aarch64":
                raise ValueError(f'目前只支持 ARM64 ELF，当前为 {info.get("machine", "未知格式")}')
            rizin.command(ANALYSIS_COMMAND)
            _publish(conn, rizin, info["baddr"], version)
        except Exception as error:
            # Published data remains unchanged if any part of export fails.
            with conn:
                conn.execute("INSERT OR REPLACE INTO metadata VALUES ('export_error',?)", (str(error),))
            raise
        finally:
            if rizin:
                rizin.close()
    return directory
