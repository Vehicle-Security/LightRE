#!/usr/bin/env python3
"""Check expected behavior and compare stdout/stderr/status/timeout exactly."""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 30


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--large", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    base = ROOT / "large" if args.large else ROOT
    report = base / "reports/tests.json"
    report.unlink(missing_ok=True)
    runtime = json.loads((base / "reports/runtime.json").read_text())
    binaries = ["reference/baseline.full", "reference/baseline.stripped",
                "reference/obfuscated.full", "deliverable/minilua"]
    cases = [
        ("smoke", ["tests/smoke.lua"], 0, "smoke: 10 42 3.5 3.75\n", None),
        ("tables", ["tests/tables.lua"], 0, "tables: 4096 25171968 keys metatables\n", None),
        ("closures", ["tests/closures.lua", "fixed-argument"], 0,
         "closures: 8 recursion: 3628800 varargs: 17 4\n", None),
        ("errors", ["tests/errors.lua"], 0, "errors: pcall nil NaN indexing loadstring\n", None),
        ("allocation", ["tests/allocation.lua"], 0,
         "allocation: 60000 checksum: 1800090000 automatic-GC: observed\n", None),
        ("capabilities", ["tests/capabilities.lua"], 0,
         "capabilities: Lua 5.1 subset; io.write bit automatic-GC\n", None),
        ("syntax_error", ["tests/syntax_error.lua"], 1, "", "tests/syntax_error.lua:1:"),
        ("runtime_error", ["tests/runtime_error.lua"], 1, "", "uncaught test error"),
        ("no_script", [], 8, "", None),
        ("delivered_smoke", [str((base / "deliverable/smoke.lua").relative_to(ROOT))], 0, "smoke: 10 42 3.5 3.75\n", None),
    ]
    env = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}
    results, log = [], []
    for name, arguments, expected_code, expected_out, error_text in cases:
        observations = []
        for binary in binaries:
            command = [*runtime["runner"], str(base / binary), *arguments]
            start = time.monotonic()
            try:
                result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=TIMEOUT)
                observation = {"stdout": result.stdout.decode("utf-8"),
                               "stderr": result.stderr.decode("utf-8"),
                               "exit_code": result.returncode, "timed_out": False}
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(f"timeout ({TIMEOUT}s): {command}") from error
            if observation["exit_code"] != expected_code or observation["stdout"] != expected_out:
                raise RuntimeError(f"unexpected result in {name} / {binary}: {observation}")
            if error_text:
                if error_text not in observation["stderr"]:
                    raise RuntimeError(f"missing expected error in {name}: {observation}")
            elif observation["stderr"]:
                raise RuntimeError(f"unexpected stderr in {name}: {observation}")
            observations.append(observation)
            results.append({"case": name, "binary": binary, "command": command,
                            "elapsed_seconds": time.monotonic() - start, **observation})
        if any(observation != observations[0] for observation in observations[1:]):
            raise RuntimeError(f"differential mismatch: {name}: {observations}")
        line = f"PASS {name}: all 4 binaries agree (exit={expected_code}, timeout=false)"
        print(line, flush=True)
        log.append(line)
    payload = {"passed": True, "case_count": len(cases), "execution_count": len(results),
               "timeout_seconds": TIMEOUT, "normalization": "none; all output compared byte for byte",
               "runtime": runtime, "results": results}
    report.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    (base / "reports/tests.log").write_text("\n".join(log) + "\n")


if __name__ == "__main__":
    main()
