"""Resolve supported C calls against a source snapshot; navigate by stable RVA."""

import hashlib
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_c

from .candidates import _blocks, _identity, _locked, _read_text, _strip_producer


class SourceChanged(ValueError):
    pass


def _ancestor(node, kinds):
    node = node.parent
    while node is not None:
        if node.type in kinds:
            return node
        node = node.parent
    return None


def _declarator(node):
    while node is not None and node.type != 'identifier':
        if node.type == 'parenthesized_declarator':
            node = node.named_children[0] if node.named_children else None
        else:
            node = node.child_by_field_name('declarator')
    return (node, node is not None and node.parent.type == 'function_declarator')


@lru_cache(maxsize=64)
def _facts(text):
    raw = text.encode()
    root = Parser(Language(tree_sitter_c.language())).parse(raw).root_node
    definitions, calls, shadows, macros, static_prototypes = [], [], [], set(), set()
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.named_children))
        if node.type in ('preproc_def', 'preproc_function_def'):
            name = node.child_by_field_name('name')
            if name:
                macros.add(name.text.decode())
        if node.type == 'function_definition':
            declarator = node.child_by_field_name('declarator')
            name, function = _declarator(declarator)
            if name and function:
                definitions.append({
                    'name': name.text.decode(), 'start': node.start_byte, 'end': node.end_byte,
                    'name_start': name.start_byte, 'name_end': name.end_byte,
                    'static': any(child.type == 'storage_class_specifier' and child.text == b'static'
                                  for child in node.named_children),
                    'supported': not declarator.has_error and _ancestor(node, {
                        'preproc_if', 'preproc_ifdef', 'preproc_else', 'preproc_elif', 'function_definition'}) is None,
                })
        if node.type in ('declaration', 'parameter_declaration', 'type_definition'):
            for declarator in node.children_by_field_name('declarator'):
                name, function = _declarator(declarator)
                if name is None:
                    continue
                # A function-typed parameter is adjusted to a function pointer
                # in C and shadows a global function just like (*callback).
                if function and node.type != 'parameter_declaration':
                    if any(child.type == 'storage_class_specifier' and child.text == b'static'
                           for child in node.named_children):
                        static_prototypes.add(name.text.decode())
                    continue
                scope = _ancestor(node, {'compound_statement', 'for_statement'})
                if node.type == 'parameter_declaration':
                    owner = _ancestor(node, {'function_definition'})
                    scope = owner if owner and node.end_byte <= owner.child_by_field_name('body').start_byte else node.parent
                scope = scope or root
                shadows.append((name.text.decode(), max(node.start_byte, scope.start_byte), scope.end_byte))
        if node.type == 'call_expression':
            name = node.child_by_field_name('function')
            if name is None or name.type != 'identifier':
                continue
            owner = _ancestor(node, {'function_definition'})
            supported = bool(owner and not owner.has_error and not node.has_error
                             and _ancestor(node, {'preproc_if', 'preproc_ifdef', 'preproc_else', 'preproc_elif'}) is None)
            calls.append((name.text.decode(), name.start_byte, name.end_byte, supported))
    return definitions, calls, shadows, macros, static_prototypes


@lru_cache(maxsize=4)
def _graph(files, catalog, source_path, sha256):
    meta = {'source_path': source_path, 'sha256': sha256}
    entities, fragments, native_names, occupied, ids = [], {}, defaultdict(list), set(), set()
    parsed = {}
    header_names = set()
    for path, text in files:
        facts = _facts(text)
        parsed[path] = facts
        if path.endswith('.h'):
            header_names.update(facts[3])
            header_names.update(item['name'] for item in facts[0] if item['static'])
            continue
        blocks = _blocks(text, meta)
        raw = text.encode()
        for block in blocks:
            rva = int(block['rva'], 16)
            if rva in fragments or block.metadata['id'] in ids:
                raise ValueError('源码定位包含重复 RVA 或源码 ID')
            ids.add(block.metadata['id'])
            occupied.add(rva)
            start = len(text[:block.body_first].encode())
            finish = len(text[:block.code_last].encode())
            display = _strip_producer(block.body)
            visible_start = finish - len(display.encode())
            definitions = [item for item in facts[0] if start <= item['start'] and item['end'] <= finish]
            fragments[rva] = {'path': path, 'source_id': block.metadata['id'], 'source_name': None,
                              'sha256': hashlib.sha256(block['code'].encode()).hexdigest(),
                              'display_start': visible_start, 'display_end': finish, 'raw': raw}
            if len(definitions) == 1 and definitions[0]['supported']:
                fragments[rva]['source_name'] = definitions[0]['name']
                entities.append({**definitions[0], 'path': path, 'rva': rva,
                                 'source_id': block.metadata['id']})
        # Unmarked helpers and unsupported declarations must also block an
        # otherwise same-named global; do not navigate to a different function.
        for definition in facts[0]:
            if not any(item['path'] == path and item['start'] == definition['start'] for item in entities):
                entities.append({**definition, 'path': path, 'rva': None, 'source_id': None})
    for rva, name in catalog:
        native_names[name].append(rva)
    local, global_names = defaultdict(list), defaultdict(list)
    for entity in entities:
        (local[(entity['path'], entity['name'])] if entity['static'] else global_names[entity['name']]).append(entity)
    result = {}
    for rva, fragment in fragments.items():
        path = fragment['path']
        _, calls, shadows, macros, static_prototypes = parsed[path]
        references, unresolved = [], []
        for name, first, last, supported in calls:
            if not fragment['display_start'] <= first < last <= fragment['display_end']:
                continue
            reason, target, source_id = None, None, None
            if not supported:
                reason = 'unsupported_syntax'
            elif name in macros or name in header_names:
                reason = 'macro'
            elif any(symbol == name and start <= first < end for symbol, start, end in shadows):
                reason = 'shadowed'
            else:
                candidates = local.get((path, name), [])
                if not candidates and name not in static_prototypes:
                    candidates = global_names.get(name, [])
                if len(candidates) == 1 and candidates[0]['supported'] and candidates[0]['rva'] is not None:
                    target, source_id = candidates[0]['rva'], candidates[0]['source_id']
                elif candidates or name in static_prototypes:
                    reason = 'ambiguous_or_unmapped'
                elif len(native_names.get(name, [])) == 1 and native_names[name][0] not in occupied:
                    target = native_names[name][0]
                else:
                    reason = 'unresolved'
            raw = fragment['raw']
            start = len(raw[fragment['display_start']:first].decode().encode('utf-16-le')) // 2
            end = len(raw[fragment['display_start']:last].decode().encode('utf-16-le')) // 2
            item = {'start': start, 'end': end, 'name': name}
            if target is None:
                unresolved.append({**item, 'reason': reason})
            else:
                references.append({**item, 'target_rva': target, 'target_source_id': source_id})
        result[rva] = {'source_id': fragment['source_id'], 'source_name': fragment['source_name'],
                       'references': references, 'unresolved_references': unresolved,
                       'reference_offset_encoding': 'utf-16', '_sha256': fragment['sha256'],
                       '_path': fragment['path']}
    return result


def source_navigation(reader, candidate):
    empty = {'source_id': None, 'source_name': None, 'references': [],
             'unresolved_references': [], 'reference_offset_encoding': 'utf-16'}
    if not candidate['code'] or Path(candidate['path']).parent.name == 'candidates':
        return empty
    with _locked(reader):
        meta, names = _identity(reader)
        files = _snapshot(reader)
        graph = _graph(files, tuple(sorted(names.items())), meta['source_path'], meta['sha256'])
        item = graph.get(candidate['rva'])
        if (item is None or item['_sha256'] != candidate['sha256']
                or reader.directory / item['_path'] != Path(candidate['path'])):
            raise SourceChanged('源码在读取期间更新，请重新读取')
        return {key: value for key, value in item.items() if not key.startswith('_')}


def assembly_navigation(reader, page):
    known = {fn['rva'] for fn in reader.catalog()['functions']}
    with reader._connect() as conn:
        ranges = json.loads(conn.execute('SELECT ranges FROM functions WHERE rva=?', (page['rva'],)).fetchone()[0])
    for instruction in page['instructions']:
        try:
            raw = bytes.fromhex(instruction['bytes'])
        except ValueError:
            continue
        if len(raw) != 4:
            continue
        word = int.from_bytes(raw, 'little')
        operation = word & 0xfc000000
        if operation not in (0x94000000, 0x14000000):
            continue
        immediate = word & 0x03ffffff
        if immediate & 0x02000000:
            immediate -= 0x04000000
        target = instruction['rva'] + immediate * 4
        if target not in known:
            continue
        if operation == 0x14000000 and any(start <= target < end for start, end in ranges):
            continue
        instruction['target_rva'] = target
        instruction['reference_kind'] = 'call' if operation == 0x94000000 else 'tail_call'
    return page


def function_name(code):
    """Return the single supported definition name, independently of its RVA."""
    definitions = _facts(code or '')[0]
    return definitions[0]['name'] if len(definitions) == 1 and definitions[0]['supported'] else None


def code_tokens(code):
    """Compare code changes without counting formatting or comments as improvement."""
    root = Parser(Language(tree_sitter_c.language())).parse((code or '').encode()).root_node
    tokens, stack = [], [root]
    while stack:
        node = stack.pop()
        if node.type == 'comment':
            continue
        if node.children:
            stack.extend(reversed(node.children))
        else:
            tokens.append((node.type, node.text))
    return tuple(tokens)


def _snapshot(reader):
    paths = set((reader.directory / 'source/src').glob('*.c'))
    paths.update((reader.directory / 'source').rglob('*.h'))
    return tuple((str(path.relative_to(reader.directory)), _read_text(path)) for path in sorted(paths))


def source_names(reader):
    """Expose current names without changing the native catalog or function identity."""
    with _locked(reader):
        files = _snapshot(reader)
        if not files:
            return {}
        meta, names = _identity(reader)
        graph = _graph(files, tuple(sorted(names.items())), meta['source_path'], meta['sha256'])
        return {rva: item['source_name'] for rva, item in graph.items() if item['source_name']}


def _replace_bytes(text, edits):
    raw = text.encode()
    for first, last, replacement in sorted(edits, reverse=True):
        raw = raw[:first] + replacement.encode() + raw[last:]
    return raw.decode()


def synchronize_calls(reader, renames):
    """Retarget provable direct calls after all model batches have been published.

    Reconstruct old definition names only for binding resolution, then check
    every proposed replacement against the actual new definitions. Unsupported
    syntax, macros and shadowed/ambiguous symbols never receive text replacement.
    """
    from .candidates import _atomic_write
    report = {'updated_calls': 0, 'skipped': [], 'scope': 'direct_calls'}
    with _locked(reader):
        meta, names = _identity(reader)
        files = _snapshot(reader)
        catalog = tuple(sorted(names.items()))
        graph = _graph(files, catalog, meta['source_path'], meta['sha256'])
        active = {int(rva): item for rva, item in renames.items()
                  if int(rva) in graph and graph[int(rva)]['source_name'] == item['new']
                  and item['old'] != item['new']}
        virtual = []
        for path, text in files:
            edits = []
            for rva, rename in active.items():
                if graph[rva]['_path'] != path:
                    continue
                block = next(b for b in _blocks(text, meta) if int(b['rva'], 16) == rva)
                start, finish = len(text[:block.body_first].encode()), len(text[:block.code_last].encode())
                definition = next(d for d in _facts(text)[0] if start <= d['start'] < d['end'] <= finish)
                edits.append((definition['name_start'], definition['name_end'], rename['old']))
            virtual.append((path, _replace_bytes(text, edits)))
        previous = _graph(tuple(virtual), catalog, meta['source_path'], meta['sha256'])
        edits_by_path, expected = defaultdict(list), []
        for rva, item in previous.items():
            # Definition-name lengths may differ in the virtual snapshot. Call
            # ordinals are stable, whereas absolute token offsets are not.
            old_calls = sorted(item['references'] + item['unresolved_references'], key=lambda ref: ref['start'])
            actual = graph[rva]
            actual_calls = sorted(actual['references'] + actual['unresolved_references'], key=lambda ref: ref['start'])
            if len(old_calls) != len(actual_calls):
                continue
            path = actual['_path']
            text = dict(files)[path]
            block = next(b for b in _blocks(text, meta) if int(b['rva'], 16) == rva)
            display = _strip_producer(block.body)
            display_start = len(text[:block.code_last].encode()) - len(display.encode())
            utf16 = display.encode('utf-16-le')
            for ordinal, (old, current) in enumerate(zip(old_calls, actual_calls)):
                target = old.get('target_rva')
                rename = active.get(target)
                if not rename or old['name'] != rename['old'] or current['name'] != rename['old']:
                    continue
                # An already resolved current binding belongs to another entity.
                if current.get('target_rva') not in (None, target):
                    report['skipped'].append({'rva': rva, 'name': current['name'], 'reason': 'binding_changed'})
                    continue
                first = display_start + len(utf16[:current['start'] * 2].decode('utf-16-le').encode())
                last = display_start + len(utf16[:current['end'] * 2].decode('utf-16-le').encode())
                edits_by_path[path].append((first, last, rename['new']))
                expected.append((rva, ordinal, target, rename['new']))
        proposed = tuple((path, _replace_bytes(text, edits_by_path[path])) for path, text in files)
        checked = _graph(proposed, catalog, meta['source_path'], meta['sha256'])
        for rva, ordinal, target, name in expected:
            item = checked[rva]
            calls = sorted(item['references'] + item['unresolved_references'], key=lambda ref: ref['start'])
            if ordinal >= len(calls) or calls[ordinal].get('target_rva') != target or calls[ordinal]['name'] != name:
                report['skipped'].append({'rva': rva, 'name': name, 'reason': 'new_binding_not_unique'})
        if report['skipped']:
            # Keep this synchronization transaction unchanged if any replacement
            # would bind to a shadow, macro, static helper or another function.
            report['reason'] = 'binding_check_failed'
            return report
        for path, text in proposed:
            original = dict(files)[path]
            if text != original:
                _atomic_write(reader.directory / path, text)
        report['updated_calls'] = len(expected)
        # Report unresolved old direct calls, rather than claiming full rename.
        for rva, item in previous.items():
            for ref in item['unresolved_references']:
                if any(ref['name'] == rename['old'] for rename in active.values()):
                    report['skipped'].append({'rva': rva, 'name': ref['name'], 'reason': ref['reason']})
    return report
