"""Render module-owned Markdown templates without interpreting input data."""

import json
from pathlib import Path

from jinja2 import Environment, StrictUndefined, TemplateError


_ENVIRONMENT = Environment(autoescape=False, undefined=StrictUndefined, keep_trailing_newline=True)
_ENVIRONMENT.filters['json'] = lambda value: json.dumps(value, ensure_ascii=False)


def render_prompt(path, **context):
    """Render trusted Markdown; inserted code/data is never rendered again."""
    path = Path(path)
    template = path.read_text(encoding='utf-8')
    try:
        return _ENVIRONMENT.from_string(template).render(**context)
    except TemplateError as error:
        raise ValueError(f'{path}: 模板渲染失败：{error}') from error
