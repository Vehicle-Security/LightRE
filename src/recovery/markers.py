"""Locate reserved source comments without matching literals or comment examples."""

import json
from bisect import bisect_left
from dataclasses import dataclass


def regions(text):
    """Yield comments and literals with their original character offsets."""
    # C line splicing happens before comment/string recognition.
    source, positions = text, range(len(text) + 1)
    if '\\\n' in text or '\\\r\n' in text:
        logical, positions = [], []
        i = 0
        while i < len(text):
            if text.startswith('\\\r\n', i):
                i += 3
            elif text.startswith('\\\n', i):
                i += 2
            else:
                logical.append(text[i])
                positions.append(i)
                i += 1
        source = ''.join(logical)
        positions.append(len(text))
    i = 0
    while i < len(source):
        start = i
        if source.startswith('//', i):
            end = source.find('\n', i)
            i = len(source) if end < 0 else end
            yield 'comment', positions[start], positions[i], source[start:i]
        elif source.startswith('/*', i):
            end = source.find('*/', i + 2)
            if end < 0:
                raise ValueError(f'源码位置 {positions[i]}：块注释未结束')
            i = end + 2
            yield 'comment', positions[start], positions[i], source[start:i]
        elif source.startswith(('R"', 'u8R"', 'uR"', 'UR"', 'LR"'), i):
            quote = source.index('"', i)
            opening = source.find('(', quote + 1, quote + 18)
            delimiter = source[quote + 1:opening] if opening >= 0 else ''
            if opening < 0 or any(c.isspace() or c in '()\\' for c in delimiter):
                raise ValueError(f'源码位置 {positions[i]}：非法原始字符串')
            # Splices inside raw-string contents are literal characters. Find
            # its terminator in the original text, then resume logical scanning.
            closing = text.find(')' + delimiter + '"', positions[opening] + 1)
            if closing < 0:
                raise ValueError(f'源码位置 {positions[i]}：原始字符串未结束')
            i = bisect_left(positions, closing + len(delimiter) + 2)
            yield 'literal', positions[start], positions[i], text[positions[start]:positions[i]]
        elif source[i].isalpha() or source[i] == '_':
            i += 1
            while i < len(source) and (source[i].isalnum() or source[i] == '_'):
                i += 1
        elif source[i].isdigit() or (source[i] == '.' and source[i + 1:i + 2].isdigit()):
            # C++ digit separators are part of a number, not character literals.
            i += 1
            while i < len(source):
                c = source[i]
                if c.isalnum() or c in "._'" or c in '+-' and source[i - 1] in 'eEpP':
                    i += 1
                else:
                    break
        elif source[i] in '\"\'':
            quote = source[i]
            i += 1
            while i < len(source) and source[i] != quote:
                if source[i] == '\n':
                    raise ValueError(f'源码位置 {positions[start]}：字符串或字符未结束')
                i += 2 if source[i] == '\\' else 1
            if i >= len(source):
                raise ValueError(f'源码位置 {positions[start]}：字符串或字符未结束')
            i += 1
            yield 'literal', positions[start], positions[i], text[positions[start]:positions[i]]
        else:
            i += 1


def comments(text):
    """Yield only actual C/C++ comments, skipping all literal contents."""
    for kind, first, last, value in regions(text):
        if kind == 'comment':
            yield first, last, value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'重复 JSON 字段：{key}')
        result[key] = value
    return result


def decode(payload, end=False):
    def invalid(value):
        raise ValueError(f'非法 JSON 常量：{value}')
    data = json.loads(payload, object_pairs_hook=_object, parse_constant=invalid)
    allowed = {'id'} if end else {'v', 'id', 'origins', 'derived_from', 'replaces'}
    required = {'id'} if end else {'v', 'id', 'origins'}
    if not isinstance(data, dict) or not required <= data.keys() or data.keys() - allowed:
        raise ValueError('源码标记字段不符合格式')
    if not isinstance(data['id'], str) or not data['id'].strip():
        raise ValueError('源码 ID 必须为非空字符串')
    if not end:
        if type(data['v']) is not int or data['v'] != 1:
            raise ValueError('不支持的源码标记版本')
        for key in ('origins', 'derived_from', 'replaces'):
            values = data.get(key, [])
            if (not isinstance(values, list) or
                    any(not isinstance(v, str) or not v.strip() for v in values) or
                    len(set(values)) != len(values)):
                raise ValueError(f'非法源码标记数组：{key}')
        origins = data['origins']
        if any(not v.startswith('0x') or not v[2:] or
               any(c not in '0123456789abcdef' for c in v[2:]) for v in origins):
            raise ValueError('origins 必须为小写十六进制 RVA')
        if len({int(v, 16) for v in origins}) != len(origins):
            raise ValueError('重复的来源 RVA')
    return data


def begin(metadata):
    return '// @lightre.begin ' + json.dumps(metadata, ensure_ascii=False, separators=(',', ':')) + '\n'


def end(source_id):
    return '// @lightre.end ' + json.dumps({'id': source_id}, ensure_ascii=False, separators=(',', ':'))


@dataclass
class Block:
    text: str
    first: int
    last: int
    code_last: int
    body_first: int
    metadata: dict
    legacy: bool = False

    def __getitem__(self, key):
        if key == 'rva':
            return self.metadata['origins'][0]
        if key == 'code':
            return self.text[self.first:self.code_last]
        raise KeyError(key)

    def group(self, key=0):
        return self.text[self.first:self.last] if key == 0 else self[key]

    def start(self, key=0):
        return self.first

    def end(self, key=0):
        return self.code_last if key == 'code' else self.last

    @property
    def body(self):
        return self.text[self.body_first:self.code_last]


def parse(text, legacy_header):
    """Read JSON markers and validated legacy wrappers; preserve body bytes as text."""
    blocks, current, ids = [], None, set()
    for first, last, comment in comments(text):
        stripped = comment.rstrip('\r')
        metadata = None
        legacy = False
        if stripped.startswith('// @lightre.'):
            line_start = text.rfind('\n', 0, first) + 1
            if text[line_start:first].strip() or '\n' in text[first:last]:
                raise ValueError(f'源码位置 {first}：定位标记必须独占一行')
            _, _, payload = stripped.partition(' ')
            kind, _, payload = payload.partition(' ')
            if kind not in ('@lightre.begin', '@lightre.end'):
                raise ValueError(f'源码位置 {first}：未知 LightRE 标记')
            try:
                metadata = decode(payload, end=kind == '@lightre.end')
            except ValueError as error:
                raise ValueError(f'源码位置 {first}：{error}') from error
            closing = kind == '@lightre.end'
            first = line_start
        elif stripped.startswith('/* LightRE analysis candidate'):
            fields = dict(line.strip().removeprefix('* ').split(': ', 1)
                          for line in stripped.splitlines() if ': ' in line)
            try:
                rva = int(fields['function_rva'], 16)
            except (KeyError, ValueError) as error:
                raise ValueError('候选的来源注释损坏') from error
            if stripped.replace('\r\n', '\n') != legacy_header(rva).rstrip('\n'):
                raise ValueError('候选的来源注释与当前 SO / RVA 不匹配')
            metadata = {'v': 1, 'id': f'f{rva:08x}', 'origins': [f'{rva:#x}']}
            closing, legacy = False, True
        elif stripped.startswith('/* LightRE end function '):
            if not stripped.endswith(' */'):
                raise ValueError('源码函数边界注释损坏')
            try:
                rva = int(stripped[len('/* LightRE end function '):-3], 16)
            except ValueError as error:
                raise ValueError('源码函数边界注释损坏') from error
            metadata = {'id': f'f{rva:08x}'}
            closing, legacy = True, True
        if metadata is None:
            continue
        if not closing:
            if current or metadata['id'] in ids:
                raise ValueError(f'源码位置 {first}：嵌套或重复源码 ID')
            body_first = last
            if text.startswith('\r\n', body_first):
                body_first += 2
            elif text.startswith('\n', body_first):
                body_first += 1
            # Writers place one empty line between metadata and code.
            if text.startswith('\r\n', body_first):
                body_first += 2
            elif text.startswith('\n', body_first):
                body_first += 1
            current = (first, body_first, metadata, legacy)
        else:
            if not current or current[2]['id'] != metadata['id'] or current[3] != legacy:
                raise ValueError(f'源码位置 {first}：起止标记不匹配')
            code_last = first
            if text[:code_last].endswith('\r\n'):
                code_last -= 2
            elif text[:code_last].endswith('\n'):
                code_last -= 1
            blocks.append(Block(text, current[0], last, code_last, current[1], current[2], legacy))
            ids.add(metadata['id'])
            current = None
    if current:
        raise ValueError('源码函数边界注释不完整，请保留 LightRE 起止标记')
    return blocks
