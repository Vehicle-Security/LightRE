"""Per-harness instructions, preserved across repairs and saved with each Run."""

from pathlib import Path
import shlex

from workspace import PROJECT_DIR
from lightre.paths import REPOSITORY_DIR, python_command


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
        body = TEMPLATE.read_text(encoding='utf-8').replace('{{TARGET}}', target)
        body = body.replace('{{ADAPTATION}}', Path(instructions).read_text(encoding='utf-8') if instructions else '请调查并补充本目标的 ABI、初始化、输入输出与已知缺口。')
        native = (shlex.join(['bash', str(REPOSITORY_DIR / 'scripts/install_deps.sh'), '--runtime-only']) if REPOSITORY_DIR
                  else '在 LightRE 源码目录执行 bash scripts/install_deps.sh --runtime-only；Python 安装包不包含系统工具')
        body = body.replace('{{SYSTEM_DEPENDENCIES}}', native)
        body = body.replace('{{HARNESS_COMMAND}}', python_command('lightre.cli.harness'))
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
        body += f'\n\n## 最近外部检查（工具维护）\n\n{BEGIN}\n{END}\n'
    # Malformed model edits must not truncate the human-maintained instructions.
    if body.count(BEGIN) != 1 or body.count(END) != 1 or body.index(END) < body.index(BEGIN):
        raise ValueError('Keep the HARNESS.md verification section markers intact')
    start, rest = body.split(BEGIN)
    _, tail = rest.split(END)
    block = (f'\n最近外部检查状态：`{report["status"]}`；通过：`{report["passed"]}`。\n'
             f'检查记录：`{Path(verification).resolve()}`。\n'
             '结论仅适用于该记录中的 SO、参数、源码和用例；修复后需重新检查。\n')
    temp = path.with_name('HARNESS.md.tmp')
    temp.write_text(start + BEGIN + block + END + tail, encoding='utf-8')
    temp.replace(path)
