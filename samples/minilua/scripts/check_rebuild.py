#!/usr/bin/env python3
"""Rebuild a successful sample once and compare all linked/delivered bytes."""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--large", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT / "large" if args.large else ROOT)
    previous = json.loads(Path("manifest.json").read_text())
    files = ["reference/baseline.full", "reference/baseline.stripped", "reference/obfuscated.full",
             "deliverable/minilua", "deliverable/smoke.lua"]
    before = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in files}
    assert all(before[name] == previous["sha256"][name] for name in files), "existing artifacts changed"
    subprocess.run(["bash", ROOT / "scripts/build.sh", "--opt", previous["optimization"],
                    *(["--large"] if args.large else [])], check=True)
    current = json.loads(Path("manifest.json").read_text())
    assert previous["environment"] == current["environment"], "build environment changed"
    if "input_sha256" in previous:
        assert previous["input_sha256"] == current["input_sha256"], "build inputs changed"
    after = {name: current["sha256"][name] for name in files}
    assert before == after, "rebuild changed output bytes"
    Path("reports/rebuild.json").write_text(json.dumps({
        "passed": True, "method": "rebuild with the same optimization and recorded environment; compare SHA-256",
        "sha256_before": before, "sha256_after": after}, indent=2) + "\n")
    print(f"PASS: all {len(files)} linked/delivered files reproduce identical bytes")


if __name__ == "__main__":
    main()
