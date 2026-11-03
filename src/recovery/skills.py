"""Snapshot the internal recovery method and its reference for each CLI batch."""

import hashlib
import json
import re
from pathlib import Path

import yaml


SKILLS = Path(__file__).resolve().parent / 'skills'
SKILL_NAME = 'lightre-recovery'


def snapshot_skill(prefix):
    """Return metadata for the entry the CLI can read from this batch snapshot."""
    source = SKILLS / SKILL_NAME
    documents = {path.relative_to(source): path.read_bytes() for path in sorted(source.rglob('*.md'))}
    if Path('SKILL.md') not in documents:
        raise FileNotFoundError(source / 'SKILL.md')
    text = documents[Path('SKILL.md')].decode('utf-8')
    frontmatter = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', text, re.DOTALL)
    if frontmatter is None:
        raise ValueError(f'{source}/SKILL.md: 缺少 Skill frontmatter')
    try:
        metadata = yaml.safe_load(frontmatter[1])
    except yaml.YAMLError as error:
        raise ValueError(f'{source}/SKILL.md: Skill frontmatter 无效：{error}') from error
    if (not isinstance(metadata, dict) or metadata.get('name') != SKILL_NAME
            or not isinstance(metadata.get('description'), str) or not metadata['description'].strip()):
        raise ValueError(f'{source}/SKILL.md: Skill 名称或描述无效')
    snapshot = Path(prefix).with_suffix('.skills').resolve()
    files = []
    for path, content in documents.items():
        relative = Path(SKILL_NAME) / path
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        files.append({'path': relative.as_posix(), 'sha256': hashlib.sha256(content).hexdigest()})
    entry = {'name': SKILL_NAME, 'description': metadata['description'],
             'path': str(snapshot / SKILL_NAME / 'SKILL.md'), 'files': files}
    Path(prefix).with_suffix('.skills.json').write_text(
        json.dumps({'version': 1, 'skills': [entry]}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return entry
