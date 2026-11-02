"""Prepare the fixed, freestanding Android test library and editable harness."""

import argparse
import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from workspace import PROJECT_DIR, prepare_workspace
from execution.guide import ensure
from execution.process import execute, succeeded
from execution.storage import digest, read_json, write_json


EXAMPLE = PROJECT_DIR / 'examples/native-smoke'


def prepare(workspace):
    workspace = Path(workspace).resolve()
    if workspace.is_relative_to(PROJECT_DIR):
        raise ValueError('Use a workspace outside the LightRE repository')
    build = workspace / 'fixtures/native-smoke'
    build.mkdir(parents=True, exist_ok=True)
    source = build / 'native_smoke.c'
    shutil.copyfile(EXAMPLE / 'native_smoke.c', source)
    compiler = shutil.which('clang')
    if not compiler or not shutil.which('ld.lld'):
        raise RuntimeError('Install clang and lld with scripts/install_deps.sh --runtime-only')
    version = execute([compiler, '--version'], build, build / 'compiler', timeout=10)
    if not succeeded(version):
        raise RuntimeError(f'Cannot inspect compiler: {build / "compiler"}')
    command = [compiler, '--target=aarch64-linux-android23', '-fuse-ld=lld', '-shared',
               '-nostdlib', '-fPIC', '-fno-stack-protector', '-fno-builtin', '-O1',
               '-Wl,-soname,libnative_smoke.so', '-Wl,--build-id=sha1',
               str(source), '-o', str(build / 'libnative_smoke.so')]
    execution = execute(command, build, build / 'build', timeout=60)
    if not succeeded(execution):
        raise RuntimeError(f'Test library build failed: {build / "build"}')
    binary = build / 'libnative_smoke.so'
    write_json(build / 'build.json', {'purpose': 'fixed test fixture, not a recovered candidate',
                                   'source_sha256': digest(source), 'binary_sha256': digest(binary),
                                   'compiler': read_json(build / 'compiler/execution.json'),
                                   'build': execution})
    directory = prepare_workspace(binary, workspace, name='native-smoke')
    harness = workspace / 'harnesses/native-smoke'
    # Instructions precede source generation and survive later preparations.
    ensure(harness, 'native-smoke', instructions=EXAMPLE / 'ADAPTATION.md')
    if not (harness / 'pom.xml').exists():
        for path in (EXAMPLE / 'harness').rglob('*'):
            if path.is_file():
                target = harness / path.relative_to(EXAMPLE / 'harness')
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copyfile(path, target)
    association = directory / 'harness.json'
    config = {'path': '../harnesses/native-smoke', 'cases': str(EXAMPLE / 'cases.json'),
              'checker': str(EXAMPLE / 'checker.py'), 'artifacts': [],
              'parameters': {'main_class': 'lightre.NativeHarness'},
              'protected_files': [str(build / 'build.json'), str(build / 'native_smoke.c')]}
    if association.exists():
        previous = read_json(association)
        if any(previous.get(key, value) != value for key, value in config.items()):
            raise ValueError('Existing fixture association differs; review it explicitly')
    write_json(association, config)
    return directory


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    print(prepare(parser.parse_args().workspace))
