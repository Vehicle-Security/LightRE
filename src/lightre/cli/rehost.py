#!/usr/bin/env python3
"""Create a fresh ARM64 unidbg harness through investigation and runtime feedback."""

import argparse
import json
import signal
import sys
from pathlib import Path

from rehost import start, resume


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    new = commands.add_parser('start', help='Start from an empty target Java project')
    new.add_argument('target', type=Path, help='Existing SO analysis directory, or Android ARM64 SO')
    goal = new.add_mutually_exclusive_group(required=True)
    goal.add_argument('--goal', help='Describe the entry/behavior to run and any known constraints')
    goal.add_argument('--task-file', type=Path, help='Read the goal from Markdown')
    new.add_argument('--workspace', type=Path, help='Required to import a new SO')
    new.add_argument('--input', action='append', default=[], type=Path, help='Seed file; repeatable')
    new.add_argument('--material', action='append', default=[], type=Path, help='Read-only supporting file/directory')
    new.add_argument('--cases', dest='cases_file', type=Path, help='Fixed independent expected results, if available')
    new.add_argument('--checker', type=Path, help='Independent fixed checker; requires --cases')
    new.add_argument('--artifact', action='append', default=[], help='Required Run artifact; repeatable')
    again = commands.add_parser('resume', help='Continue a saved rehost task instead of starting over')
    again.add_argument('target', type=Path)
    again.add_argument('--task', required=True, type=Path)
    for command in (new, again):
        command.add_argument('--max-rounds', type=int, help='Optional caller limit for this invocation')
        command.add_argument('--timeout', type=float, default=600, help='Per-process deadline, seconds')
    args = parser.parse_args(argv)
    stopped = False

    def stop(signum, frame):
        nonlocal stopped
        stopped = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    options = dict(max_rounds=args.max_rounds, timeout=args.timeout, cancelled=lambda: stopped)
    try:
        if args.command == 'start':
            description = args.task_file.read_text(encoding='utf-8') if args.task_file else args.goal
            state = start(args.target, description=description, workspace=args.workspace,
                          inputs=args.input, materials=args.material, cases_file=args.cases_file,
                          checker=args.checker, artifacts=args.artifact, **options)
        else:
            state = resume(args.target, args.task, **options)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        # "observed" is an operational result, with passed=False in the report.
        return 0 if state['status'] in ('complete', 'observed') else 1
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        parser.exit(1, f'error: {error}\n')


if __name__ == '__main__':
    sys.exit(main())
