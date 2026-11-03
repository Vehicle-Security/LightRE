"""Snapshot internal method documents and inject their catalogue per batch."""

import hashlib
import json
import re
from pathlib import Path

import yaml

from lightre.prompt import render_prompt


SKILLS = Path(__file__).resolve().parent / 'skills'
CATALOG = Path(__file__).resolve().parent / 'prompts/SKILLS.md'


def skill_catalog(prefix, *, assembly_only=False, feedback=False):
    """Return a small catalogue; the CLI reads selected snapshot files itself."""
    names = ['lightre-recover-assembly']
    if not assembly_only:
        names.append('lightre-refine-candidate')
    if feedback:
        names.append('lightre-apply-feedback')
    snapshot = Path(prefix).with_suffix('.skills').resolve()
    entries = []
    for name in names:
        source = SKILLS / name
        documents = {path.relative_to(source): path.read_bytes()
                     for path in sorted(source.rglob('*.md'))}
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
        if (not isinstance(metadata, dict) or metadata.get('name') != name
                or not isinstance(metadata.get('description'), str)
                or not metadata['description'].strip()):
            raise ValueError(f'{source}/SKILL.md: Skill 名称或描述无效')
        files = []
        for path, content in documents.items():
            relative = Path(name) / path
            destination = snapshot / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            files.append({'path': relative.as_posix(), 'sha256': hashlib.sha256(content).hexdigest()})
        entries.append({'name': name, 'description': metadata['description'],
                        'path': str(snapshot / name / 'SKILL.md'), 'files': files})
    Path(prefix).with_suffix('.skills.json').write_text(
        json.dumps({'version': 1, 'skills': entries}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    return render_prompt(CATALOG, SKILLS=entries)
