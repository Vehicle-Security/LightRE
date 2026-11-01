"""Build and merge module files before the LLM batch loop."""

import shutil
from pathlib import Path
from tempfile import mkdtemp

from .candidates import (
    FUNCTIONS_PER_FILE, GHIDRA_MARKER, _atomic_write, _blocks, _identity,
    _index, _locked, _module_header, _module_prefix, _read_text, candidate_rvas, module_name, save_candidate,
)
from .formatting import FormattingError, format_code


def merge_sources(reader, modules=None):
    """Repack source fragments by module without rewriting any function body.

    Shared declarations outside function markers keep their original file. The
    source lock and a recoverable directory swap protect readers during a merge.
    """
    modules = modules or {}
    with _locked(reader):
        meta, names = _identity(reader)
        directory = reader.directory / "source/src"
        originals = {path.name: _read_text(path) for path in directory.glob("*.c")}
        if not originals:
            return {"functions": 0, "files_before": 0, "files_after": 0}
        grouped, desired, seen = {}, {}, set()
        for filename, text in originals.items():
            prefix = _module_prefix(text, meta)
            matches = _blocks(text, meta)
            for match in matches:
                rva = int(match['rva'], 16)
                if rva in seen:
                    raise ValueError(f"源码包含重复函数 RVA：{rva:#x}")
                seen.add(rva)
            # Do not discard or relocate manually added shared types/declarations.
            outside = text
            for match in reversed(matches):
                outside = outside[:match.start()] + outside[match.end():]
            if outside.strip() != prefix.strip():
                desired[filename] = text
                continue
            for match in matches:
                rva = int(match['rva'], 16)
                module = module_name(modules.get(rva, Path(filename).stem))
                grouped.setdefault(module, []).append((rva, match.group(0)))
        for module, functions in sorted(grouped.items()):
            functions.sort()
            number = 1
            for offset in range(0, len(functions), FUNCTIONS_PER_FILE):
                while True:
                    suffix = '' if number == 1 else f'_{number:03d}'
                    filename = f'{module}{suffix}.c'
                    number += 1
                    if filename not in desired:
                        break
                desired[filename] = _module_header(meta) + ''.join(
                    code + '\n\n' for _, code in functions[offset:offset + FUNCTIONS_PER_FILE])
        if desired != originals:
            staging = Path(mkdtemp(prefix='.merge-', dir=directory.parent))
            try:
                shutil.copytree(directory, staging, dirs_exist_ok=True)
                for path in staging.glob('*.c'):
                    path.unlink()
                for filename, text in desired.items():
                    _atomic_write(staging / filename, text)
                previous = directory.parent / '.previous'
                directory.rename(previous)
                try:
                    staging.rename(directory)
                except BaseException:
                    previous.rename(directory)
                    raise
                shutil.rmtree(previous)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        _index(reader, meta, names)
        return {"functions": len(seen), "files_before": len(originals), "files_after": len(desired)}


def prepare_source(reader, references):
    """Write Ghidra initial code, preserve existing work, then consolidate modules."""
    present = candidate_rvas(reader)
    seeded, format_errors = 0, {}
    modules = {item['rva']: module_name(item.get('module', 'recovered')) for item in references}
    for item in references:
        if item['status'] == 'ok' and item.get('code') and item['rva'] not in present:
            code = item['code']
            try:
                code = format_code(reader, code)
            except FormattingError as error:
                format_errors[f"{item['rva']:#x}"] = str(error)
            save_candidate(reader, item['rva'], GHIDRA_MARKER + code, modules[item['rva']])
            seeded += 1
            present.add(item['rva'])
    result = merge_sources(reader, modules)
    return {**result, 'seeded': seeded, 'format_errors': format_errors}
