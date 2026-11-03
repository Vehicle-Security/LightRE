"""One editable knowledge.md per SO, indexed by Markdown H2 function RVAs."""

import fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from markdown_it import MarkdownIt
from lightre.prompt import render_prompt

from .candidates import _atomic_write

MARKDOWN = MarkdownIt('commonmark')
_MEMORY_LOCK = RLock()
_DOCUMENTS = {}


@dataclass
class _Document:
    signature: tuple | None
    lines: list
    sections: dict
    notes: dict


def _signature(path):
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _remember(path, signature, lines, sections):
    document = _Document(signature, lines, sections,
                         {rva: _body(lines, section) for rva, section in sections.items()})
    _DOCUMENTS[path] = document
    return document


def _load(path):
    signature = _signature(path)
    document = _DOCUMENTS.get(path)
    if document is None or document.signature != signature:
        lines, sections = _sections(path.read_text(encoding='utf-8') if signature else '')
        # Keep the signature observed before reading. An external replacement
        # during parsing must trigger another reload on the next access.
        document = _remember(path, signature, lines, sections)
    return document


@contextmanager
def _locked(reader):
    # flock coordinates processes; RLock also serializes threads and the parser.
    with _MEMORY_LOCK, (reader.directory / '.knowledge.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield reader.directory.resolve() / 'knowledge.md'


def _document(reader):
    with _MEMORY_LOCK:
        path = reader.directory.resolve() / 'knowledge.md'
        document = _DOCUMENTS.get(path)
        if document is not None and document.signature == _signature(path):
            return document
        with _locked(reader):
            _migrate(reader, path)
            return _load(path)


def _sections(text):
    """Use source line maps; fenced code and nested headings are not boundaries."""
    lines = text.splitlines(keepends=True)
    tokens = MARKDOWN.parse(text)
    boundaries = []
    for index, token in enumerate(tokens):
        if token.type != 'heading_open' or token.level != 0 or token.tag not in ('h1', 'h2'):
            continue
        rva = None
        title = tokens[index + 1].content.split(maxsplit=1)
        if token.tag == 'h2' and title:
            key = title[0].strip('`')
            if key.lower().startswith('0x'):
                try:
                    rva = int(key, 16)
                except ValueError:
                    pass
        boundaries.append((token.map[0], token.map[1], rva))
    sections = {}
    for index, (start, body, rva) in enumerate(boundaries):
        if rva is None:
            continue
        if rva in sections:
            raise ValueError(f'knowledge.md 包含重复函数标题：{rva:#x}')
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(lines)
        sections[rva] = (start, body, end)
    return lines, sections


def _body(lines, section):
    return ''.join(lines[section[1]:section[2]]).strip('\r\n')


def _normalize(notes):
    """Keep function notes under H2 without letting their headings split entries."""
    notes = notes.strip('\r\n')
    tokens = MARKDOWN.parse(notes)
    headings = [(token, tokens[index + 1].content) for index, token in enumerate(tokens)
                if token.type == 'heading_open' and token.level == 0]
    shift = max(0, 3 - min((int(token.tag[1]) for token, _ in headings), default=3))
    if not shift:
        return notes
    lines = notes.splitlines(keepends=True)
    for token, title in reversed(headings):
        level = min(6, int(token.tag[1]) + shift)
        lines[token.map[0]:token.map[1]] = ['#' * level + ' ' + title.replace('\n', ' ') + '\n']
    return ''.join(lines).strip('\r\n')


def _header(reader):
    sample = reader.catalog()['sample']
    return render_prompt(Path(__file__).parent / 'prompts/KNOWLEDGE_HEADER.md',
                         BINARY=Path(sample['source_path']).name, SHA256=sample['sha256'])


def _entry(rva, notes):
    return f'## {rva:#010x}\n\n{notes}\n\n'


def _validate(text, expected):
    # A temporary end heading also exposes an unclosed fence/HTML block in the
    # final entry; it is only used for validation, never written to the file.
    lines, sections = _sections(text + '\n# LightRE document end\n')
    if set(sections) != set(expected):
        raise ValueError('知识 Markdown 的章节边界不完整，请检查未闭合的代码块或 HTML')
    for rva, notes in expected.items():
        if _body(lines, sections[rva]) != notes:
            raise ValueError(f'知识 Markdown 的函数正文边界异常：{rva:#x}')
    # Reuse this parse in memory, excluding the temporary end marker.
    lines = text.splitlines(keepends=True)
    return lines, {rva: (start, body, min(end, len(lines)))
                   for rva, (start, body, end) in sections.items()}


def _migrate(reader, path):
    legacy = sorted((reader.directory / 'knowledge').glob('0x*.md'))
    if not legacy:
        return 0
    text = path.read_text(encoding='utf-8') if path.exists() else _header(reader)
    lines, sections = _sections(text)
    existing = {rva: _body(lines, section) for rva, section in sections.items()}
    additions = {}
    for old in legacy:
        rva = int(old.stem, 16)
        notes = _normalize(old.read_text(encoding='utf-8'))
        previous = existing.get(rva, additions.get(rva))
        if previous is not None and previous != notes:
            raise ValueError(f'知识迁移内容冲突，保留两份供检查：{old}')
        if rva not in existing:
            additions[rva] = notes
    if additions:
        text = text.rstrip('\r\n') + '\n\n' + ''.join(_entry(rva, additions[rva]) for rva in sorted(additions))
        _validate(text, {**existing, **additions})
        _atomic_write(path, text)
    # Verify the committed document before removing old function files.
    lines, sections = _sections(path.read_text(encoding='utf-8'))
    for old in legacy:
        if _body(lines, sections[int(old.stem, 16)]) != _normalize(old.read_text(encoding='utf-8')):
            raise ValueError(f'知识迁移校验失败，保留旧文件：{old}')
    for old in legacy:
        old.unlink()
    directory = reader.directory / 'knowledge'
    if not any(directory.iterdir()):
        directory.rmdir()
    return len(legacy)


def migrate_knowledge(reader):
    """Consolidate legacy per-function files; identical partial migrations resume."""
    with _locked(reader) as path:
        return _migrate(reader, path)


def read_knowledge(reader, rva):
    return _document(reader).notes.get(rva, '')


def knowledge_rvas(reader):
    return set(_document(reader).notes)


def save_knowledge(reader, rva, notes):
    """Save one manual update immediately; analysis uses save_knowledge_batch."""
    return save_knowledge_batch(reader, {rva: notes})


def save_knowledge_batch(reader, updates):
    """Publish a completed batch with one atomic write, retaining other sections."""
    with _locked(reader) as path:
        _migrate(reader, path)
        document = _load(path)
        updates = {rva: _normalize(notes) for rva, notes in updates.items()}
        changes = {rva: notes for rva, notes in updates.items() if document.notes.get(rva) != notes}
        if not changes:
            return path
        lines = document.lines.copy() if document.signature else _header(reader).splitlines(keepends=True)
        sections = document.sections
        edits = []
        for rva, notes in changes.items():
            if rva in sections:
                start, _, end = sections[rva]
            else:
                start = end = next((section[0] for address, section in sorted(sections.items()) if address > rva),
                                   max((section[2] for section in sections.values()), default=len(lines)))
            separator = '\n' if start and lines[start - 1].strip('\r\n') else ''
            edits.append((start, end, rva, separator + _entry(rva, notes)))
        # Reverse edits keep original line positions valid. At a shared position,
        # replace an existing entry before inserting new RVAs in ascending order.
        for start, end, _, entry in sorted(edits, reverse=True):
            lines[start:end] = [entry]
        updated = ''.join(lines)
        lines, sections = _validate(updated, {**document.notes, **changes})
        _atomic_write(path, updated)
        _remember(path, _signature(path), lines, sections)
        return path
