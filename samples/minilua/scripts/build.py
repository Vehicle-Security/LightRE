#!/usr/bin/env python3
"""Pinned LLVM 14 build, selective flattening, then real execution and checks."""
import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
COMMANDS = []


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args, *, cwd=None, env=None, echo_output=True):
    cwd = Path.cwd() if cwd is None else cwd
    args = [str(a) for a in args]
    record = {"argv": args, "cwd": str(cwd), "environment": env or {}}
    COMMANDS.append(record)
    print("+ " + shlex.join(args), flush=True)
    process_env = {k: v for k, v in os.environ.items() if not k.startswith("LLVM_OBF_")}
    process_env.update({"LC_ALL": "C", "TZ": "UTC"})
    process_env.update(env or {})
    result = subprocess.run(args, cwd=cwd, env=process_env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    if echo_output:
        print(result.stdout, end="", flush=True)
    result.check_returncode()
    return result.stdout


def prepare_tool(pins, *, split=False):
    version = run(["llvm-config-14", "--version"]).strip()
    if version != pins["obfuscator"]["llvm_version"]:
        raise RuntimeError(f"LLVM {version} is not the pinned version")
    if "aarch64" not in run(["llvm-config-14", "--targets-built"]).lower():
        raise RuntimeError("LLVM lacks the AArch64 backend")
    tool = ROOT / ".cache/obfuscator-llvm"
    commit = pins["obfuscator"]["commit"]
    if not tool.exists():
        run(["git", "clone", pins["obfuscator"]["repository"], tool])
    run(["git", "restore", "--", "flattening/Flattening.cpp", "split/SplitBasicBlocks.cpp"], cwd=tool)
    run(["git", "checkout", "--detach", commit], cwd=tool)
    if run(["git", "rev-parse", "HEAD"], cwd=tool).strip() != commit:
        raise RuntimeError("obfuscator revision mismatch")
    original = run(["git", "show", f"{commit}:flattening/Flattening.cpp"], cwd=tool, echo_output=False)
    (tool / "flattening/Flattening.cpp").write_text(original)
    run(["git", "apply", ROOT / "patches/obfuscator-function-filter.patch"], cwd=tool)
    if split:
        run(["git", "apply", ROOT / "patches/obfuscator-split-filter.patch"], cwd=tool)
    changed = run(["git", "diff", "--name-only"], cwd=tool).splitlines()
    expected = ["flattening/Flattening.cpp", *(["split/SplitBasicBlocks.cpp"] if split else [])]
    if changed != expected:
        raise RuntimeError(f"unexpected tool modifications: {changed}")
    llvm_dir = run(["llvm-config-14", "--cmakedir"]).strip()
    tool_build = ROOT / ".cache/obfuscator-build"
    run(["cmake", "-G", "Ninja", "-S", tool, "-B", tool_build,
         f"-DLLVM_DIR={llvm_dir}", "-DCMAKE_BUILD_TYPE=Release",
         "-DCMAKE_C_COMPILER=clang-14", "-DCMAKE_CXX_COMPILER=clang++-14"])
    run(["cmake", "--build", tool_build, "--parallel", "2"])
    return tool_build / "libLLVMObfuscator.so"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opt", choices=["O2", "Os"], help="small defaults to O2; large defaults to Os")
    parser.add_argument("--large", action="store_true", help="split and flatten the interpreter twice")
    args = parser.parse_args()
    base = ROOT / "large" if args.large else ROOT
    base.mkdir(exist_ok=True)
    os.chdir(base)
    for directory in ["reference", "reports", "deliverable"]:
        (base / directory).mkdir(exist_ok=True)
    (ROOT / ".cache").mkdir(exist_ok=True)
    # Do not leave a successful report from a previous build on failure.
    for name in ["manifest.json", "reports/verification.md", "reports/tests.json",
                 "reports/tests.log", "reports/cfg.json", "reports/elf.json",
                 "reports/lightre.json", "reports/lightre.md", "reports/rebuild.json"]:
        (base / name).unlink(missing_ok=True)
    pins = json.loads((ROOT / "sources.json").read_text())
    if sha256(ROOT / "src/minilua.original.c") != pins["minilua"]["sha256"]:
        raise RuntimeError("original minilua hash mismatch")
    # Reapply the reviewed patch, never silently trust an edited build input.
    patched = ROOT / (".cache/minilua-large.c" if args.large else ".cache/minilua.c")
    shutil.copyfile(ROOT / "src/minilua.original.c", patched)
    run(["patch", "--batch", str(patched), "-i", ROOT / "src/modifications.patch"])
    if patched.read_bytes() != (ROOT / "src/minilua.c").read_bytes():
        raise RuntimeError("src/minilua.c does not match modifications.patch")
    if args.large:
        run(["patch", "--batch", str(patched), "-i", ROOT / "patches/minilua-large.patch"])
        pins["targets"] = ["luaV_execute"]
        Path("sources.json").write_text(json.dumps(pins, indent=2) + "\n")
    args.opt = args.opt or (pins["large"]["optimization"] if args.large else "O2")

    plugin = prepare_tool(pins, split=args.large)

    compile_flags = ["--target=aarch64-linux-gnu", "--gcc-toolchain=/usr",
                     f"-{args.opt}", "-fPIE", "-ffunction-sections", "-fdata-sections", "-g0"]
    link_flags = [*compile_flags, "-pie", "-Wl,--gc-sections", "-Wl,--build-id=sha1"]
    if args.large:
        link_flags += ["-Wl,-z,max-page-size=16384"]
    # Use a stable relative filename for deterministic source_filename/ModuleID.
    source = "src/minilua.c"
    if args.large:
        shutil.copyfile(patched, "reference/minilua.c")
        source = "reference/minilua.c"
    run(["clang-14", *compile_flags, "-emit-llvm", "-c", source,
         "-o", "reference/baseline.bc"])
    passes = "flattening"
    options = []
    if args.large:
        passes = "split-basic-blocks," + ",".join(["flattening"] * pins["large"]["flatten_rounds"])
        options = ["-load", plugin, f"-split_num={pins['large']['split_num']}"]
    trace = run(["opt-14", *options, "-load-pass-plugin", plugin,
                 f"-passes=function({passes}),verify", "-verify-each",
                 "reference/baseline.bc", "-o", "reference/obfuscated.bc"],
                env={"LLVM_OBF_SEED": pins["seed"],
                     "LLVM_OBF_FUNCTIONS": ",".join(pins["targets"])})
    Path("reports/flattening.log").write_text(trace)
    expected = (["[split] luaV_execute"] + ["[flattening] luaV_execute"] * pins["large"]["flatten_rounds"]
                if args.large else [f"[flattening] {n}" for n in pins["targets"]])
    if sorted(trace.splitlines()) != sorted(expected):
        raise RuntimeError("passes did not apply exactly to the selected targets")
    for variant in ["baseline", "obfuscated"]:
        prefix = f"reference/{variant}"
        run(["llvm-dis-14", f"{prefix}.bc", "-o", f"{prefix}.ll"])
        run(["llc-14", "-O2", "-mtriple=aarch64-linux-gnu", "-relocation-model=pic",
             "-function-sections", "-data-sections", "-filetype=obj",
             f"{prefix}.bc", "-o", f"{prefix}.o"])
        run(["clang-14", *link_flags, f"{prefix}.o", "-lm", "-o", f"{prefix}.full"])
        assembly = run(["aarch64-linux-gnu-objdump", "-d", f"{prefix}.full"], echo_output=False)
        Path(f"{prefix}.asm").write_text(assembly)
    shutil.copyfile("reference/baseline.full", "reference/baseline.stripped")
    Path("reference/baseline.stripped").chmod(0o755)
    run(["aarch64-linux-gnu-strip", "--strip-all", "reference/baseline.stripped"])
    shutil.copyfile("reference/obfuscated.full", "deliverable/minilua")
    Path("deliverable/minilua").chmod(0o755)
    run(["aarch64-linux-gnu-strip", "--strip-all", "deliverable/minilua"])
    shutil.copyfile(ROOT / "tests/smoke.lua", "deliverable/smoke.lua")

    native = platform.machine().lower() in ["aarch64", "arm64"]
    sysroot = "/" if native else os.environ.get("AARCH64_SYSROOT", "/usr/aarch64-linux-gnu")
    runtime = {"mode": "native" if native else "qemu", "sysroot": sysroot,
               "sysroot_source": "native libc6/libc6-dev packages" if native else
                   ("libc6-arm64-cross/libc6-dev-arm64-cross packages" if sysroot == "/usr/aarch64-linux-gnu"
                    else os.environ.get("AARCH64_SYSROOT_SOURCE", "custom AARCH64_SYSROOT; provenance not supplied")),
               "runner": [] if native else ["qemu-aarch64", "-L", sysroot]}
    tools = {name: run(command).strip() for name, command in {
        "clang": ["clang-14", "--version"], "llvm": ["llc-14", "--version"],
        "linker": ["aarch64-linux-gnu-ld", "--version"],
        "binutils": ["aarch64-linux-gnu-strip", "--version"],
        "cmake": ["cmake", "--version"], "python": ["python3", "--version"],
        "libc_packages": ["dpkg-query", "-W", "libc6", "libc6-dev",
                          *([] if native else ["libc6-arm64-cross", "libc6-dev-arm64-cross"])],
    }.items() if command[0] != "dpkg-query" or shutil.which("dpkg-query")}
    if not native:
        tools["qemu"] = run(["qemu-aarch64", "--version"]).strip()
    else:
        tools["qemu"] = "not used; executed on native AArch64 Linux"
    manifest = {"sources": pins, "environment": {"platform": platform.platform(),
                "os_release": Path("/etc/os-release").read_text(), "tools": tools},
                "optimization": args.opt, "compile_flags": compile_flags,
                "link_flags": link_flags, "runtime": runtime,
                "pass_stage": "explicit opt after clang -O2/-Os IR optimization, before llc -O2; no LTO",
                "plugin_sha256": sha256(plugin), "commands": COMMANDS,
                "profile": "large" if args.large else "small", "passes": passes,
                "compiled_source_sha256": sha256(source)}
    # test.py reads only the runtime config; final manifest is written after all checks pass.
    Path("reports/runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
    mode = ["--large"] if args.large else []
    run(["python3", ROOT / "scripts/test.py", *mode])
    run(["python3", ROOT / "scripts/verify.py", *mode])
    manifest["commands"] = COMMANDS
    manifest["size"] = json.loads(Path("reports/elf.json").read_text())["size"]
    manifest["tests"] = json.loads(Path("reports/tests.json").read_text())
    manifest["elf"] = json.loads(Path("reports/elf.json").read_text())
    manifest["flattening"] = json.loads(Path("reports/cfg.json").read_text())
    inputs = [ROOT / "sources.json", ROOT / ".clang-format", *(ROOT / "src").glob("*"),
              *(ROOT / "patches").glob("*"), *(ROOT / "scripts").glob("*.py"),
              *(ROOT / "scripts").glob("*.sh"), *(ROOT / "tests").glob("*")]
    manifest["input_sha256"] = {str(p.relative_to(ROOT)): sha256(p) for p in sorted(inputs) if p.is_file()}
    artifacts = [Path("sources.json"), *Path("reference").glob("*"),
                 *Path("deliverable").glob("*"), *Path("reports").glob("*.json"),
                 *Path("reports").glob("*.md"), *Path("reports").glob("*.readelf.txt"),
                 Path("reports/flattening.log")]
    manifest["sha256"] = {str(p): sha256(p) for p in sorted(artifacts) if p.is_file()}
    Path("manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print("PASS: build, differential tests, ELF, flattening and size; see manifest.json")


if __name__ == "__main__":
    main()
