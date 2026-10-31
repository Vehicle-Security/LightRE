#!/usr/bin/env python3
"""Compare an already exported minilua workspace with its assembly reference."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from workspace import WorkspaceReader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True,
                        help="completed SO result directory containing index.sqlite")
    args = parser.parse_args()
    reader = WorkspaceReader(args.workspace)
    catalog = reader.catalog()
    sample = catalog["sample"]
    profiles = [ROOT, ROOT / "large"]
    candidates = {"reference/baseline.full": "baseline",
                  "reference/baseline.stripped": "baseline",
                  "reference/obfuscated.full": "obfuscated",
                  "deliverable/minilua": "obfuscated"}
    match = next(((base, name) for base in profiles for name in candidates if (base / name).is_file()
                  and hashlib.sha256((base / name).read_bytes()).hexdigest() == sample["sha256"]), None)
    if match is None:
        raise ValueError("workspace SHA-256 does not match a current minilua sample")
    base, relative = match
    variant = candidates[relative]
    cfg = json.loads((base / "reports/cfg.json").read_text())
    rows = {row["rva"]: row for row in catalog["functions"]}
    result = {"input": str((base / relative).relative_to(ROOT)), "workspace": str(reader.directory),
              "sha256": sample["sha256"], "rizin_version": sample.get("rizin_version"),
              "analysis_profile": sample.get("analysis_profile"), "targets": {}}
    for name, versions in cfg["targets"].items():
        rva = int(versions[variant]["address"], 16)
        text = (base / f"reference/{variant}.{name}.asm").read_text()
        end = rva + versions[variant]["code_bytes"]
        expected = {int(address, 16): int(word, 16).to_bytes(4, "little").hex()
                    for address, word in re.findall(r"^[ \t]*([0-9a-f]+):[ \t]+([0-9a-f]{8})\b", text, re.M)
                    if rva <= int(address, 16) < end}
        if not expected:
            raise ValueError(f"empty assembly reference: {name}")
        actual, offset, pages = {}, 0, 0
        if rva in rows:
            while True:
                page = reader.assembly(rva, offset=offset, limit=200)
                actual.update((op["rva"], op["bytes"]) for op in page["instructions"])
                pages += 1
                if page["next_offset"] is None:
                    break
                if page["next_offset"] <= offset:
                    raise RuntimeError("pagination made no progress")
                offset = page["next_offset"]
        target = {"rva": hex(rva), "identified": rva in rows,
                  "expected_instructions": len(expected), "returned_instructions": len(actual),
                  "pages": pages, "exact_coverage": actual == expected,
                  "missing_rvas": [hex(v) for v in sorted(expected.keys() - actual.keys())],
                  "extra_rvas": [hex(v) for v in sorted(actual.keys() - expected.keys())],
                  "wrong_bytes_rvas": [hex(v) for v in sorted(actual.keys() & expected.keys())
                                       if actual[v] != expected[v]]}
        result["targets"][name] = target
        print(f"{name} @ {rva:#x}: {len(actual)}/{len(expected)} instructions; "
              f"exact={target['exact_coverage']}")
    result["all_targets_exact"] = all(t["exact_coverage"] for t in result["targets"].values())
    (base / "reports/lightre.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return 0 if result["all_targets_exact"] else 1


if __name__ == "__main__":
    sys.exit(main())
