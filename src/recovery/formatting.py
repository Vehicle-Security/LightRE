"""Format generated source before publication, with the same style on disk."""

import re
import shutil
import subprocess
from xml.etree import ElementTree

from lightre.paths import CLANG_FORMAT
from .candidates import (
    _atomic_write, _blocks, _identity, _index, _migration_locked, _read_text, _strip_producer,
)
from .markers import regions


class FormattingError(RuntimeError):
    pass


def _close_signature_gaps(code):
    """clang-format 18 preserves blank lines before braces; remove code-only gaps.

    Protect comments and literals (including raw strings) with the shared lexer.
    Keep one newline so preprocessor line boundaries remain intact.
    """
    protected = iter((first, last) for _, first, last, _ in regions(code))
    region = next(protected, None)
    edits = []
    for match in re.finditer(r'(?<=\))[ \t]*\r?\n(?:[ \t]*\r?\n)+(?=[ \t]*\{)', code):
        while region and region[1] <= match.start() - 1:
            region = next(protected, None)
        if region is None or region[0] >= match.end():
            edits.append((match.start(), match.end()))
    for first, last in reversed(edits):
        code = code[:first] + '\n' + code[last:]
    return code


def format_code(reader, code):
    executable = shutil.which('clang-format')
    if executable is None:
        raise FormattingError('未安装 clang-format，请在部署环境中安装运行时依赖')
    style = reader.directory / 'source/.clang-format'
    if not style.exists():
        style.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(style, _read_text(CLANG_FORMAT))
    try:
        prepared = _close_signature_gaps(code).lstrip('\r\n').rstrip('\r\n') + '\n'
        result = subprocess.run(
            [executable, f'--style=file:{style}', '--assume-filename=recovered.c', '--output-replacements-xml'],
            input=prepared, encoding='utf-8', capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise FormattingError(f'源码格式化失败：{error}') from error
    if result.returncode or not result.stdout.strip():
        raise FormattingError(f'clang-format 未完成：{result.stderr.strip()[:1000]}')
    try:
        replacements = ElementTree.fromstring(result.stdout)
        if replacements.tag != 'replacements' or replacements.get('incomplete_format') != 'false':
            raise ValueError('当前草稿无法完整格式化')
        output = prepared.encode('utf-8')
        previous = len(output)
        for edit in reversed(list(replacements)):
            offset, length = int(edit.attrib['offset']), int(edit.attrib['length'])
            if edit.tag != 'replacement' or offset < 0 or length < 0 or offset + length > previous:
                raise ValueError('非法格式化替换范围')
            output = output[:offset] + (edit.text or '').encode('utf-8') + output[offset + length:]
            previous = offset
        return output.decode('utf-8')
    except (ElementTree.ParseError, ValueError, KeyError) as error:
        raise FormattingError(f'格式化结果无效：{error}') from error


def format_sources(reader):
    """Explicitly format existing modules only while the source task queue is idle."""
    with _migration_locked(reader):
        meta, names = _identity(reader)
        _index(reader, meta, names)
        pending, functions = [], 0
        for path in sorted((reader.directory / 'source/src').glob('*.c')):
            original = _read_text(path)
            before = _blocks(original, meta)
            formatted = format_code(reader, original)
            after = _blocks(formatted, meta)
            identity = lambda blocks: [(b.metadata, _strip_producer(b.body) != b.body) for b in blocks]
            if identity(before) != identity(after):
                raise FormattingError(f'格式化改变了源码定位标记或生产者，保留原文件：{path}')
            functions += len(before)
            if formatted != original:
                pending.append((path, original, formatted))
        for path, original, formatted in pending:
            if _read_text(path) != original:
                raise FormattingError(f'格式化期间源码已修改，保留当前文件：{path}')
            _atomic_write(path, formatted)
        _index(reader, meta, names)
        return {'files': len(pending), 'functions': functions}
