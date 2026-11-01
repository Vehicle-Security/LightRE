"""Ghidra owns the function catalog, assembly and reference pseudocode."""

import fcntl
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from workspace import PROJECT_DIR, RESULT_SCHEMA, WorkspaceReader, prepare_workspace

SCRIPT = Path(__file__).with_name('ghidra_scripts') / 'ExportReferences.java'
ANALYSIS_PROFILE = 'ghidra-catalog-v1'


def _write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def find_headless():
    home = Path(os.environ.get('GHIDRA_HOME', PROJECT_DIR / 'tools/ghidra'))
    local = home / 'support/analyzeHeadless'
    return str(local) if local.is_file() else shutil.which('analyzeHeadless')


def _configuration(digest):
    executable = find_headless()
    if not executable:
        raise RuntimeError('找不到 Ghidra Headless，请运行 scripts/install_deps.sh')
    properties = Path(executable).resolve().parents[1] / 'Ghidra/application.properties'
    version = properties.read_text(encoding='utf-8') if properties.exists() else executable
    analysis_timeout = os.environ.get('LIGHTRE_GHIDRA_ANALYSIS_TIMEOUT', '300')
    function_timeout = os.environ.get('LIGHTRE_GHIDRA_FUNCTION_TIMEOUT', '60')
    if min(int(analysis_timeout), int(function_timeout)) < 1:
        raise ValueError('Ghidra 超时时间必须大于零')
    identity = hashlib.sha256((digest + version + SCRIPT.read_text() + analysis_timeout + function_timeout).encode()).hexdigest()
    return executable, identity, analysis_timeout, function_timeout


def _read_export(path, digest):
    data = json.loads(path.read_text(encoding='utf-8'))
    if data['sha256'] != digest:
        raise ValueError('Ghidra 结果属于其他 SO')
    if data.get('export_schema') != 1 or not data.get('language', '').startswith('AARCH64:LE:64'):
        raise ValueError('需要 ARM64 Ghidra 完整导出，请重新执行分析入口')
    entries = data['functions']
    if len({fn['rva'] for fn in entries}) != len(entries):
        raise ValueError('Ghidra 导出含重复函数 RVA')
    if not entries:
        raise ValueError('Ghidra 没有识别到包含指令的函数；请查看 headless.log')
    return data


def _export(directory, digest, configuration, force=False):
    executable, identity, analysis_timeout, function_timeout = configuration
    root = directory / 'references/ghidra'
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / 'state.json'
    if not force and state_path.exists():
        saved = json.loads(state_path.read_text())
        if saved.get('identity') == identity and saved.get('status') in ('ready', 'exported'):
            suffix = '' if saved['status'] == 'ready' else '.pending'
            output, assembly = root / f'functions{suffix}.json', root / f'assembly{suffix}.jsonl'
            if output.exists() and assembly.exists():
                data = _read_export(output, digest)
                return data, output, assembly, saved
    output, assembly = root / 'functions.pending.json', root / 'assembly.pending.jsonl'
    output.unlink(missing_ok=True)
    assembly.unlink(missing_ok=True)
    project = root / 'project'
    project.mkdir(exist_ok=True)
    state = {'status': 'running', 'sha256': digest, 'identity': identity, 'log': str(root / 'headless.log')}
    _write(state_path, state)
    # An explicit new export imports and analyzes the original binary; complete
    # matching exports return above, without starting a tool or using Rizin.
    command = [executable, str(project), 'LightRE', '-import', str(directory / 'binary/original.so'),
               '-overwrite', '-analysisTimeoutPerFile', analysis_timeout,
               '-scriptPath', str(SCRIPT.parent), '-postScript', SCRIPT.name,
               str(output), str(assembly), digest, function_timeout]
    process = None
    try:
        with (root / 'headless.log').open('ab') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            returncode = process.wait()
            process = None
        if returncode or not output.exists() or not assembly.exists():
            raise RuntimeError(f'Ghidra 导出失败，退出码 {returncode}；详情见 {root / "headless.log"}')
        data = _read_export(output, digest)
        if data.get('analysis_timed_out'):
            raise RuntimeError('Ghidra 自动分析超时；旧目录保留，请增加 LIGHTRE_GHIDRA_ANALYSIS_TIMEOUT 后重试')
        state.update(status='exported', functions=len(data['functions']),
                     available=sum(fn['status'] == 'ok' for fn in data['functions']))
        _write(state_path, state)
        return data, output, assembly, state
    except BaseException as error:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            except ProcessLookupError:
                pass
        state.update(status='error', error=str(error))
        _write(state_path, state)
        raise


def _publish(conn, data, assembly_path, identity):
    """Replace the catalog in a transaction, retaining reviews at unchanged RVAs."""
    functions = {fn['rva']: fn for fn in data['functions']}
    with conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('''CREATE TABLE IF NOT EXISTS assembly (
            function_rva INTEGER NOT NULL REFERENCES functions(rva) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL, rva INTEGER NOT NULL, bytes TEXT NOT NULL, opcode TEXT NOT NULL,
            PRIMARY KEY(function_rva, ordinal))''')
        conn.execute('DELETE FROM assembly')
        # Upsert keeps unchanged function reviews; only removed entities cascade.
        conn.executemany('''INSERT INTO functions VALUES (?,?,?,?) ON CONFLICT(rva) DO UPDATE SET
            name=excluded.name, instructions=excluded.instructions, ranges=excluded.ranges''', [
                (fn['rva'], fn['name'], fn['instructions'], json.dumps(fn['ranges'])) for fn in functions.values()])
        conn.execute('CREATE TEMP TABLE new_functions (rva INTEGER PRIMARY KEY)')
        conn.executemany('INSERT INTO new_functions VALUES (?)', [(rva,) for rva in functions])
        conn.execute('DELETE FROM functions WHERE rva NOT IN (SELECT rva FROM new_functions)')
        conn.execute('DROP TABLE new_functions')
        seen = set()
        with assembly_path.open(encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                rva, ops = row['rva'], row['instructions']
                if rva in seen or rva not in functions:
                    raise ValueError('Ghidra 汇编函数集合不匹配')
                fn = functions[rva]
                expected = [address for start, end in fn['ranges'] for address in range(start, end, 4)]
                if (len(ops) != fn['instructions'] or [op['rva'] for op in ops] != expected
                        or any(len(bytes.fromhex(op['bytes'])) != 4 for op in ops)):
                    raise ValueError(f'Ghidra 函数汇编不完整：{rva:#x}')
                conn.executemany('INSERT INTO assembly VALUES (?,?,?,?,?)', [
                    (rva, index, op['rva'], op['bytes'], op['opcode']) for index, op in enumerate(ops)])
                seen.add(rva)
        if seen != set(functions):
            raise ValueError('Ghidra 汇编缺少函数')
        conn.execute("DELETE FROM metadata WHERE key IN ('source_fingerprint','export_error','rizin_version')")
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_index'").fetchone()
        if exists:
            conn.execute('DELETE FROM source_index')
        conn.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)', {
            'base': str(data['image_base']), 'state': 'ready', 'error': '', 'schema_version': RESULT_SCHEMA,
            'analysis_profile': ANALYSIS_PROFILE, 'ghidra_export_identity': identity,
        }.items())


def analyze(target, workspace=None, name=None, force=False):
    """Export Ghidra results, reconcile old boundaries, and seed module drafts."""
    from .migration import publish_catalog, recover_catalog
    from recovery.source import prepare_source
    directory = prepare_workspace(Path(target), workspace, name)
    original = directory / 'binary/original.so'
    with original.open('rb') as stream:
        header = stream.read(20)
    if header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != 183:
        raise ValueError('目前只支持小端 ARM64 ELF')
    tasks = directory / 'tasks'
    tasks.mkdir(exist_ok=True)
    with (directory / '.analysis.lock').open('a+') as analysis_lock, (tasks / '.lock').open('a+') as worker_lock:
        fcntl.flock(analysis_lock, fcntl.LOCK_EX)
        try:
            fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('当前工作区有分析任务运行；请暂停队列后重新导出函数目录') from error
        recover_catalog(directory)
        with sqlite3.connect(directory / 'index.sqlite') as conn:
            meta = dict(conn.execute('SELECT key,value FROM metadata'))
        configuration = _configuration(meta['sha256'])
        data, output, assembly, state = _export(directory, meta['sha256'], configuration, force)
        if (force or meta.get('analysis_profile') != ANALYSIS_PROFILE
                or meta.get('ghidra_export_identity') != state['identity']):
            publish_catalog(directory, data, assembly, state['identity'])
        if output.name.endswith('.pending.json'):
            output.replace(output.with_name('functions.json'))
            assembly.replace(assembly.with_name('assembly.jsonl'))
        state['status'] = 'ready'
        _write(directory / 'references/ghidra/state.json', state)
        prepare_source(WorkspaceReader(directory), data['functions'])
    return directory


def prepare_references(reader):
    """Workers use the already exported references; analysis happens at import."""
    root = reader.directory / 'references/ghidra'
    state_path = root / 'state.json'
    if not state_path.exists():
        return {'status': 'unavailable', 'error': '尚未导出 Ghidra 参考，请运行 scripts/analyze.py'}
    state = json.loads(state_path.read_text())
    if state.get('status') != 'ready':
        return {'status': 'unavailable', 'error': 'Ghidra 导出尚未完成，请重新运行分析入口'}
    return state


def read_references(reader, rvas):
    output = reader.directory / 'references/ghidra/functions.json'
    if not output.exists():
        return [{'rva': rva, 'status': 'unavailable', 'code': None} for rva in rvas]
    data = json.loads(output.read_text(encoding='utf-8'))
    if data['sha256'] != reader.catalog()['sample']['sha256']:
        raise ValueError('Ghidra 参考结果属于其他 SO')
    functions = {fn['rva']: {**fn, 'image_base': data.get('image_base', 0)} for fn in data['functions']}
    return [functions.get(rva, {'rva': rva, 'status': 'missing', 'code': None}) for rva in rvas]


def investigate_function(reader, rva, jumps=(), style='normalize', timeout=120):
    """Probe saved ProgramDB in read-only mode; never replace current results.

    jumps is an explicit list of {rva: computed branch, targets: [RVA, ...]}.
    Callers must establish those targets from evidence, not guessed bounds.
    """
    if rva not in {fn['rva'] for fn in reader.catalog()['functions']}:
        raise ValueError('未识别的函数 RVA')
    if style not in ('decompile', 'normalize', 'firstpass', 'register') or timeout < 1:
        raise ValueError('无效的反编译模式或时限')
    headless = find_headless()
    if not headless:
        raise RuntimeError('找不到 Ghidra Headless')
    root = reader.directory / 'references/ghidra'
    if not (root / 'project/LightRE.gpr').exists():
        raise RuntimeError('没有保存的 Ghidra 项目')
    directory = root / 'investigations' / f'{rva:x}-{uuid4().hex[:8]}'
    directory.mkdir(parents=True)
    plan = {'rva': rva, 'jumps': list(jumps), 'style': style, 'timeout': timeout,
            'sha256': reader.catalog()['sample']['sha256']}
    _write(directory / 'input.json', plan)
    command = [headless, str(root / 'project'), 'LightRE', '-process', 'original.so',
               '-readOnly', '-noanalysis', '-scriptPath', str(SCRIPT.parent),
               '-postScript', 'DecompileFunction.java', str(directory / 'input.json'),
               str(directory / 'result.json')]
    started = time.monotonic()
    with (reader.directory / '.analysis.lock').open('a+') as lock, (directory / 'headless.log').open('wb') as log:
        fcntl.flock(lock, fcntl.LOCK_EX)
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
        try:
            returncode = process.wait(timeout=timeout + 90)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            except ProcessLookupError:
                pass
            raise
    output = directory / 'result.json'
    if returncode or not output.exists():
        raise RuntimeError(f'定向反编译失败，详情见 {directory / "headless.log"}')
    result = json.loads(output.read_text())
    result.update(sha256=plan['sha256'], duration_seconds=round(time.monotonic() - started, 3))
    _write(output, result)
    return {**result, 'path': str(output)}
