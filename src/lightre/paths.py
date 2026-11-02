"""Runtime assets and external tool locations in both editable and wheel installs."""

import os
import shlex
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SOURCE_DIR = PACKAGE_DIR.parent
_candidate = SOURCE_DIR.parent
REPOSITORY_DIR = _candidate if SOURCE_DIR.name == 'src' and (_candidate / 'pyproject.toml').is_file() else None
# Compatibility for existing workspace guards and repository-only examples.
PROJECT_DIR = REPOSITORY_DIR or PACKAGE_DIR
_data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
TOOLS_DIR = Path(os.environ.get('LIGHTRE_TOOLS_DIR',
                               REPOSITORY_DIR / 'tools' if REPOSITORY_DIR else _data / 'lightre/tools')).expanduser().resolve()
CLANG_FORMAT = PACKAGE_DIR / 'resources/clang-format'


def python_command(module):
    """A shell-safe command for generated instructions, using this installation."""
    return shlex.join([sys.executable, '-m', module])
