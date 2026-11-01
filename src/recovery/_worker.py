"""Internal process started by the analysis flow or Web feedback."""

import os
import sys

from workspace import WorkspaceReader
from .queue import run_queue


if __name__ == "__main__":
    try:
        run_queue(WorkspaceReader(sys.argv[1]), int(sys.argv[2]))
    finally:
        os.close(int(sys.argv[2]))
