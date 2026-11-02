#!/usr/bin/env python3
"""Compatibility wrapper; install LightRE (uv sync) before running."""

from lightre.cli.analyze import main


if __name__ == "__main__":
    raise SystemExit(main())
