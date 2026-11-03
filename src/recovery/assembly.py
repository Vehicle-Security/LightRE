"""Complete inline assembly inputs with file snapshots for reproducibility."""

import json
import re
import shutil
import shlex
import sys
from pathlib import Path

from lightre.prompt import render_prompt
from lightre.paths import SOURCE_DIR
from .knowledge import read_knowledge


PROMPTS = Path(__file__).parent / 'prompts'


def _inline_instruction(op, image_base):
    opcode = re.sub(r',\s+', ',', op['opcode'])
    mnemonic = opcode.split()[0]
    if mnemonic in ('b', 'bl', 'cbz', 'cbnz', 'tbz', 'tbnz') or mnemonic.startswith('b.'):
        target = re.search(r'0x[0-9a-fA-F]+$', opcode)
        if target and int(target[0], 16) >= image_base:
            opcode = opcode[:target.start()] + hex(int(target[0], 16) - image_base)
    return f"{op['rva']:x} {opcode}"


def assembly_prompt(reader, state, prefix, batch):
    sample = reader.catalog()['sample']
    image_base = sample.get('base', 0)
    image_base = int(image_base, 0) if isinstance(image_base, str) else image_base
    inputs, sections = [], []
    for fn in batch:
        rva = fn['rva']
        with reader._connect() as conn:
            ranges = json.loads(conn.execute('SELECT ranges FROM functions WHERE rva=?', (rva,)).fetchone()[0])
        path = prefix.parent / f'{prefix.name}-{rva:#010x}.asm'
        count, offset, instructions = 0, 0, []
        with path.open('w', encoding='utf-8') as stream:
            stream.write(render_prompt(PROMPTS / 'ASSEMBLY_HEADER.md', RVA=hex(rva),
                                       SHA256=sample.get('sha256'), IMAGE_BASE=sample.get('base', '0'),
                                       RANGES=json.dumps(ranges)))
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
        inputs.append({**fn, 'assembly': str(path), 'instructions': count,
                       'ranges': ranges, 'knowledge': str(knowledge)})
        sections.append(render_prompt(PROMPTS / 'ASSEMBLY_SECTION.md', RVA=hex(rva),
                                      KNOWLEDGE=knowledge_text, COUNT=count,
                                      INSTRUCTIONS='\n'.join(instructions)).rstrip('\n'))
    return render_prompt(PROMPTS / 'ASSEMBLY.md',
        TASK=prefix.parent / 'task.md',
        SHA256=sample.get('sha256'),
        BINARY=reader.directory / 'binary/original.so',
        IMAGE_BASE=f'{image_base:#x}',
        SOURCE_DIR=SOURCE_DIR,
        OBJDUMP=shutil.which('aarch64-linux-gnu-objdump'),
        READELF=shutil.which('aarch64-linux-gnu-readelf'),
        INPUTS=json.dumps(inputs, ensure_ascii=False),
        COMMENT=state.get('comment'),
        PYTHON=shlex.quote(sys.executable),
        DIRECTORY=f'{str(reader.directory)!r}',
        SECTIONS=chr(10).join(sections))
