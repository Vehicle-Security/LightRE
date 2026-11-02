#!/usr/bin/env python3
"""Create, query and message durable Codex agents from any local process."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from agents import AgentManager


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=os.environ.get('LIGHTRE_AGENT_ROOT', '.lightre-agents'),
                        help='Shared registry/output directory; all clients must use the same root')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create', help='Create a session without starting a model')
    create.add_argument('--cwd', type=Path, required=True)
    create.add_argument('--codex', help='Override LIGHTRE_CODEX_BIN')
    create.add_argument('--model')
    create.add_argument('--profile')
    create.add_argument('--sandbox', choices=['read-only', 'workspace-write', 'danger-full-access'])
    create.add_argument('--ephemeral', action='store_true', help='Independent turns; no Codex conversation resume')
    commands.add_parser('list')
    for name in ('status', 'send', 'pause', 'resume', 'cancel', 'run', 'events', 'result', 'wait'):
        command = commands.add_parser(name)
        command.add_argument('--session', required=True, help='Managed session ID or its registered Codex thread ID')
        if name in ('cancel', 'run', 'events', 'result', 'wait'):
            command.add_argument('--run', help='Run ID; defaults to active run, otherwise latest run')
        if name == 'send':
            prompt = command.add_mutually_exclusive_group(required=True)
            prompt.add_argument('--message')
            prompt.add_argument('--message-file', help='UTF-8 file, or - for stdin')
            command.add_argument('--timeout', type=float, default=600, help='Execution deadline in seconds')
            command.add_argument('--schema', type=Path)
            command.add_argument('--request-id', help='Idempotency key within this session')
            command.add_argument('--delivery', choices=['next_turn', 'immediate'], default='next_turn')
        if name == 'events':
            command.add_argument('--cursor', type=int, default=0)
            command.add_argument('--limit', type=int, default=100)
        if name == 'wait':
            command.add_argument('--timeout', type=float, help='Client wait only; does not cancel the run')
    args = parser.parse_args(argv)
    exit_code = 0
    try:
        manager = AgentManager(args.root)
        if args.command == 'create':
            value = manager.create(args.cwd, executable=args.codex, model=args.model, profile=args.profile,
                                   sandbox=args.sandbox, persistent=not args.ephemeral).snapshot()
        elif args.command == 'list':
            value = manager.list()
        else:
            session = manager.get(args.session)
            if args.command == 'status':
                value = session.snapshot()
            elif args.command == 'resume':
                value = session.resume().snapshot()
            elif args.command == 'pause':
                value = session.pause().snapshot()
            elif args.command == 'send':
                message = args.message
                if args.message_file:
                    message = sys.stdin.read() if args.message_file == '-' else Path(args.message_file).read_text(encoding='utf-8')
                schema = json.loads(args.schema.read_text(encoding='utf-8')) if args.schema else None
                value = session.send_message(message, timeout=args.timeout, output_schema=schema,
                                             request_id=args.request_id, delivery=args.delivery).poll()
            elif args.command == 'cancel' and not args.run:
                session.cancel()
                value = session.snapshot()
            else:
                run = session.get_run(args.run)
                if args.command == 'cancel':
                    run.cancel()
                    value = run.poll()
                elif args.command == 'events':
                    value = run.read_events(args.cursor, args.limit)
                elif args.command == 'result':
                    value = run.result()
                    exit_code = 0 if value['status'] == 'completed' else 1
                elif args.command == 'wait':
                    value = run.wait(args.timeout)
                    exit_code = 0 if value['status'] == 'completed' else 1
                else:
                    value = run.poll()
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return exit_code
    except (OSError, ValueError, RuntimeError, KeyError, TimeoutError, NotImplementedError, sqlite3.Error) as error:
        print(json.dumps({'error': str(error), 'type': type(error).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
