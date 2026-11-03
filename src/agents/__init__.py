"""Codex execution management.

## API

### Sequential (blocking)

    from agents import run_blocking

    result = run_blocking(
        root=agents_root,
        directory=output_dir,
        cwd=working_dir,
        prompt=prompt
    )  # blocks until complete

### Concurrent (async)

    from agents import run_async

    # Launch multiple runs (non-blocking)
    runs = []
    for task in tasks:
        run = run_async(root, task_dir, cwd=..., prompt=task_prompt)
        runs.append(run)

    # Wait for all
    results = [run.wait() for run in runs]

### When to use which?

- `run_blocking()`: One task at a time (execution/loop.py)
- `run_async()`: Multiple tasks in parallel (recovery/jobs.py)
"""

from .manager import AgentManager, AgentRun, AgentSession, run_blocking, run_async, collect_blocking

__all__ = ['run_blocking', 'run_async', 'collect_blocking', 'AgentManager', 'AgentRun', 'AgentSession']
