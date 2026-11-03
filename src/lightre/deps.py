"""Dependency discovery and provisioning for the LightRE service.

Python dependencies are managed by ``pyproject.toml``/``uv.lock``.  This
module owns native dependencies and managed tools so ``lightre <workspace>``
can bootstrap a new machine without a shell environment script.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .paths import TOOLS_DIR

PROFILES = {
    "runtime": {
        "commands": ("java", "javac", "mvn", "clang", "ld.lld", "clang-format", "readelf"),
        "packages": ("ca-certificates", "default-jdk", "maven", "clang", "lld", "clang-format", "binutils"),
        "python": (), "ghidra": False, "rizin": False, "android": False,
    },
    "analysis": {
        "commands": ("java", "javac", "mvn", "clang", "ld.lld", "clang-format", "readelf",
                     "curl", "git", "unzip", "make", "gcc", "g++", "bison", "flex", "file", "rg", "codex"),
        "packages": ("ca-certificates", "default-jdk", "maven", "clang", "lld", "clang-format",
                     "binutils", "curl", "git", "unzip", "make", "gcc", "g++", "bison", "flex",
                     "python3-venv", "file", "ripgrep", "zlib1g-dev"),
        "python": ("tree_sitter", "tree_sitter_c"), "ghidra": True, "rizin": False, "android": False,
    },
    "rizin": {
        "commands": ("java", "javac", "mvn", "clang", "ld.lld", "clang-format", "readelf",
                     "curl", "git", "unzip", "make", "gcc", "g++", "bison", "flex",
                     "meson", "ninja", "pkg-config"),
        "packages": ("ca-certificates", "default-jdk", "maven", "clang", "lld", "clang-format",
                     "binutils", "curl", "git", "unzip", "make", "gcc", "g++", "bison", "flex",
                     "python3-venv", "meson", "ninja-build", "pkg-config", "libcapstone-dev",
                     "liblz4-dev", "libzip-dev", "libxxhash-dev", "libmagic-dev", "libssl-dev",
                     "libzstd-dev", "libtree-sitter-dev", "zlib1g-dev"),
        "python": ("tree_sitter", "tree_sitter_c"), "ghidra": True, "rizin": True, "android": False,
    },
    "android": {
        "commands": ("java", "javac", "mvn", "clang", "ld.lld", "clang-format", "readelf",
                     "curl", "unzip", "make", "gcc", "g++", "bison", "flex", "clang-14",
                     "llvm-config-14", "opt-14", "llc-14", "cmake", "ninja", "patch"),
        "packages": ("ca-certificates", "default-jdk", "maven", "clang", "lld", "clang-format",
                     "binutils", "curl", "unzip", "make", "gcc", "g++", "bison", "flex",
                     "python3-venv", "clang-14", "llvm-14-dev", "cmake", "ninja-build", "patch"),
        "python": ("tree_sitter", "tree_sitter_c"), "ghidra": False, "rizin": False, "android": True,
    },
}


def _spec(profile):
    try:
        return PROFILES[profile]
    except KeyError as error:
        raise ValueError(f"unknown dependency profile: {profile}") from error


def _command(name):
    if name == 'codex':
        name = os.environ.get('LIGHTRE_CODEX_BIN', name)
    return shutil.which(name)


def _locations():
    return {
        "root": TOOLS_DIR,
        "ghidra": Path(os.environ.get("GHIDRA_HOME", TOOLS_DIR / "ghidra")).expanduser(),
        "rizin": TOOLS_DIR / "rizin",
        "android_ndk": TOOLS_DIR / "android-ndk-r27d",
        "cache": TOOLS_DIR / "cache",
    }


def tool_locations():
    return {key: str(value) for key, value in _locations().items()}


def _java_major(executable="java"):
    path = _command(executable) or executable
    try:
        result = subprocess.run([path, "-version"], capture_output=True, text=True, check=False)
    except OSError:
        return None
    text = result.stdout + result.stderr
    import re
    match = re.search(r'version "(\d+)', text) or re.search(r'\b(\d+)\.', text)
    return int(match.group(1)) if match else None


def _required_java(profile):
    if _spec(profile)['ghidra']:
        properties = _locations()['ghidra'] / 'Ghidra/application.properties'
        if properties.is_file():
            for line in properties.read_text().splitlines():
                if line.startswith('application.java.min='):
                    return max(17, int(line.split('=', 1)[1]))
    return 17


def _ghidra_platform():
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "linux_x86_64"
    if machine in ("aarch64", "arm64"):
        return "linux_arm_64"
    raise RuntimeError(f"unsupported Linux architecture for Ghidra: {machine}")


def _ghidra_native(home=None):
    home = Path(home or _locations()["ghidra"])
    native = home / "Ghidra/Features/Decompiler/os" / _ghidra_platform()
    return native / "decompile", native / "sleigh"


def _has_ghidra(home=None):
    home = Path(home or _locations()["ghidra"])
    decompile, sleigh = _ghidra_native(home)
    return (home / "support/analyzeHeadless").is_file() and decompile.is_file() and sleigh.is_file()


def _has_rizin():
    binary = _command("rizin") or (_locations()["rizin"] / "bin/rizin")
    return Path(binary).is_file() and os.access(binary, os.X_OK)


def _has_android_sysroot():
    root = _locations()["android_ndk"]
    return (root / "source.properties").is_file() and (
        root / "toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/include/android/api-level.h"
    ).is_file() and (
        root / "toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib/aarch64-linux-android/23/libc.so"
    ).is_file()


def check(profile="runtime"):
    spec = _spec(profile)
    missing = [name for name in spec["commands"] if not _command(name)]
    missing_python = [name for name in spec["python"] if importlib.util.find_spec(name) is None]
    required_java = _required_java(profile)
    for command in ('java', 'javac'):
        if (_java_major(command) or 0) < required_java and command not in missing:
            missing.append(f'{command}>={required_java}')
    tools = {"ghidra": _has_ghidra(), "rizin": _has_rizin(), "android_sysroot": _has_android_sysroot()}
    if spec["ghidra"] and not tools["ghidra"]:
        missing.append("ghidra")
    if spec["rizin"] and not tools["rizin"]:
        missing.append("rizin")
    if spec["android"] and not tools["android_sysroot"]:
        missing.append("android-ndk-r27d-sysroot")
    return {"profile": profile, "ok": not missing and not missing_python,
            "missing_commands": missing, "missing_python": missing_python,
            "tools": tools, "tools_dir": str(TOOLS_DIR), "platform": platform.platform()}


def _run(command, *, cwd=None):
    return subprocess.run(command, cwd=cwd, check=True, text=True,
                          env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'})


def _install_packages(packages, *, update=True):
    if not _command("apt-get"):
        raise RuntimeError("automatic native dependency installation requires apt-get")
    prefix = [] if os.geteuid() == 0 else (["sudo"] if _command("sudo") else None)
    if prefix is None:
        raise RuntimeError("system installation requires root or sudo")
    if update:
        _run([*prefix, "apt-get", "update"])
    _run([*prefix, "apt-get", "install", "-y", '--no-install-recommends', *packages])


def install_system(profile="runtime", *, apt=True):
    spec = _spec(profile)
    if not apt:
        return {"installed": [], "skipped": "apt disabled"}
    packages = [f'openjdk-{max(21, _required_java(profile))}-jdk-headless'
                if package == 'default-jdk' else package for package in spec['packages']]
    _install_packages(packages)
    return {"installed": packages}


def install_codex():
    """Install the standalone Linux binary without requiring Node or npm."""
    if _command('codex'):
        return _command('codex')
    if os.environ.get('LIGHTRE_CODEX_BIN'):
        raise RuntimeError('LIGHTRE_CODEX_BIN points to a missing executable')
    machine = platform.machine().lower()
    architecture = {'x86_64': 'x86_64', 'amd64': 'x86_64',
                    'aarch64': 'aarch64', 'arm64': 'aarch64'}.get(machine)
    if architecture is None:
        raise RuntimeError(f'unsupported Codex Linux architecture: {machine}')
    name = f'codex-{architecture}-unknown-linux-musl'
    cache = _locations()['cache']
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / (name + '.tar.gz')
    if archive.exists() and not tarfile.is_tarfile(archive):
        archive.unlink()
    if not archive.exists():
        _download(
            f'https://github.com/openai/codex/releases/latest/download/{name}.tar.gz', archive)
    destination = TOOLS_DIR / 'bin/codex'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as bundle:
        member = next((item for item in bundle.getmembers()
                       if item.isfile() and Path(item.name).name in (name, 'codex')), None)
        if member is None:
            raise RuntimeError('Codex archive has no matching executable')
        temporary = destination.with_suffix('.tmp')
        with bundle.extractfile(member) as source, temporary.open('wb') as target:
            shutil.copyfileobj(source, target)
        temporary.chmod(0o755)
        temporary.replace(destination)
    return str(destination)


def _download(url, destination):
    """Keep failed or partial downloads out of the reusable tool cache."""
    destination = Path(destination)
    print(f'LightRE: 正在下载 {destination.name}', flush=True)
    temporary = destination.with_suffix(destination.suffix + '.part')
    if url.startswith('file:'):
        urllib.request.urlretrieve(url, temporary)
    else:
        _run(['curl', '--fail', '--location', '--silent', '--show-error',
              '--retry', '3', '--retry-all-errors', '--connect-timeout', '30',
              '--output', str(temporary), url])
    temporary.replace(destination)


def _latest_ghidra_url():
    response = subprocess.run(
        ['curl', '--fail', '--location', '--silent', '--show-error', '--retry', '3',
         '--retry-all-errors', '--connect-timeout', '30', '--max-time', '60',
         '--header', 'Accept: application/vnd.github+json', '--user-agent', 'LightRE',
         'https://api.github.com/repos/NationalSecurityAgency/ghidra/releases/latest'],
        check=True, text=True, capture_output=True)
    release = json.loads(response.stdout)
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if "_PUBLIC_" in name and name.endswith(".zip") and "-src" not in name:
            return asset["browser_download_url"]
    raise RuntimeError("Ghidra release has no PUBLIC zip asset")


def install_ghidra(url=None, *, destination=None):
    locations = _locations()
    destination = Path(destination or locations["ghidra"]).expanduser().resolve()
    if (destination / 'support/analyzeHeadless').is_file():
        return str(destination)
    url = url or os.environ.get("GHIDRA_URL") or _latest_ghidra_url()
    cache = locations["cache"]
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / Path(url.split("?", 1)[0]).name
    if archive.exists() and not zipfile.is_zipfile(archive):
        archive.unlink()
    if not archive.exists():
        _download(url, archive)
    temp = Path(tempfile.mkdtemp(prefix=".ghidra-install-", dir=locations["root"]))
    try:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(temp)
            for member in bundle.infolist():
                mode = (member.external_attr >> 16) & 0o777
                if mode:
                    (temp / member.filename).chmod(mode)
        roots = [path for path in temp.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("invalid Ghidra archive")
        if destination.exists():
            raise RuntimeError(f"Ghidra destination exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        roots[0].replace(destination)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return str(destination)


def prepare_ghidra_native(home=None):
    home = Path(home or _locations()["ghidra"])
    decompile, sleigh = _ghidra_native(home)
    if decompile.is_file() and sleigh.is_file():
        return {"decompile": str(decompile), "sleigh": str(sleigh)}
    source = home / "Ghidra/Features/Decompiler/src/decompile/cpp"
    if not source.is_dir():
        raise RuntimeError(f"Ghidra decompiler source is missing: {source}")
    native = decompile.parent
    _run(["make", "-C", str(source), "-j" + os.environ.get("BUILD_JOBS", "4"), "ghidra_opt", "ARCH_TYPE=", f"OSDIR={native.name}"])
    _run(["make", "-C", str(source), "-j" + os.environ.get("BUILD_JOBS", "4"), "sleigh_opt", "ARCH_TYPE=", f"OSDIR={native.name}"])
    native.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "ghidra_opt", decompile)
    shutil.copy2(source / "sleigh_opt", sleigh)
    decompile.chmod(0o755)
    sleigh.chmod(0o755)
    return {"decompile": str(decompile), "sleigh": str(sleigh)}


def install_rizin():
    if _has_rizin():
        return str(_locations()["rizin"])
    source = _locations()["root"] / "src/rizin"
    build = source / "build-lightre"
    source.parent.mkdir(parents=True, exist_ok=True)
    if not (source / ".git").is_dir():
        _run(["git", "clone", "--depth", "1", "https://github.com/rizinorg/rizin.git", str(source)])
    setup = ["meson", "setup"]
    if (build / "meson-private/coredata.dat").exists():
        setup.append("--reconfigure")
    _run([*setup, str(build), str(source), "--prefix=" + str(_locations()["rizin"]), "--buildtype=release", "-Dportable=true"])
    _run(["meson", "compile", "-C", str(build)])
    _run(["meson", "install", "-C", str(build)])
    if not _has_rizin():
        raise RuntimeError("Rizin build completed but binary is missing")
    return str(_locations()["rizin"])


def install_android_sysroot():
    if _has_android_sysroot():
        return str(_locations()["android_ndk"])
    locations = _locations()
    cache = locations["cache"]
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / "android-ndk-r27d-linux.zip"
    if not archive.exists():
        _download(
            "https://dl.google.com/android/repository/android-ndk-r27d-linux.zip", archive)
    digest = "22105e410cf29afcf163760cc95522b9fb981121"
    import hashlib
    actual = hashlib.sha1(archive.read_bytes()).hexdigest()
    if actual != digest:
        raise RuntimeError(f"Android NDK checksum mismatch: {actual}")
    _run(["unzip", "-q", "-o", str(archive),
          "android-ndk-r27d/source.properties",
          "android-ndk-r27d/toolchains/llvm/prebuilt/linux-x86_64/sysroot/*",
          "-d", str(locations["root"])])
    if not _has_android_sysroot():
        raise RuntimeError("Android NDK sysroot is incomplete")
    return str(locations["android_ndk"])


def ensure(profile="runtime", *, install=None):
    """Check and, by default, provision missing dependencies for a profile."""
    _spec(profile)
    if install is None:
        install = os.environ.get("LIGHTRE_AUTO_INSTALL_DEPS", "1") != "0"
    binary_dir = str(TOOLS_DIR / 'bin')
    if binary_dir not in os.environ.get('PATH', '').split(os.pathsep):
        os.environ['PATH'] = binary_dir + os.pathsep + os.environ.get('PATH', '')
    report = check(profile)
    if install and not report["ok"]:
        install_system(profile)
        spec = PROFILES[profile]
        if spec["ghidra"] and not _has_ghidra():
            install_ghidra()
            required_java = _required_java(profile)
            if min(_java_major('java') or 0, _java_major('javac') or 0) < required_java:
                _install_packages([f'openjdk-{required_java}-jdk-headless'], update=False)
            javac = _command('javac')
            if javac:
                os.environ['JAVA_HOME'] = str(Path(javac).resolve().parent.parent)
            prepare_ghidra_native()
        if 'codex' in spec['commands'] and not _command('codex'):
            install_codex()
        if spec["rizin"] and not _has_rizin():
            install_rizin()
        if spec["android"] and not _has_android_sysroot():
            install_android_sysroot()
        report = check(profile)
    if not report["ok"]:
        missing = ", ".join(report["missing_commands"] + report["missing_python"])
        raise RuntimeError(f"missing {profile} dependencies: {missing}")
    return report
