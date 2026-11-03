"""Small file records and mutual exclusion, without a task service."""

import fcntl
import hashlib
import json
import time
from contextlib import contextmanager, ExitStack
from pathlib import Path
from uuid import uuid4


class WriterBusy(RuntimeError):
    """Another task still owns the workspace or harness writer lock."""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def identifier():
    return time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + '-' + uuid4().hex[:8]


def inventory(root):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Symlinks are not supported in a harness snapshot: {path}')
        if path.is_file():
            result[str(path.relative_to(root))] = digest(path)
    return result


@contextmanager
def exclusive(directory, harness):
    """Share the v0.2 SO writer lock; also exclude other SOs using this harness."""
    paths = {Path(harness) / '.lightre.lock'}
    if directory is not None:
        directory = Path(directory)
        (directory / 'tasks').mkdir(exist_ok=True)
        paths.add(directory / 'tasks/.lock')
    with ExitStack() as stack:
        handles = []
        for path in sorted(paths):
            handle = stack.enter_context(path.open('a+'))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WriterBusy(f'Another writer is active: {path}') from None
            handles.append(handle)
        yield tuple(handle.fileno() for handle in handles)
