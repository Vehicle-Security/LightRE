#!/usr/bin/env python3
"""Configure, run, check, replay and repair Android ARM64 Maven harnesses."""

import argparse
import json
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from execution.guide import ensure
from execution.loop import repair
from execution.runner import run, check
from execution.storage import exclusive, read_json
from execution.contract import configure, case_request, cases
from workspace import PROJECT_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    guide = commands.add_parser('guide', help='Create per-target HARNESS.md first; preserve existing adaptations')
    guide.add_argument('harness', type=Path, help='External harness project root, not an SO directory')
    guide.add_argument('--target', default='target', help='Display name; no target type registry')
    guide.add_argument('--instructions', type=Path, help='Target-specific adaptation Markdown')
    config = commands.add_parser('configure', help='Associate any Maven harness and an explicit verification contract')
    config.add_argument('target', type=Path, help='Existing SO analysis directory, or a new Android SO')
    config.add_argument('--harness', type=Path, required=True, help='External editable Maven project')
    config.add_argument('--cases', dest='cases_file', type=Path, required=True)
    config.add_argument('--main-class', required=True)
    config.add_argument('--checker', type=Path, help='Standalone fixed Python checker, outside editable harness')
    config.add_argument('--artifact', action='append', default=[], help='Required artifact path relative to Run; repeatable')
    config.add_argument('--instructions', type=Path)
    config.add_argument('--workspace', type=Path, help='Original workspace root, required when importing a new SO')
    listing = commands.add_parser('cases', help='List saved target cases')
    listing.add_argument('directory', type=Path)
    for name in ('run', 'replay', 'repair', 'check'):
        command = commands.add_parser(name)
        command.add_argument('directory', type=Path, help='Selected SO directory containing harness.json')
        command.add_argument('--timeout', type=float, default=300, help='Per-process timeout in seconds')
        if name == 'run':
            command.add_argument('--request', type=Path, help='Target request JSON; optional input_hex or input_file')
            command.add_argument('--case', help='Select a configured case')
        if name == 'replay':
            command.add_argument('run', type=Path)
        if name == 'repair':
            command.add_argument('--task', type=Path, help='Resume an existing harness task')
            command.add_argument('--max-rounds', type=int, default=3)
            command.add_argument('--description', default='修复目标 harness，真实调用 SO 并通过固定用例。')
    cancel = commands.add_parser('cancel', help='Request cancellation of a running harness task')
    cancel.add_argument('task', type=Path)
    args = parser.parse_args()
    stopped = False

    def stop(signum, frame):
        nonlocal stopped
        stopped = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    try:
        if args.command == 'guide':
            # No SO association or runnable project is created by this command.
            harness = args.harness.resolve()
            if harness.is_relative_to(PROJECT_DIR):
                raise ValueError('Use an editable harness outside the LightRE repository')
            harness.mkdir(parents=True, exist_ok=True)
            with exclusive(None, harness):
                print(ensure(harness, args.target, instructions=args.instructions))
            return 0
        if args.command == 'configure':
            print(configure(args.target, harness=args.harness, cases_file=args.cases_file,
                            main_class=args.main_class, checker=args.checker, artifacts=args.artifact,
                            workspace=args.workspace, instructions=args.instructions))
            return 0
        if args.command == 'cases':
            print(json.dumps(cases(args.directory), ensure_ascii=False, indent=2))
            return 0
        if args.command == 'cancel':
            state = read_json(args.task / 'state.json')
            if state.get('mode') != 'harness':
                raise ValueError('Not a harness task')
            (args.task / 'cancel').touch()
            print('Cancellation requested:', args.task)
            return 0
        if args.timeout <= 0:
            raise ValueError('timeout must be positive')
        options = {'timeout': args.timeout, 'cancelled': lambda: stopped}
        if args.command == 'repair':
            report = repair(args.directory, task=args.task, description=args.description,
                            max_rounds=args.max_rounds, **options)
            passed = report['status'] == 'complete'
        elif args.command == 'check':
            report = check(args.directory, **options)
            passed = report['passed']
        else:
            if args.command == 'run' and args.request and args.case:
                raise ValueError('Use either --request or --case')
            request = read_json(args.request) if args.command == 'run' and args.request else None
            if args.command == 'run' and args.case:
                request = case_request(args.directory, args.case)
            report = run(args.directory, request,
                         request_base=args.request.resolve().parent if args.command == 'run' and args.request else None,
                         replay=args.run if args.command == 'replay' else None, **options)
            passed = report['passed']
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if passed else 1
    except (OSError, ValueError, RuntimeError, SyntaxError, TypeError, KeyError) as error:
        parser.exit(1, f'error: {error}\n')


if __name__ == '__main__':
    sys.exit(main())
