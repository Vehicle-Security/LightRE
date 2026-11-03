"""Workspace 级 Rehost 知识库：跨 SO、跨轮次的 Markdown 经验笔记。

目录位于 workspace 根（rehost-knowledge/），首次使用时从模块模板播种；
条目由 agent 在真实运行取得依据后追加，格式约定见模板 INDEX.md。
"""

from pathlib import Path

SEED_DIR = Path(__file__).parent / 'knowledge'


def directory(directory: Path) -> Path:
    """分析目录所属 workspace 的知识库路径，缺省文件播种后返回。"""
    root = Path(directory).resolve().parent / 'rehost-knowledge'
    root.mkdir(parents=True, exist_ok=True)
    for template in sorted(SEED_DIR.glob('*.md')):
        path = root / template.name
        if not path.exists():
            path.write_text(template.read_text(encoding='utf-8'), encoding='utf-8')
    return root


def summary(root: Path) -> str:
    """给轮次 prompt 的知识库概览：根路径与每个文件的标题清单。"""
    lines = [f'知识库根目录：{root}',
             '使用说明与条目格式见 INDEX.md；按需读取相关主题文件，不必全部载入。',
             '本轮以真实运行为依据的新经验，结束前追加到对应主题文件。']
    topics = []
    for path in sorted(root.glob('*.md')):
        if path.name == 'INDEX.md':
            continue
        heading = next((line.lstrip('# ') for line in path.read_text(encoding='utf-8').splitlines()
                        if line.startswith('# ')), path.stem)
        topics.append(f'- {path.name}：{heading}')
    return '\n'.join(lines + topics)
