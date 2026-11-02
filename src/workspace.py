"""Import binary files and read completed, persistent analysis results."""

import hashlib
import os
import shutil
import sqlite3
from pathlib import Path


from lightre.paths import PROJECT_DIR
RESULT_SCHEMA = "2"


def find_target(target: Path) -> Path:
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


def prepare_workspace(target: Path, workspace: Path | None = None, name: str | None = None) -> Path:
    """Called by the exporter, never by the Web server."""
    binary = find_target(Path(target))
    source_dir = binary.parent
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    workspace_name = source_dir.name if binary.suffix == ".so" else binary.stem
    workspace = (Path(workspace) if workspace else Path.cwd() / (workspace_name + "-analysis")).resolve()
    if workspace.is_relative_to(PROJECT_DIR):
        raise ValueError("分析 workspace 请放在 LightRE 工具仓库之外")
    source_repo = next((p for p in (source_dir, *source_dir.parents) if (p / ".git").exists()), None)
    if source_repo and workspace.is_relative_to(source_repo):
        raise ValueError("分析 workspace 请放在样本 Git 仓库之外，通过 --workspace 指定目录")
    workspace.mkdir(parents=True, exist_ok=True)
    for db in workspace.glob("*/index.sqlite"):
        with sqlite3.connect(db) as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key='sha256'").fetchone()
        if row and row[0] == digest:
            original = db.parent / "binary/original.so"
            if hashlib.sha256(original.read_bytes()).hexdigest() != digest:
                raise ValueError(f"工作区原始 SO 已发生变化：{original}")
            return db.parent
    display_name = name or source_dir.name
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in display_name).strip(".") or "binary"
    length = 4
    directory = workspace / f"{safe_name}-{digest[:length]}"
    while directory.exists():
        length += 1
        if length > 64:
            raise ValueError(f"分析目录冲突，请检查：{directory}")
        directory = workspace / f"{safe_name}-{digest[:length]}"
    (directory / "binary").mkdir(parents=True)
    original = directory / "binary/original.so"
    shutil.copyfile(binary, original)
    original.chmod(0o444)
    with sqlite3.connect(directory / "index.sqlite") as conn:
        conn.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE functions (
                rva INTEGER PRIMARY KEY, name TEXT NOT NULL,
                instructions INTEGER NOT NULL, ranges TEXT NOT NULL
            );
        """)
        conn.executemany("INSERT INTO metadata VALUES (?,?)", {
            "sha256": digest, "name": display_name, "source_path": str(binary),
            "source_dir": str(source_dir), "state": "new", "base": "0",
            "schema_version": "1", "error": "",
        }.items())
    return directory


class WorkspaceReader:
    """Read a selected SO's exported results, without loading any analysis tool."""

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
            raise ValueError("工作区尚未完成汇编导出，请先运行 scripts/analyze.py")

    def _connect(self):
        conn = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def catalog(self) -> dict:
        with self._connect() as conn:
            conn.execute("BEGIN")
            meta = dict(conn.execute("SELECT key,value FROM metadata"))
            functions = [dict(row) for row in conn.execute(
                "SELECT rva,name,instructions FROM functions ORDER BY rva"
            )]
        return {"sample": {**meta, "function_count": len(functions),
                           "directory": str(self.directory),
                           "workspace": str(self.directory.parent)},
                "functions": functions}

    def assembly(self, rva: int, offset: int = 0, limit: int = 200) -> dict:
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
        return {"rva": rva, "name": function["name"], "offset": offset,
                "total": function["instructions"], "instructions": instructions,
                "next_offset": next_offset if next_offset < function["instructions"] else None}
