"""Complete inline assembly inputs with file snapshots for reproducibility."""

import json
import re

from .knowledge import read_knowledge


def _inline_instruction(op, image_base):
    opcode = re.sub(r',\s+', ',', op['opcode'])
    mnemonic = opcode.split()[0]
    if mnemonic in ('b', 'bl', 'cbz', 'cbnz', 'tbz', 'tbnz') or mnemonic.startswith('b.'):
        target = re.search(r'0x[0-9a-fA-F]+$', opcode)
        if target and int(target[0], 16) >= image_base:
            opcode = opcode[:target.start()] + hex(int(target[0], 16) - image_base)
    return f"{op['rva']:x} {opcode}"


def assembly_inputs(reader, prefix, batch):
    sample = reader.catalog()['sample']
    image_base = sample.get('base', 0)
    image_base = int(image_base, 0) if isinstance(image_base, str) else image_base
    inputs = []
    for fn in batch:
        rva = fn['rva']
        with reader._connect() as conn:
            ranges = json.loads(conn.execute('SELECT ranges FROM functions WHERE rva=?', (rva,)).fetchone()[0])
        path = prefix.parent / f'{prefix.name}-{rva:#010x}.asm'
        count, offset, instructions = 0, 0, []
        with path.open('w', encoding='utf-8') as stream:
            stream.write('# ' + json.dumps({'rva': hex(rva), 'sha256': sample.get('sha256'),
                                            'image_base': image_base, 'ranges': ranges}) + '\n')
            while True:
                page = reader.assembly(rva, offset=offset, limit=200)
                for op in page['instructions']:
                    stream.write(f"{op['rva']:08x}  {op['bytes']}  {op['opcode']}\n")
                    # The full opcode and address go directly into the prompt.
                    # Redundant machine bytes remain in the durable snapshot.
                    instructions.append(_inline_instruction(op, image_base))
                count += len(page['instructions'])
                if page['next_offset'] is None:
                    break
                offset = page['next_offset']
        if count != fn['instructions']:
            raise ValueError(f'汇编快照指令数与函数目录不符：{rva:#x}')
        knowledge = path.with_suffix('.knowledge.md')
        knowledge_text = read_knowledge(reader, rva)
        knowledge.write_text(knowledge_text, encoding='utf-8')
        inputs.append({'rva': hex(rva), 'instructions': count, 'knowledge': knowledge_text,
                       'assembly_text': '\n'.join(instructions),
                       'snapshot': {'assembly': str(path), 'ranges': ranges, 'knowledge': str(knowledge)}})
    return inputs
