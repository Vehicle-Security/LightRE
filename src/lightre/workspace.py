"""Workspace 管理：导入二进制、准备分析环境、读取结果"""

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

from agents import run_blocking
from lightre.paths import PACKAGE_DIR, PROJECT_DIR
from lightre.prompt import render_prompt

RESULT_SCHEMA = "2"


# ============================================================================
# 文件发现
# ============================================================================

def find_target(target: Path) -> Path:
    """在目录中查找唯一的 SO 文件"""
    target = target.resolve()
    if target.is_file():
        return target
    if not target.is_dir():
        raise ValueError(f"输入不存在：{target}")
    
    candidates = sorted(p for p in target.glob("*.so") if p.is_file())
    if not candidates:
        for root, dirs, files in os.walk(target):
            if "index.sqlite" in files:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            candidates.extend(Path(root) / f for f in files if f.endswith(".so"))
    
    if len(candidates) != 1:
        choices = "\n".join(str(p) for p in candidates) or "没有找到 .so"
        raise ValueError(f"请明确选择目标 SO：\n{choices}")
    return candidates[0]


def arm64_libraries(directory: Path) -> list[Path]:
    """扫描目录中所有 ARM64 .so 文件"""
    candidates = set()
    directory = Path(directory).resolve()
    
    for root, dirs, files in os.walk(directory):
        if 'index.sqlite' in files:
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        
        for name in files:
            if not name.endswith('.so'):
                continue
            path = (Path(root) / name).resolve()
            if not path.is_relative_to(directory) or not path.is_file():
                continue
            
            # 检查是否为 ARM64 ELF
            with path.open('rb') as stream:
                header = stream.read(20)
            if header[:6] == b'\x7fELF\x02\x01' and int.from_bytes(header[18:20], 'little') == 183:
                candidates.add(path)
    
    return sorted(candidates)


# ============================================================================
# Workspace 准备
# ============================================================================

def prepare_workspace(target: Path, workspace: Path | None = None, name: str | None = None) -> Path:
    """准备或重用 SO 的分析目录"""
    binary = find_target(Path(target))
    source_dir = binary.parent
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    
    # 确定 workspace
    workspace = (Path(workspace) if workspace else Path.cwd() / "workspace").resolve()
    
    # 路径检查：允许 ./workspace/，不允许项目根目录
    if workspace == PROJECT_DIR or (
        workspace.is_relative_to(PROJECT_DIR) and
        workspace.parent == PROJECT_DIR and
        workspace.name != "workspace"
    ):
        raise ValueError("分析 workspace 请放在 LightRE 工具仓库之外")

    # 检查是否在源 SO 的 git 仓库内
    source_repo = next((p for p in (source_dir, *source_dir.parents) if (p / ".git").exists()), None)
    if source_repo and workspace.is_relative_to(source_repo):
        raise ValueError("分析 workspace 请放在样本 Git 仓库之外，通过 --workspace 指定目录")
    
    workspace.mkdir(parents=True, exist_ok=True)
    
    # 检查是否已存在
    for db in workspace.glob("*/index.sqlite"):
        with sqlite3.connect(db) as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key='sha256'").fetchone()
        if row and row[0] == digest:
            original = db.parent / "binary/original.so"
            if hashlib.sha256(original.read_bytes()).hexdigest() != digest:
                raise ValueError(f"工作区原始 SO 已发生变化：{original}")
            return db.parent
    
    # 创建新目录
    display_name = name or source_dir.name
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in display_name).strip(".") or "binary"
    
    length = 4
    directory = workspace / f"{safe_name}-{digest[:length]}"
    while directory.exists():
        length += 1
        if length > 64:
            raise ValueError(f"分析目录冲突，请检查：{directory}")
        directory = workspace / f"{safe_name}-{digest[:length]}"
    
    # 初始化目录
    (directory / "binary").mkdir(parents=True)
    original = directory / "binary/original.so"
    shutil.copyfile(binary, original)
    original.chmod(0o444)
    
    # 创建数据库
    with sqlite3.connect(directory / "index.sqlite") as conn:
        conn.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE functions (
                rva INTEGER PRIMARY KEY, name TEXT NOT NULL,
                instructions INTEGER NOT NULL, ranges TEXT NOT NULL
            );
        """)
        conn.executemany("INSERT INTO metadata VALUES (?,?)", {
            "sha256": digest,
            "name": display_name,
            "source_path": str(binary),
            "source_dir": str(source_dir),
            "state": "new",
            "base": "0",
            "schema_version": "1",
            "error": "",
        }.items())
    
    return directory


def explore_directory(source_dir: Path, workspace: Path) -> dict:
    """探索目录中的 ARM64 库，返回选择结果"""
    source_dir = Path(source_dir).resolve()
    workspace = Path(workspace).resolve()
    
    candidates = arm64_libraries(source_dir)
    if not candidates:
        raise ValueError(f'目标目录没有找到 ARM64 .so：{source_dir}')
    
    relative = {str(path.relative_to(source_dir)): path for path in candidates}
    
    # 只有一个 SO，直接返回
    if len(candidates) == 1:
        return {'binary': next(iter(relative)), 'dependencies': []}
    
    # 多个 SO，使用 Agent 选择
    output = workspace / 'exploration' / uuid4().hex[:12]
    output.mkdir(parents=True)
    
    schema = {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'binary': {'type': 'string', 'enum': list(relative)},
            'dependencies': {'type': 'array', 'items': {'type': 'string', 'enum': list(relative)}},
            'reason': {'type': 'string'},
        },
        'required': ['binary', 'dependencies', 'reason'],
    }
    
    (output / 'schema.json').write_text(json.dumps(schema, ensure_ascii=False), encoding='utf-8')
    
    # 加载 prompt
    prompt = render_prompt(PACKAGE_DIR / 'prompts/EXPLORE.md',
                           DIRECTORY=source_dir, CANDIDATES=list(relative))
    (output / 'prompt.md').write_text(prompt, encoding='utf-8')
    
    print(f'LightRE: 正在探索 {source_dir}；记录：{output}', flush=True)
    
    result = run_blocking(
        root=workspace / 'agents',
        directory=output,
        cwd=source_dir,
        prompt=prompt,
        timeout=300,
        output_schema=schema,
        sandbox='read-only',
    )
    
    if result.get('status') not in ('exited', 'completed') or result.get('exit_code', 0) != 0:
        raise ValueError(f'目标目录探索失败，请检查 {output / "execution.json"}')
    
    try:
        selection = json.loads((output / 'result.md').read_text(encoding='utf-8'))
        if (selection['binary'] not in relative or
                not isinstance(selection['dependencies'], list) or
                any(name not in relative for name in selection['dependencies'])):
            raise ValueError('选择结果不属于目录中的 ARM64 SO')
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f'目标目录探索返回无效，请检查 {output / "result.md"}：{error}') from error
    
    return selection


# ============================================================================
# Workspace Reader
# ============================================================================

class WorkspaceReader:
    """读取 SO 的导出结果"""
    
    def __init__(self, directory: Path):
        directory = Path(directory).resolve()
        if not (directory / "index.sqlite").is_file():
            candidates = list(directory.glob("*/index.sqlite"))
            if len(candidates) != 1:
                raise ValueError("请指定包含 index.sqlite 的 SO 分析目录")
            directory = candidates[0].parent
        
        self.directory = directory
        self.database = directory / "index.sqlite"
        
        meta = self.catalog()["sample"]
        if meta.get("schema_version") != RESULT_SCHEMA or meta["state"] != "ready":
            raise ValueError("工作区尚未完成汇编导出，请由主 workflow 完成静态分析")
    
    def _connect(self):
        conn = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn
    
    def catalog(self) -> dict:
        """获取 SO 的元数据和函数列表"""
        with self._connect() as conn:
            conn.execute("BEGIN")
            meta = dict(conn.execute("SELECT key,value FROM metadata"))
            functions = [dict(row) for row in conn.execute(
                "SELECT rva,name,instructions FROM functions ORDER BY rva"
            )]
        
        return {
            "sample": {
                **meta,
                "function_count": len(functions),
                "directory": str(self.directory),
                "workspace": str(self.directory.parent)
            },
            "functions": functions
        }
    
    def assembly(self, rva: int, offset: int = 0, limit: int = 200) -> dict:
        """获取函数的汇编指令"""
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError("offset 必须非负，limit 范围为 1–200")
        
        with self._connect() as conn:
            conn.execute("BEGIN")
            function = conn.execute("SELECT * FROM functions WHERE rva=?", (rva,)).fetchone()
            if function is None:
                raise KeyError(f"未识别的函数 RVA：{rva:#x}")
            
            instructions = [dict(row) for row in conn.execute(
                "SELECT rva,bytes,opcode FROM assembly "
                "WHERE function_rva=? AND ordinal>=? ORDER BY ordinal LIMIT ?",
                (rva, offset, limit),
            )]
        
        expected = min(limit, max(0, function["instructions"] - offset))
        if len(instructions) != expected:
            raise RuntimeError("保存的汇编不完整，请重新执行导出")
        
        next_offset = offset + len(instructions)
        return {
            "rva": rva,
            "name": function["name"],
            "offset": offset,
            "total": function["instructions"],
            "instructions": instructions,
            "next_offset": next_offset if next_offset < function["instructions"] else None
        }
