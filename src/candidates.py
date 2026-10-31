"""One current, unverified C candidate per original function RVA."""

from pathlib import Path
from tempfile import NamedTemporaryFile

from workspace import WorkspaceReader


def _target(reader: WorkspaceReader, rva: int):
    with reader._connect() as conn:
        conn.execute("BEGIN")
        name = conn.execute("SELECT name FROM functions WHERE rva=?", (rva,)).fetchone()
        meta = dict(conn.execute("SELECT key,value FROM metadata"))
    if name is None:
        raise KeyError(f"未识别的函数 RVA：{rva:#x}")
    path = reader.directory / "candidates" / f"{rva:#010x}.c"
    header = (
        "/* LightRE analysis candidate\n"
        f" * binary: {Path(meta['source_path']).name}\n"
        f" * sha256: {meta['sha256']}\n"
        f" * function_rva: {rva:#010x}\n"
        " * status: unverified (分析候选，未验证)\n"
        " */\n"
    )
    return path, header, name[0]


def read_candidate(reader: WorkspaceReader, rva: int) -> dict:
    """Read the actual file afresh; missing candidates are a normal state."""
    path, header, name = _target(reader, rva)
    try:
        code = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        code = None
    if code is not None and not code.startswith(header):
        raise ValueError(f"候选的来源注释与当前 SO / RVA 不匹配：{path}")
    return {"rva": rva, "name": name, "path": str(path), "code": code,
            "status": "unverified" if code is not None else "missing"}


def save_candidate(reader: WorkspaceReader, rva: int, code: str) -> Path:
    """Atomically replace one candidate, with provenance from the selected DB."""
    path, header, _ = _target(reader, rva)
    if code.startswith("/* LightRE analysis candidate"):
        if not code.startswith(header):
            raise ValueError("候选的来源注释与当前 SO / RVA 不匹配")
        code = code[len(header):].lstrip("\n")
    if not code.strip():
        raise ValueError("候选代码不能为空")
    path.parent.mkdir(exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                            prefix=".candidate-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(header + "\n" + code)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
