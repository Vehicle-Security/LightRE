"""Agent 可编辑的知识文件：单工程 HARNESS.md 与 workspace 级知识库。

两层生命周期不同：HARNESS.md 随单个 harness 工程存续，记录该 SO 的入口、
环境与缺口；rehost-knowledge/ 位于 workspace 根，跨 SO、跨轮次沉淀通用经验。
"""

from pathlib import Path

from lightre.workspace import PROJECT_DIR
from lightre.prompt import render_prompt

TEMPLATE = Path(__file__).parent / 'prompts/HARNESS.md'
SEED_DIR = Path(__file__).parent / 'knowledge'
BEGIN = '<!-- lightre:verification -->'
END = '<!-- /lightre:verification -->'


# ============================================================================
# 单工程操作说明（HARNESS.md）
# ============================================================================

def ensure_guide(harness, target='target', *, instructions=None):
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


def record_check(harness, report, verification):
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


# ============================================================================
# Workspace 级知识库（rehost-knowledge/）
# ============================================================================

def directory(directory: Path) -> Path:
    """分析目录所属 workspace 的知识库路径，缺省文件播种后返回。"""
    root = Path(directory).resolve().parent / 'rehost-knowledge'
    root.mkdir(parents=True, exist_ok=True)
    for template in sorted(SEED_DIR.glob('*.md')):
        path = root / template.name
        if not path.exists():
            body = template.read_text(encoding='utf-8')
            with path.open('x', encoding='utf-8') as stream:
                stream.write(body)
    return root


def summary(root: Path) -> str:
    """给轮次 prompt 的知识库概览：根路径与每个文件的标题清单。"""
    lines = [f'知识库根目录：{root}',
             '使用说明与条目格式见 INDEX.md；按需读取相关主题文件，不必全部载入。',
             '本轮以真实运行为依据的新经验，结束前追加到对应主题文件。']
    for path in sorted(root.glob('*.md')):
        if path.name == 'INDEX.md':
            continue
        heading = next((line.lstrip('# ') for line in path.read_text(encoding='utf-8').splitlines()
                        if line.startswith('# ')), path.stem)
        lines.append(f'- {path.name}：{heading}')
    return '\n'.join(lines)
