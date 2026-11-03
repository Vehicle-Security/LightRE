"""Per-harness instructions, preserved across repairs and saved with each Run."""

from pathlib import Path

from lightre.workspace import PROJECT_DIR
from lightre.prompt import render_prompt


TEMPLATE = Path(__file__).parent / 'prompts/HARNESS.md'
BEGIN = '<!-- lightre:verification -->'
END = '<!-- /lightre:verification -->'


def ensure(harness, target='target', *, instructions=None):
    """Create instructions first; never overwrite user/model adaptations."""
    harness = Path(harness).resolve()
    if harness.is_relative_to(PROJECT_DIR):
        raise ValueError('Use an editable harness outside the LightRE repository')
    harness.mkdir(parents=True, exist_ok=True)
    path = harness / 'HARNESS.md'
    if path.is_symlink():
        raise ValueError('HARNESS.md cannot be a symlink')
    if not path.exists():
        body = render_prompt(TEMPLATE, TARGET=target,
                             ADAPTATION=Path(instructions).read_text(encoding='utf-8') if instructions else None)
        with path.open('x', encoding='utf-8') as stream:
            stream.write(body)
    return path


def record(harness, report, verification):
    """Update only the tool-owned evidence section, after execution has stopped."""
    path = Path(harness) / 'HARNESS.md'
    if path.is_symlink():
        raise ValueError('HARNESS.md cannot be a symlink')
    body = path.read_text(encoding='utf-8')
    if BEGIN not in body and END not in body:
        body += render_prompt(TEMPLATE.with_name('VERIFICATION_SECTION.md'), BEGIN=BEGIN, END=END)
    # Malformed model edits must not truncate the human-maintained instructions.
    if body.count(BEGIN) != 1 or body.count(END) != 1 or body.index(END) < body.index(BEGIN):
        raise ValueError('Keep the HARNESS.md verification section markers intact')
    start, rest = body.split(BEGIN)
    _, tail = rest.split(END)
    block = render_prompt(TEMPLATE.with_name('VERIFICATION_RESULT.md'), STATUS=report['status'],
                          PASSED=report['passed'], REPORT=Path(verification).resolve())
    temp = path.with_name('HARNESS.md.tmp')
    temp.write_text(start + BEGIN + block + END + tail, encoding='utf-8')
    temp.replace(path)
