#!/usr/bin/env python3
"""Build an independent Bionic ARM64 SO, preserving the Linux deliverables."""

import argparse
import contextlib
import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
LIGHTRE = ROOT.parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New build directory; never reused')
    parser.add_argument('--obfuscation', choices=('none', 'small', 'large'), default='small')
    parser.add_argument('--sysroot', type=Path, default=LIGHTRE / 'tools/android-ndk-r27d/toolchains/llvm/prebuilt/linux-x86_64/sysroot')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'sources').mkdir()
    manifest = {'status': 'building', 'started_at': time.time(), 'api': 23,
                'target': 'aarch64-linux-android23', 'obfuscation': args.obfuscation,
                'commands': []}

    def save():
        (output / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')

    def run(command, env=None):
        command = [str(x) for x in command]
        record = {'command': command, 'environment': env or {}, 'started_at': time.time()}
        manifest['commands'].append(record)
        save()
        result = subprocess.run(command, cwd=output, env={**os.environ, **(env or {})}, capture_output=True, text=True)
        record.update(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr, finished_at=time.time())
        save()
        if result.returncode:
            raise RuntimeError(f'Command failed: {command}\n{result.stderr}')
        return result.stdout + result.stderr

    try:
        sysroot = args.sysroot.resolve()
        libs = sysroot / 'usr/lib/aarch64-linux-android/23'
        if not (libs / 'libc.so').is_file():
            raise RuntimeError('Install the Android sysroot with LightRE/scripts/install_deps.sh --android')
        source = output / 'sources/minilua.c'
        shutil.copyfile(ROOT / 'src/minilua.c', source)
        shutil.copyfile(ROOT / 'src/unidbg_api.c', output / 'sources/unidbg_api.c')
        if args.obfuscation == 'large':
            run(['patch', '--batch', source, '-i', ROOT / 'patches/minilua-large.patch'])
        inputs = [ROOT / 'sources.json', Path(__file__).resolve(), ROOT / 'patches/obfuscator-function-filter.patch']
        if args.obfuscation == 'large':
            inputs += [ROOT / 'patches/minilua-large.patch', ROOT / 'patches/obfuscator-split-filter.patch']
        manifest['build_inputs'] = {str(path): sha(path) for path in inputs}
        manifest['source_files'] = {str(path): sha(path) for path in (ROOT / 'src/minilua.c', ROOT / 'src/unidbg_api.c', source)}
        manifest['ndk_properties'] = (sysroot.parents[4] / 'source.properties').read_text()
        compiler = 'clang' if args.obfuscation == 'none' else 'clang-14'
        manifest['compiler'] = run([compiler, '--version'])
        manifest['linker'] = run(['ld.lld', '--version'])
        flags = ['--target=aarch64-linux-android23', '--sysroot=' + str(sysroot), '-Os',
                 '-fPIC', '-fvisibility=hidden', '-ffunction-sections', '-fdata-sections']
        obj = output / 'baseline.o'
        if args.obfuscation == 'none':
            run([compiler, *flags, '-c', 'sources/unidbg_api.c', '-o', obj])
        else:
            # Reuse the sample's pinned, function-filtered LLVM14 pass setup.
            import build
            pins = json.loads((ROOT / 'sources.json').read_text())
            setup_log = io.StringIO()
            try:
                with contextlib.redirect_stdout(setup_log):
                    plugin = build.prepare_tool(pins, split=args.obfuscation == 'large')
            finally:
                manifest['tool_setup_commands'] = build.COMMANDS
                (output / 'tool-setup.log').write_text(setup_log.getvalue())
                save()
            manifest['obfuscator'] = {**pins['obfuscator'], 'plugin_sha256': sha(plugin), 'seed': pins['seed']}
            run([compiler, *flags, '-emit-llvm', '-c', 'sources/unidbg_api.c', '-o', 'baseline.bc'])
            run(['llc-14', '-O2', '-mtriple=aarch64-linux-android23', '-relocation-model=pic', '-filetype=obj',
                 'baseline.bc', '-o', obj])
            targets = ['luaV_execute'] if args.obfuscation == 'large' else pins['targets']
            passes = 'split-basic-blocks,flattening,flattening' if args.obfuscation == 'large' else 'flattening'
            options = ['-load', plugin, '-split_num=5'] if args.obfuscation == 'large' else []
            trace = run(['opt-14', *options, '-load-pass-plugin', plugin,
                         f'-passes=function({passes}),verify', '-verify-each', 'baseline.bc', '-o', 'obfuscated.bc'],
                        {'LLVM_OBF_FUNCTIONS': ','.join(targets), 'LLVM_OBF_SEED': pins['seed']})
            expected = (['[split] luaV_execute', '[flattening] luaV_execute', '[flattening] luaV_execute']
                        if args.obfuscation == 'large' else [f'[flattening] {x}' for x in targets])
            if sorted(trace.splitlines()) != sorted(expected):
                raise RuntimeError(f'Unexpected obfuscator trace: {trace}')
            manifest['obfuscator'].update(targets=targets, passes=passes, trace=trace)
            run(['llc-14', '-O2', '-mtriple=aarch64-linux-android23', '-relocation-model=pic', '-filetype=obj',
                 'obfuscated.bc', '-o', 'obfuscated.o'])
        variants = ['baseline'] + ([] if args.obfuscation == 'none' else ['obfuscated'])
        for variant in variants:
            run([compiler, *flags, '-fuse-ld=lld', '-shared', '-nostdlib', libs / 'crtbegin_so.o',
                 output / f'{variant}.o', '-L' + str(libs), '-lc', '-lm', '-ldl', libs / 'crtend_so.o',
                 '-Wl,--no-undefined', '-Wl,--gc-sections', '-Wl,--build-id=sha1', '-Wl,-soname,libminilua.so',
                 '-o', f'{variant}.so'])
        shutil.copyfile(output / f'{variants[-1]}.so', output / 'libminilua.so')
        manifest['elf'] = run(['readelf', '-h', '-d', '-Ws', 'libminilua.so'])
        manifest.update(status='built', binary=str(output / 'libminilua.so'), binary_sha256=sha(output / 'libminilua.so'), finished_at=time.time())
        save()
        print(output / 'libminilua.so')
    except BaseException as error:
        manifest.update(status='failed', error=str(error), finished_at=time.time())
        save()
        raise


if __name__ == '__main__':
    main()
