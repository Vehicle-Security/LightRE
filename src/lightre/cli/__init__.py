"""Installed command entry points; each command also supports python -m."""

import argparse
import importlib
import sys
from importlib.metadata import version

COMMANDS = ('agent', 'analyze', 'harness', 'rehost', 'web')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description='LightRE ARM64 analysis and agent tools')
    parser.add_argument('--version', action='version', version=version('lightre'))
    parser.add_argument('command', choices=COMMANDS)
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv[:1])
    return importlib.import_module(f'lightre.cli.{args.command}').main(argv[1:])
