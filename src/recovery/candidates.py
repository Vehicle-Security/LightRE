"""Current candidates live in module files; SQLite only indexes their RVAs."""

import fcntl
import hashlib
import json
import re
import sqlite3
import shutil
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile

from lightre.workspace import WorkspaceReader
from lightre.prompt import render_prompt
from .markers import begin, end, parse, comments


FUNCTIONS_PER_FILE = 20
GHIDRA_MARKER = "/* LightRE producer: ghidra (初稿，尚未经过 LLM) */\n"


def _identity(reader):
    with reader._connect() as conn:
        conn.execute("BEGIN")
        meta = dict(conn.execute("SELECT key,value FROM metadata"))
        names = dict(conn.execute("SELECT rva,name FROM functions"))
    return meta, names


def _read_text(path):
    with path.open(encoding='utf-8', newline='') as stream:
        return stream.read()


def _legacy_header(meta, rva):
    return (
        "/* LightRE analysis candidate\n"
        f" * binary: {Path(meta['source_path']).name}\n"
        f" * sha256: {meta['sha256']}\n"
        f" * function_rva: {rva:#010x}\n"
        " * status: unverified (分析候选，未验证)\n"
        " */\n"
    )


def module_name(name):
    name = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "recovered"
    name = re.sub(r"(?:_\d{3})+$", "", name)
    return (name if name[0].isalpha() else "module_" + name)[:48]


def _module_header(meta):
    return '/* LightRE source module */\n\n'


def _legacy_module_header(meta):
    return ("/* LightRE source module\n"
            f" * binary: {Path(meta['source_path']).name}\n"
            f" * sha256: {meta['sha256']}\n"
            " * status: unverified\n */\n\n")


@lru_cache(maxsize=64)
def _parsed_blocks(text, source_path, sha256):
    meta = {'source_path': source_path, 'sha256': sha256}
    matches = parse(text, lambda rva: _legacy_header(meta, rva))
    for match in matches:
        if len(match.metadata['origins']) != 1:
            raise ValueError('当前索引仅支持每段源码对应一个原始 RVA；合并/拆分索引尚未实现')
    return tuple(matches)


def _blocks(text, meta):
    return _parsed_blocks(text, meta['source_path'], meta['sha256'])


def _module_prefix(text, meta):
    for header in (_module_header(meta), _legacy_module_header(meta)):
        if text.startswith(header):
            return header
        crlf = header.replace('\n', '\r\n')
        if text.startswith(crlf):
            return crlf
    raise ValueError('源码模块的来源注释不匹配')


def _fragment(meta, rva, code):
    """Parse a raw API fragment, which omits its closing marker."""
    opening = code.lstrip(' \t')
    if opening.startswith('// @lightre.begin '):
        from .markers import decode
        metadata = decode(opening.split('\n', 1)[0][len('// @lightre.begin '):].rstrip('\r'))
        closing = end(metadata['id'])
    elif code.startswith('/* LightRE analysis candidate'):
        closing = f'/* LightRE end function {rva:#010x} */'
    else:
        return None
    blocks = _blocks(code + '\n' + closing, meta)
    if len(blocks) != 1 or int(blocks[0]['rva'], 16) != rva:
        raise ValueError('候选的来源注释与当前 RVA 不匹配')
    return blocks[0]


def display_code(meta, rva, code):
    if code is None:
        return None
    fragment = _fragment(meta, rva, code)
    body = fragment.body if fragment else code
    return _strip_producer(body)


def _strip_producer(body):
    for marker in (GHIDRA_MARKER, GHIDRA_MARKER.replace('\n', '\r\n')):
        if body.startswith(marker):
            return body[len(marker):]
    return body


def canonical_candidate(meta, rva, code, current=None):
    """Normalize only reserved metadata, preserving ordinary source text."""
    fragment = _fragment(meta, rva, code)
    previous = _fragment(meta, rva, current) if current else None
    metadata = (previous or fragment).metadata if previous or fragment else {
        'v': 1, 'id': f'f{rva:08x}', 'origins': [f'{rva:#x}']}
    if previous and fragment and previous.metadata != fragment.metadata:
        raise ValueError('修改函数时必须保留已有源码 ID 与来源')
    body = fragment.body if fragment else code
    if not _strip_producer(body).strip():
        raise ValueError('候选代码不能为空')
    for _, _, comment in comments(body):
        if comment.startswith(('// @lightre.', '/* LightRE analysis candidate', '/* LightRE end function')):
            raise ValueError('候选正文不能包含其他函数的来源或边界标记')
    return begin(metadata) + '\n' + body


@contextmanager
def _locked(reader):
    with (reader.directory / ".source.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # A stopped merge either keeps the old tree or publishes the whole new tree.
        source = reader.directory / "source"
        previous = source / ".previous"
        if previous.exists():
            if not (source / "src").exists():
                previous.rename(source / "src")
            else:
                shutil.rmtree(previous)
        for staging in source.glob(".merge-*"):
            shutil.rmtree(staging)
        yield


def _index(reader, meta, names):
    files = {path: _read_text(path) for path in sorted((reader.directory / "source/src").glob("*.c"))}
    signature = hashlib.sha256(json.dumps(['markers-v1-content',
        sorted(names), [(str(path.relative_to(reader.directory)), hashlib.sha256(text.encode()).hexdigest())
                        for path, text in files.items()]
    ]).encode()).hexdigest()
    with reader._connect() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_index'").fetchone()
        if exists and meta.get("source_fingerprint") == signature:
            return dict(conn.execute("SELECT function_rva,path FROM source_index"))
    if not files and not exists:
        return {}
    rows, ids = {}, set()
    for path, text in files.items():
        try:
            _module_prefix(text, meta)
            matches = _blocks(text, meta)
        except ValueError as error:
            raise ValueError(f'{path}: {error}') from error
        for match in matches:
            if match.metadata['id'] in ids:
                raise ValueError(f"源码包含重复源码 ID：{match.metadata['id']}")
            ids.add(match.metadata['id'])
            rva = int(match['rva'], 16)
            if rva in rows:
                raise ValueError(f"源码包含重复函数 RVA：{rva:#x}")
            if rva in names:
                rows[rva] = str(path.relative_to(reader.directory))
    with sqlite3.connect(reader.database, timeout=30) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("""CREATE TABLE IF NOT EXISTS source_index (
            function_rva INTEGER PRIMARY KEY REFERENCES functions(rva) ON DELETE CASCADE,
            path TEXT NOT NULL)""")
        conn.execute("DELETE FROM source_index")
        conn.executemany("INSERT INTO source_index VALUES (?,?)", rows.items())
        conn.execute("INSERT OR REPLACE INTO metadata VALUES ('source_fingerprint',?)", (signature,))
    return rows


def candidate_origins(reader):
    """Read all current producers in one source snapshot, without per-function reads."""
    with _locked(reader):
        meta, names = _identity(reader)
        origins = {rva: "llm" for rva in _index(reader, meta, names)}
        for path in (reader.directory / "source/src").glob("*.c"):
            for match in _blocks(_read_text(path), meta):
                rva = int(match['rva'], 16)
                if rva in origins and _strip_producer(match.body) != match.body:
                    origins[rva] = "ghidra"
        for path in (reader.directory / "candidates").glob("0x*.c"):
            try:
                rva = int(path.stem, 16)
            except ValueError:
                continue
            if rva in names:
                origins.setdefault(rva, "llm")
        return origins


def candidate_rvas(reader, optimized_only=False):
    return {rva for rva, origin in candidate_origins(reader).items()
            if not optimized_only or origin == "llm"}


def read_candidate(reader: WorkspaceReader, rva: int) -> dict:
    """Return the exact function fragment from the current module file."""
    with _locked(reader):
        meta, names = _identity(reader)
        if rva not in names:
            raise KeyError(f"未识别的函数 RVA：{rva:#x}")
        index = _index(reader, meta, names)
        path = reader.directory / index.get(rva, "source/src/recovered.c")
        legacy = reader.directory / f"candidates/{rva:#010x}.c"
        code = None
        if rva in index:
            code = next(m['code'] for m in _blocks(_read_text(path), meta)
                        if int(m['rva'], 16) == rva)
        elif legacy.exists():
            path = legacy
            code = _read_text(path)
        fragment = _fragment(meta, rva, code) if code is not None else None
        if code is not None and fragment is None:
            raise ValueError(f"候选的来源注释与当前 SO / RVA 不匹配：{path}")
        producer = "ghidra" if fragment and _strip_producer(fragment.body) != fragment.body else "llm"
        return {"rva": rva, "name": names[rva], "path": str(path), "code": code,
                "display_code": _strip_producer(fragment.body) if fragment else None,
                "sha256": hashlib.sha256(code.encode()).hexdigest() if code is not None else None,
                "producer": producer if code is not None else None,
                "status": ("reference" if producer == "ghidra" else "unverified") if code is not None else "missing"}


def _atomic_write(path, text):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                            prefix=".source-", suffix=".tmp", newline='', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_candidate(reader: WorkspaceReader, rva: int, code: str, module="recovered") -> Path:
    """Replace just this function; keep its siblings and current module location."""
    with _locked(reader):
        meta, names = _identity(reader)
        if rva not in names:
            raise KeyError(f"未识别的函数 RVA：{rva:#x}")
        index = _index(reader, meta, names)
        if rva in index:
            path = reader.directory / index[rva]
            text = _read_text(path)
            match = next(m for m in _blocks(text, meta) if int(m['rva'], 16) == rva)
            canonical = canonical_candidate(meta, rva, code, match['code'])
            source_id = _fragment(meta, rva, canonical).metadata['id']
            text = text[:match.start()] + canonical + '\n' + end(source_id) + text[match.end():]
            prefix = _module_prefix(text, meta)
            text = _module_header(meta) + text[len(prefix):]
        else:
            canonical = canonical_candidate(meta, rva, code)
            source_id = _fragment(meta, rva, canonical).metadata['id']
            for existing in (reader.directory / 'source/src').glob('*.c'):
                if any(block.metadata['id'] == source_id for block in _blocks(_read_text(existing), meta)):
                    raise ValueError(f'源码包含重复源码 ID：{source_id}')
            module = module_name(module)
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", module):
                raise ValueError("模块名需为小写字母、数字或下划线，且以字母开头")
            directory = reader.directory / "source/src"
            directory.mkdir(parents=True, exist_ok=True)
            number = 1
            while True:
                suffix = "" if number == 1 else f"_{number:03d}"
                path = directory / f"{module}{suffix}.c"
                text = _read_text(path) if path.exists() else _module_header(meta)
                if len(_blocks(text, meta)) < FUNCTIONS_PER_FILE:
                    break
                number += 1
            prefix = _module_prefix(text, meta)
            text = _module_header(meta) + text[len(prefix):]
            text += canonical + '\n' + end(source_id) + '\n\n'
        _atomic_write(path, text)
        readme = reader.directory / "source/README.md"
        if not readme.exists():
            readme.write_text(render_prompt(Path(__file__).parent / 'prompts/SOURCE_README.md',
                                            FUNCTIONS_PER_FILE=FUNCTIONS_PER_FILE), encoding='utf-8')
        _index(reader, meta, names)
        return path


def migrate_candidates(reader):
    """Pack legacy one-function files, verify their bodies, then remove originals."""
    directory = reader.directory / "candidates"
    for path in sorted(directory.glob("0x*.c")):
        rva = int(path.stem, 16)
        original = _read_text(path)
        current = read_candidate(reader, rva)
        if current['code'] != original:
            raise ValueError(f"旧候选与模块源码不同，保留两者供检查：{path}")
        save_candidate(reader, rva, original)
        meta, _ = _identity(reader)
        if _fragment(meta, rva, read_candidate(reader, rva)['code']).body != _fragment(meta, rva, original).body:
            raise ValueError(f"迁移校验失败，保留旧候选：{path}")
        path.unlink()
    if directory.exists() and not list(directory.iterdir()):
        directory.rmdir()


@contextmanager
def _migration_locked(reader):
    # Match queue -> source lock order; NB task lock avoids waiting for a worker
    # which may itself need the queue lock to finish.
    from .queue import locked
    with locked(reader) as root, (root / '.lock').open('a+') as task_lock:
        try:
            fcntl.flock(task_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('批量整理源码前请等待后台任务结束') from error
        with _locked(reader):
            yield


def migrate_source_markers(reader):
    """Explicit, idempotent metadata migration; never rewrite code or shared text.

    Do this with the task queue idle: raw fragment hashes intentionally change.
    Mixed old/new files remain readable if migration is interrupted.
    """
    with _migration_locked(reader):
        meta, names = _identity(reader)
        _index(reader, meta, names)
        pending, converted = [], 0
        for path in sorted((reader.directory / 'source/src').glob('*.c')):
            original = _read_text(path)
            prefix = _module_prefix(original, meta)
            blocks = _blocks(original, meta)
            text = original
            for block in reversed(blocks):
                if block.legacy:
                    replacement = begin(block.metadata) + '\n' + block.body + '\n' + end(block.metadata['id'])
                    text = text[:block.start()] + replacement + text[block.end():]
                    converted += 1
            text = _module_header(meta) + text[len(prefix):]
            checked = _blocks(text, meta)
            if [(b.metadata, b.body) for b in checked] != [(b.metadata, b.body) for b in blocks]:
                raise ValueError(f'标记迁移校验失败，保留原文件：{path}')
            if text != original:
                pending.append((path, original, text))
        for path, original, text in pending:
            if _read_text(path) != original:
                raise ValueError(f'迁移期间源码已修改，保留当前文件：{path}')
            _atomic_write(path, text)
        _index(reader, meta, names)
        return {'files': len(pending), 'functions': converted}
