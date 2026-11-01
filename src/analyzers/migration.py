"""Preserve old work when an explicit export changes function boundaries."""

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from recovery.candidates import _atomic_write, _blocks, _locked, _module_prefix, _read_text

_REFS = ('functions.json', 'assembly.jsonl', 'state.json', 'boundaries.json')


def _restore(directory, archive):
    with sqlite3.connect(archive / 'index.sqlite') as source, sqlite3.connect(directory / 'index.sqlite') as destination:
        source.backup(destination)
    for name in ('source', 'candidates'):
        current, saved = directory / name, archive / name
        if current.exists():
            shutil.rmtree(current)
        if saved.exists():
            shutil.copytree(saved, current)
    current, saved = directory / 'knowledge.md', archive / 'knowledge.md'
    if saved.exists():
        _atomic_write(current, saved.read_text(encoding='utf-8'))
    else:
        current.unlink(missing_ok=True)
    for name in _REFS:
        current, saved = directory / 'references/ghidra' / name, archive / 'references' / name
        if saved.exists():
            shutil.copy2(saved, current)
        else:
            current.unlink(missing_ok=True)


def recover_catalog(directory):
    marker = directory / '.catalog-migration.json'
    if marker.exists():
        archive = directory / json.loads(marker.read_text())['archive']
        with _locked(SimpleNamespace(directory=directory)):
            _restore(directory, archive)
            marker.unlink()


def _changes(previous, data):
    incoming = {fn['rva']: fn for fn in data['functions']}
    changes = []
    for rva, fn in previous.items():
        if rva in incoming:
            if fn['ranges'] != incoming[rva]['ranges']:
                changes.append({'old_rva': rva, 'new_rva': rva, 'kind': 'range_changed',
                                'old_ranges': fn['ranges'], 'new_ranges': incoming[rva]['ranges']})
            continue
        owners = [new['rva'] for new in incoming.values()
                  if any(start <= rva < end for start, end in new['ranges'])]
        section = next((s for s in data.get('sections', []) if s['start'] <= rva < s['end']), {})
        kind = 'contained' if len(owners) == 1 else 'data' if section.get('executable') is False else 'unmapped'
        changes.append({'old_rva': rva, 'new_rva': owners[0] if len(owners) == 1 else None,
                        'kind': kind, 'owners': owners, 'section': section.get('name'), 'old_ranges': fn['ranges']})
    return changes


def publish_catalog(directory, data, assembly, identity):
    from .ghidra import _publish
    from workspace import WorkspaceReader
    from recovery import read_knowledge, save_knowledge_batch
    reader = SimpleNamespace(directory=directory)
    database = directory / 'index.sqlite'
    with _locked(reader):
        with sqlite3.connect(database) as conn:
            meta = dict(conn.execute('SELECT key,value FROM metadata'))
            previous = {rva: {'ranges': json.loads(ranges)} for rva, ranges in conn.execute('SELECT rva,ranges FROM functions')}
        changes = _changes(previous, data)
        marker = directory / '.catalog-migration.json'
        archive = None
        if previous:
            name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-catalog-' + uuid4().hex[:6]
            archive = directory / 'history' / name
            archive.mkdir(parents=True)
            with sqlite3.connect(database) as source, sqlite3.connect(archive / 'index.sqlite') as destination:
                source.backup(destination)
            for name in ('source', 'candidates'):
                if (directory / name).exists():
                    shutil.copytree(directory / name, archive / name)
            if (directory / 'knowledge.md').exists():
                shutil.copy2(directory / 'knowledge.md', archive / 'knowledge.md')
            (archive / 'references').mkdir()
            for name in _REFS:
                if (directory / 'references/ghidra' / name).exists():
                    shutil.copy2(directory / 'references/ghidra' / name, archive / 'references' / name)
            _atomic_write(marker, json.dumps({'archive': str(archive.relative_to(directory))}))
        try:
            retired = {item['old_rva'] for item in changes}
            incoming = {fn['rva'] for fn in data['functions']}
            for path in (directory / 'source/src').glob('*.c'):
                text = _read_text(path)
                prefix = _module_prefix(text, meta)
                matches = _blocks(text, meta)
                for match in reversed(matches):
                    rva = int(match['rva'], 16)
                    if rva in retired or rva not in incoming:
                        text = text[:match.start()] + text[match.end():]
                if text.strip() == prefix.strip():
                    path.unlink()
                elif text != _read_text(path):
                    _atomic_write(path, text)
            for path in (directory / 'candidates').glob('0x*.c'):
                if int(path.stem, 16) in retired or int(path.stem, 16) not in incoming:
                    path.unlink()
            with sqlite3.connect(database) as conn:
                conn.execute('PRAGMA foreign_keys=ON')
                _publish(conn, data, assembly, identity)
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='reviews'").fetchone():
                    conn.executemany('DELETE FROM reviews WHERE function_rva=?', [(rva,) for rva in retired])
            root = directory / 'references/ghidra'
            root.mkdir(parents=True, exist_ok=True)
            mapping_path = root / 'boundaries.json'
            old_mapping = json.loads(mapping_path.read_text()).get('changes', []) if mapping_path.exists() else []
            mapping = {item['old_rva']: item for item in old_mapping}
            for item in changes:
                item['archive'] = str(archive.relative_to(directory)) if archive else None
                mapping[item['old_rva']] = item
            _atomic_write(mapping_path, json.dumps({'sha256': data['sha256'], 'changes': list(mapping.values())}, ensure_ascii=False, indent=2))
            reader = WorkspaceReader(directory)
            links = {}
            for item in changes:
                owner = item['new_rva']
                if owner is not None:
                    links.setdefault(owner, []).append(
                        f"- 原条目 `{item['old_rva']:#x}`：{item['kind']}。旧代码与评价保存在 `{item['archive']}`；"
                        f"旧知识仍可用 read_knowledge(reader, {item['old_rva']:#x}) 读取。")
            updates = {rva: read_knowledge(reader, rva) + '\n\n### Ghidra 边界调整\n' + '\n'.join(notes)
                       for rva, notes in links.items()}
            if updates:
                save_knowledge_batch(reader, updates)
            marker.unlink(missing_ok=True)
        except BaseException:
            if archive:
                _restore(directory, archive)
                marker.unlink(missing_ok=True)
            raise
