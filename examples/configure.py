#!/usr/bin/env python3
"""Sample setup only. The generic runner does not import this module."""
import argparse
import sys
import shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from workspace import PROJECT_DIR, prepare_workspace
from execution.guide import ensure
from execution.storage import digest, exclusive, read_json, write_json

LUA_CASES = [
    ('smoke', 0, 'smoke: 10 42 3.5 3.75\n', ''),
    ('tables', 0, 'tables: 4096 25171968 keys metatables\n', ''),
    ('closures', 0, 'closures: 8 recursion: 3628800 varargs: 17 4\n', ''),
    ('errors', 0, 'errors: pcall nil NaN indexing loadstring\n', ''),
    ('allocation', 0, 'allocation: 60000 checksum: 1800090000 automatic-GC: observed\n', ''),
    ('capabilities', 0, 'capabilities: Lua 5.1 subset; io.write bit automatic-GC\n', ''),
    ('syntax_error', 3, '', "Error: tests/syntax_error.lua:1: '<name>' expected near '='\n"),
    ('runtime_error', 2, '', 'Error: tests/runtime_error.lua:1: uncaught test error\n'),
]


def configure(target, kind, *, workspace=None, programs=None, vm_entry_rva=None):
    """Generate instructions first, preserve editable sources, associate fixed cases."""
    if kind not in ('dg', 'minilua'):
        raise ValueError('configure accepts dg or minilua')
    target = Path(target).resolve()
    if target.is_file():
        if workspace is None:
            raise ValueError('Importing a new SO requires --workspace')
        directory = prepare_workspace(target, Path(workspace), name=f'{kind}-android')
    else:
        directory = target
    if directory.is_relative_to(PROJECT_DIR):
        raise ValueError('Target workspace must be outside the LightRE repository')
    from execution.runner import _target
    binary = _target(directory)
    harness = directory.parent / 'harnesses' / kind
    harness.mkdir(parents=True, exist_ok=True)
    with exclusive(directory, harness):
        ensure(harness, kind, instructions=PROJECT_DIR / 'examples' / kind / 'ADAPTATION.md')
        template = PROJECT_DIR / 'examples' / kind / 'harness'
        for source in template.rglob('*'):
            if source.is_file():
                destination = harness / source.relative_to(template)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    shutil.copyfile(source, destination)
        if kind == 'minilua':
            rows = []
            for name, status, stdout, stderr in LUA_CASES:
                source = PROJECT_DIR / f'samples/minilua/tests/{name}.lua'
                request = {'input_file': str(source), 'chunk_name': f'tests/{name}.lua'}
                if name == 'closures':
                    request['argument'] = 'fixed-argument'
                rows.append({'id': name, 'input_sha256': digest(source), 'request': request,
                             'expected': {'lua_status': status, 'exit_code': int(status != 0), 'stdout': stdout, 'stderr': stderr}})
        else:
            if vm_entry_rva is None:
                if binary['sha256'] != 'd65fc4e408a87ab24c0f355096ceb8c406176379067657f409d6b51e24283472':
                    raise ValueError('Unknown DG version: provide an investigated --vm-entry-rva')
                vm_entry_rva = '0x1c07c'
            if programs is None:
                raise ValueError('DG requires --programs pointing to the matching .bin directory')
            rows = []
            for source in sorted(Path(programs).resolve().glob('*.bin')):
                rows.append({'id': source.stem, 'input_sha256': digest(source),
                             'request': {'input_file': str(source), 'stage': 'run', 'vm_entry_rva': vm_entry_rva,
                                         'environment_profile': 'headless-v1', 'random_seed': 0,
                                         'runtime_gr': 0, 'runtime_i': 0, 'runtime_j': 0, 'runtime_h': '', 'runtime_m': '', 'runtime_k': '', 'monotonic_nanos': 1000000000, 'wall_time_millis': 1700000000000},
                             'expected': {'loaded': True, 'stage': 'run', 'handle_nonzero': True, 'closed': True, 'missing_jni': [], 'environment_profile': 'headless-v1'}})
            if not rows:
                raise ValueError('DG program directory has no .bin files')
        config = {'path': f'../harnesses/{kind}', 'checker': str(PROJECT_DIR / 'examples' / kind / 'checker.py'),
                  'artifacts': ['proto.txt'] if kind == 'minilua' else ['output.bin'],
                  'parameters': {'main_class': 'lightre.DgHarness' if kind == 'dg' else 'lightre.MiniluaHarness'},
                  'cases': 'harness-cases.json'}
        if (directory / 'harness.json').exists():
            previous = read_json(directory / 'harness.json')
            if (directory / previous['path']).resolve() != harness.resolve():
                raise ValueError('Existing harness path differs; preserve and review it explicitly')
            if previous.get('kind', kind) != kind or any(
                    key != 'kind' and previous[key] != config.get(key) for key in previous):
                raise ValueError('Existing harness contract differs; preserve and review it explicitly')
        if not (directory / 'harness-cases.json').exists():
            write_json(directory / 'harness-cases.json', rows)
        write_json(directory / 'harness.json', config)
        guide = harness / 'HARNESS.md'
        heading = f'## 已关联目标 {directory.name}'
        if heading not in guide.read_text(encoding='utf-8'):
            with guide.open('a', encoding='utf-8') as stream:
                stream.write(f'\n{heading}\n\nSO：`{directory / "binary/original.so"}`。\n'
                             f'SHA256：`{binary["sha256"]}`。\n'
                             f'调用与预期：`{directory / "harness-cases.json"}`。\n'
                             f'运行全部用例：`{PROJECT_DIR}/tools/venv/bin/python '
                             f'{PROJECT_DIR}/scripts/harness.py check {directory}`。\n'
                             '关联配置不等于运行通过；结果见最近外部检查区。\n')
    return directory


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path)
    parser.add_argument('--kind', choices=('dg', 'minilua'), required=True)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--programs', type=Path)
    parser.add_argument('--vm-entry-rva')
    args = parser.parse_args()
    print(configure(args.target, args.kind, workspace=args.workspace,
                    programs=args.programs, vm_entry_rva=args.vm_entry_rva))
