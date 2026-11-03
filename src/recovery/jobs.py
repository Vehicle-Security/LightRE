"""A detached Codex task per SO with bounded parallel batches; files are the durable task record."""

import fcntl
from collections import Counter, deque
import json
import os
import shutil
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from lightre.prompt import render_prompt
from lightre.paths import SOURCE_DIR, TOOLS_DIR
from analyzers.ghidra import prepare_references, read_references
from .candidates import candidate_origins, candidate_rvas, migrate_candidates, module_name, read_candidate, save_candidate
from .source import prepare_source
from .formatting import FormattingError, format_code
from .knowledge import _entry, _normalize, _validate, knowledge_rvas, migrate_knowledge, read_knowledge, save_knowledge_batch
from .skills import skill_catalog

PROMPTS = Path(__file__).parent / 'prompts'

MAX_BATCH_INSTRUCTIONS = 4000
MAX_BATCH_FUNCTIONS = 32
MAX_BATCH_CONTEXT_CHARS = 60000
MAX_PROMPT_CHARS = 1_048_576  # Codex turn/start input limit, measured with a full assembly prompt.
INITIAL_BATCH_TIMEOUT = 180
FEEDBACK_BATCH_TIMEOUT = 600
MAX_BATCH_RETRIES = 1
RETRY_BACKOFF = 5


class BusyError(RuntimeError):
    pass


class BatchFailure(RuntimeError):
    """A CLI/output failure, isolated from source-storage failures."""

    def __init__(self, message, missing=None, rate_limited=False, *, retryable=True):
        super().__init__(message)
        self.missing = missing
        self.rate_limited = rate_limited
        self.retryable = retryable


def _write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def task_state(reader):
    from .queue import snapshot
    jobs = [job for job in snapshot(reader) if not job.get('dismissed')]
    if not jobs:
        return {"status": "idle"}
    state = next((job for job in jobs if job["status"] == "running"), None)
    if state is not None:
        with (reader.directory / "tasks/.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return state
        state["status"] = "interrupted"
        state["error"] = "任务进程已结束，继续队列可从未完成函数恢复"
        return state
    return next((job for job in jobs if job["status"] == "queued"),
                max(jobs, key=lambda job: job["created_at"]))


def progress(reader):
    from .queue import snapshot
    functions = reader.catalog()["functions"]
    origins = candidate_origins(reader)
    optimized = {rva for rva, origin in origins.items() if origin == "llm"}
    state = task_state(reader)
    # Every attempted function has an H2 entry, even if no code was produced.
    attempted = knowledge_rvas(reader)
    attempted.update(int(rva, 0) for rva in state.get("unresolved", {}))
    jobs = snapshot(reader)
    failed = set()
    for job in sorted(jobs, key=lambda item: item['created_at']):
        failed.difference_update(job.get('done_rvas', []))
        failed.update(int(rva, 0) for rva in job.get('failed_rvas', {}))
    failed.difference_update(optimized)
    statuses = {fn["rva"]: "llm" if fn["rva"] in optimized else
                "failed" if fn["rva"] in failed else
                "unresolved" if fn["rva"] in attempted else origins.get(fn["rva"], "pending")
                for fn in functions}
    fields = ('id', 'status', 'mode', 'priority', 'created_at', 'started_at', 'finished_at',
              'total', 'processed', 'generated', 'failed_count', 'effective_workers', 'run_duration_seconds',
              'rvas', 'done_rvas', 'current', 'active_batches', 'workers', 'comment', 'directory', 'error', 'parent_task', 'followup_id', 'input_mode')
    queue = [{key: job[key] for key in fields if key in job} for job in jobs]
    for item, job in zip(queue, jobs):
        failures = job.get('failed_rvas', {})
        issues = {int(rva, 0): {'rva': int(rva, 0), 'status': 'unresolved', 'reason': str(reason)[:500]}
                  for rva, reason in job.get('unresolved', {}).items()}
        issues.update({int(rva, 0): {'rva': int(rva, 0), 'status': 'failed', 'reason': str(reason)[:500]}
                       for rva, reason in failures.items()})
        item['issues'] = [issues[rva] for rva in job.get('rvas', []) if rva in issues]
        item.setdefault('failed_count', len(failures))
    queue = [item for item, job in zip(queue, jobs) if not job.get('dismissed')]
    paused = (reader.directory / 'tasks/.paused').exists()
    return {"total": len(functions), "candidates": len(optimized),
            "source_functions": len(origins), "initial": len(origins) - len(optimized),
            "failed": len(failed), "unresolved": sum(status == 'unresolved' for status in statuses.values()),
            "function_statuses": statuses, "task": state, "queue": queue, "paused": paused,
            "concurrency": {"limit": max(1, int(os.environ.get('LIGHTRE_CODEX_WORKERS', '8'))),
                            "in_use": sum(job.get('active_batches', 0) for job in jobs if job['status'] == 'running')},
            "queued": sum(job['status'] == 'queued' for job in jobs)}


def start_task(reader, rvas=None, comment="", expected_sha256=None, workers=None, *, input_mode='reference'):
    from .queue import resume, submit
    if rvas is None and not comment:
        resume(reader)
    return submit(reader, rvas, comment, expected_sha256, workers, input_mode=input_mode)


def stop_task(reader):
    from .queue import pause
    pause(reader)
    return task_state(reader)


def _batches(functions):
    """Bound function count, instructions and context; oversized functions go alone."""
    batch, size, context_size = [], 0, 0
    for fn in functions:
        if batch and (len(batch) >= MAX_BATCH_FUNCTIONS or size + fn["instructions"] > MAX_BATCH_INSTRUCTIONS
                      or context_size + fn.get('context_chars', 0) > MAX_BATCH_CONTEXT_CHARS
                      or fn.get("module") != batch[0].get("module")
                      or fn.get('input_mode', 'reference') != batch[0].get('input_mode', 'reference')):
            yield batch
            batch, size, context_size = [], 0, 0
        batch.append(fn)
        size += fn["instructions"]
        context_size += fn.get('context_chars', 0)
    if batch:
        yield batch


def _schema(batch, initial=False, feedback=False):
    schema = {"type": "object", "additionalProperties": False, "required": ["results"], "properties": {
        "results": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["rva", "code", "notes"], "properties": {
                "rva": {"type": "string", "enum": [f"{fn['rva']:#x}" for fn in batch]},
                "code": {"type": ["string", "null"]}, "notes": {"type": "string", "minLength": 1}}}}}}
    if initial or feedback:
        item = schema['properties']['results']['items']
        item['required'].append('edits')
        item['properties']['edits'] = {'type': ['array', 'null'], 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['old', 'new'],
            'properties': {'old': {'type': 'string'}, 'new': {'type': 'string'}}}}
    if feedback:
        item = schema['properties']['results']['items']
        item['required'].extend(['reply', 'knowledge_edits'])
        item['properties']['reply'] = {'type': 'string', 'minLength': 1}
        item['properties']['notes'] = {'type': ['string', 'null']}
        item['properties']['knowledge_edits'] = item['properties']['edits']
    return schema


def _initial_prompt(reader, state, batch, current, references):
    """Prepare bounded first-pass context so the model need not investigate files."""
    refs = {item['rva']: item for item in references}
    context = []
    sample = reader.catalog()['sample']
    from .candidates import display_code
    for fn in batch:
        rva = fn['rva']
        reference = refs.get(rva, {})
        candidate = current[rva]
        code = candidate['code'] or reference.get('code')
        if code:
            code = display_code(sample, rva, code)
        item = {**fn, 'code': code,
                'producer': candidate['producer'], 'ghidra_error': reference.get('error'),
                'knowledge': read_knowledge(reader, rva)}
        # Short ABI helpers benefit from seeing all instructions. Large functions
        # use Ghidra C; failed decompiles get an explicit, incomplete excerpt.
        if fn['instructions'] and (fn['instructions'] <= 32 or not item['code']):
            try:
                head = reader.assembly(rva, limit=64)
                item['assembly'] = head['instructions']
                if head['next_offset'] is not None:
                    item['assembly_tail'] = reader.assembly(rva, offset=max(64, fn['instructions'] - 16), limit=16)['instructions']
                    item['assembly_incomplete'] = True
            except (KeyError, RuntimeError) as error:
                item['assembly_error'] = str(error)
        context.append(item)
    prompt = render_prompt(PROMPTS / 'INITIAL.md',
        FUNCTION_COUNT=len(batch),
        SHA256=sample.get('sha256'),
        IMAGE_BASE=sample.get('base', '0'),
        CONTEXT=json.dumps(context, ensure_ascii=False))
    return prompt, {f"{item['rva']:#x}": item['code'] for item in context}


def _feedback_prompt(reader, state, current, knowledge):
    """Inline the selected function; ask for a focused patch and a short reply."""
    context, inputs = [], {}
    for rva, candidate in current.items():
        code = candidate['display_code']
        inputs[f'{rva:#x}'] = code
        item = {'rva': f'{rva:#x}', 'code': code, 'sha256': candidate['sha256'],
                'path': candidate['path'], 'knowledge': knowledge[rva]}
        reviewed = Path(state['directory']) / 'reviewed.c'
        if candidate['sha256'] != state.get('expected_sha256') and reviewed.exists():
            item['reviewed_snapshot'] = reviewed.read_text()
        context.append(item)
    prompt = render_prompt(PROMPTS / 'FEEDBACK.md',
        COMMENT=json.dumps(state.get('comment', ''), ensure_ascii=False),
        SOURCE_DIR=SOURCE_DIR,
        DIRECTORY=reader.directory,
        PYTHON=shlex.quote(sys.executable),
        CONTEXT=json.dumps(context, ensure_ascii=False))
    return prompt, inputs


def _start_batch(reader, state, number, batch):
    directory = Path(state["directory"])
    prefix = directory / f"batch-{number:04d}"
    schema = prefix.with_suffix(".schema.json")
    output = prefix.with_suffix(".result.json")
    assembly_only = (state.get('input_mode') == 'assembly'
                     or all(fn.get('input_mode') == 'assembly' for fn in batch))
    initial = state.get("mode") == "initial" and not assembly_only
    feedback = state.get('mode') == 'comment'
    _write(schema, _schema(batch, initial=initial, feedback=feedback))
    current = {fn["rva"]: read_candidate(reader, fn["rva"]) for fn in batch}
    before = {rva: item["sha256"] for rva, item in current.items()}
    knowledge = {rva: read_knowledge(reader, rva) for rva in current} if feedback else {}
    if feedback:
        _write(prefix.with_suffix('.before.json'), {
            str(rva): {**{key: item[key] for key in ('display_code', 'sha256', 'path')}, 'knowledge': knowledge[rva]}
            for rva, item in current.items()})
    references = read_references(reader, list(before)) if not assembly_only and state.get("ghidra", {}).get("status") == "ready" else []
    if assembly_only:
        from .assembly import assembly_prompt
        prompt = assembly_prompt(reader, state, prefix, batch)
        if feedback:
            prompt += (PROMPTS / 'ASSEMBLY_FEEDBACK.md').read_text(encoding='utf-8')
    elif initial:
        prompt, inputs = _initial_prompt(reader, state, batch, current, references)
        # Immutable prompt source, allowing edits and interrupted publication to
        # be replayed against exactly the code that the model saw.
        _write(prefix.with_suffix('.input.json'), inputs)
    elif feedback:
        prompt, inputs = _feedback_prompt(reader, state, current, knowledge)
        _write(prefix.with_suffix('.input.json'), inputs)
    else:
        reference_text = json.dumps(references, ensure_ascii=False)
        modules = sorted({module_name(path.stem) for path in (reader.directory / "source/src").glob("*.c")})
        mapping = reader.directory / 'references/ghidra/boundaries.json'
        boundaries = [item for item in json.loads(mapping.read_text()).get('changes', [])
                      if item.get('new_rva') in before] if mapping.exists() else []
        prompt = render_prompt(PROMPTS / 'TARGETED.md',
            TASK=directory / 'task.md',
            FUNCTIONS=json.dumps(batch, ensure_ascii=False),
            SOURCE_DIR=SOURCE_DIR,
            DIRECTORY=reader.directory,
            PYTHON=shlex.quote(sys.executable),
            REFERENCES=reference_text,
            REFERENCES_TOO_LONG=len(reference_text) > 60000,
            OBJDUMP=shutil.which('aarch64-linux-gnu-objdump'),
            READELF=shutil.which('aarch64-linux-gnu-readelf'),
            RIZIN=TOOLS_DIR / 'rizin/bin/rizin',
            MODULES=json.dumps(modules),
            BOUNDARIES=json.dumps(boundaries, ensure_ascii=False))
    if not initial:
        prompt = skill_catalog(prefix, assembly_only=assembly_only, feedback=feedback) + '\n' + prompt
    prompt_path = prefix.with_suffix('.prompt.md')
    prompt_path.write_text(prompt, encoding="utf-8")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise BatchFailure(
            f'完整提示词含 {len(prompt)} 个字符，超过 Codex 单条输入上限 {MAX_PROMPT_CHARS}；'
            f'未截断输入、未调用模型，完整提示词保存在 {prompt_path}', retryable=False)
    command = [state["codex"], "-a", "never", "exec", "--sandbox",
               os.environ.get("LIGHTRE_CODEX_SANDBOX", "danger-full-access"),
               "--skip-git-repo-check", "--ephemeral", "-C", str(reader.directory), "--json",
               "--output-schema", str(schema), "-o", str(output)]
    if initial:
        command.extend(['--disable', 'shell_tool', '--disable', 'plugins', '--disable', 'apps',
                        '--disable', 'multi_agent', '-c', 'model_reasoning_effort="low"'])
    elif assembly_only or feedback:
        command.extend(['--disable', 'plugins', '--disable', 'apps', '--disable', 'multi_agent'])
    profile = os.environ.get("LIGHTRE_CODEX_PROFILE")
    if profile:
        command.extend(["-p", profile])
    command.append("-")
    started_at, started_clock = time.time(), time.monotonic()
    # Let the child read the durable prompt directly. Writing a large PIPE in
    # the dispatcher can block all other batches while Codex is starting up.
    with prompt_path.open('rb') as input_file, \
         prefix.with_suffix(".events.jsonl").open("ab") as events, \
         prefix.with_suffix(".stderr.log").open("ab") as errors:
        try:
            process = subprocess.Popen(command, stdin=input_file, stdout=events, stderr=errors,
                                       start_new_session=True)
        except OSError as error:
            raise BatchFailure(f'无法启动 Codex：{error}') from error
    return {"process": process, "prefix": prefix, "before": before, "batch": batch,
            "started_at": started_at, "started_clock": started_clock,
            "input_mode": 'assembly' if assembly_only else 'reference',
            "timeout": INITIAL_BATCH_TIMEOUT if initial else FEEDBACK_BATCH_TIMEOUT}


def _feedback_knowledge(item, baseline):
    """Resolve the current document separately from the transient task reply."""
    if (not isinstance(item.get('reply'), str) or not item['reply'].strip()
            or not {'notes', 'knowledge_edits', 'edits'} <= item.keys()):
        raise ValueError('评论结果缺少独立回复或知识更新')
    edits = item['knowledge_edits']
    if edits is None:
        notes = item['notes']
    else:
        notes = baseline['knowledge']
        if item['notes'] is not None or not isinstance(edits, list) or not notes.strip():
            raise ValueError('知识补丁需要既有正文，不能同时返回完整 notes')
        for edit in edits:
            if (not isinstance(edit, dict) or not isinstance(edit.get('old'), str)
                    or not edit['old'] or notes.count(edit['old']) != 1
                    or not isinstance(edit.get('new'), str)):
                raise ValueError('知识补丁的 old 必须在当前正文恰好出现一次')
            notes = notes.replace(edit['old'], edit['new'], 1)
    if not isinstance(notes, str) or not notes.strip():
        raise ValueError('整合后的知识不能为空')
    return _normalize(notes)


def _publish_batch(reader, state, running):
    """Save every valid item, including partial output from an unsuccessful CLI."""
    from .candidates import canonical_candidate, display_code
    prefix, before = running['prefix'], running['before']
    error = running.get('failure')
    limited = False
    if running['process'].returncode:
        stderr = prefix.with_suffix('.stderr.log')
        text = stderr.read_text(encoding='utf-8', errors='replace') if stderr.exists() else ''
        limited = '429' in text or 'rate_limit' in text.lower()
        error = error or f"Codex 退出码 {running['process'].returncode}，日志：{stderr}"
    try:
        results = json.loads(prefix.with_suffix('.result.json').read_text())['results']
        if not isinstance(results, list):
            raise ValueError('results 必须是列表')
    except (ValueError, KeyError, TypeError, OSError) as problem:
        raise BatchFailure(error or f'Codex 输出缺失或无效：{problem}', rate_limited=limited) from problem
    addresses = []
    for item in results:
        try:
            addresses.append(int(item['rva'], 0))
        except (KeyError, ValueError, TypeError):
            addresses.append(None)
    counts = Counter(addresses)
    valid = [(rva, item) for rva, item in zip(addresses, results)
             if rva in before and counts[rva] == 1 and isinstance(item, dict)
             and 'code' in item
             and (item.get('code') is None or isinstance(item.get('code'), str) and item['code'].strip())]
    done = set(state.setdefault('done_rvas', []))
    notes, published, generated = {}, [], 0
    meta = reader.catalog()['sample']
    inputs_path = prefix.with_suffix('.input.json')
    inputs = json.loads(inputs_path.read_text()) if inputs_path.exists() else {}
    baseline_path = prefix.with_suffix('.before.json')
    baselines = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    for rva, item in valid:
        if rva in done:
            continue
        # A malformed model note must not hide subsequent H2 entries or stop
        # unrelated functions. Validate before publishing any source for it.
        baseline = baselines.get(str(rva))
        feedback = state.get('mode') == 'comment' and baseline is not None and 'knowledge' in baseline
        try:
            if feedback:
                knowledge = _feedback_knowledge(item, baseline)
            else:
                if not isinstance(item.get('notes'), str) or not item['notes'].strip():
                    continue
                knowledge = _normalize(item['notes'])
            _validate(_entry(rva, knowledge), {rva: knowledge})
        except ValueError:
            continue
        reply = item['reply'].strip() if feedback else knowledge
        code = item['code']
        if item.get('edits') is not None:
            if code is not None or not isinstance(item['edits'], list):
                continue
            code = inputs.get(f'{rva:#x}')
            if not code:
                continue
            for edit in item['edits']:
                if (not isinstance(edit, dict) or not isinstance(edit.get('old'), str)
                        or not edit['old'] or edit['old'] not in code or not isinstance(edit.get('new'), str)):
                    code = None
                    break
                code = code.replace(edit['old'], edit['new'])
            if not code or not code.strip():
                continue
        try:
            code = display_code(meta, rva, code) if code else None
            if code:
                try:
                    code = format_code(reader, code)
                except FormattingError as format_error:
                    warning = f'源码格式化未完成，保留本轮原文：{format_error}'
                    reply += '\n\n' + warning
                    if not feedback:
                        knowledge += '\n\n' + warning
            current = read_candidate(reader, rva)
            canonical = canonical_candidate(meta, rva, code, current['code']) if code else None
        except ValueError:
            continue
        conflict = current['sha256'] != before[rva] and current['code'] != canonical
        knowledge_conflict = (feedback and read_knowledge(reader, rva) not in (baseline['knowledge'], knowledge))
        conflict = conflict or knowledge_conflict
        if conflict:
            reply = ('分析期间源码或 Knowledge 被修改，保留当前文件；本轮源码与知识更新未发布。'
                     '\n\n模型拟议回复（未采用）：\n' + reply)
            code = None
        if code:
            module = next(fn.get('module', 'recovered') for fn in running['batch'] if fn['rva'] == rva)
            if current['code'] != canonical:
                save_candidate(reader, rva, code, module=module)
            generated += 1
            state['unresolved'].pop(f'{rva:#x}', None)
        else:
            state['unresolved'][f'{rva:#x}'] = reply or '本轮未确定完整逻辑，需后续分析'
        if state.get('mode') == 'comment':
            from .feedback import record_result
            if baseline is None and current['sha256'] == before[rva]:
                baseline = {key: current[key] for key in ('display_code', 'sha256', 'path')}
            record_result(state, rva, baseline, read_candidate(reader, rva), reply,
                          'conflict' if conflict else 'unresolved' if not code else None)
        if conflict:
            pass  # A proposal based on stale input must never replace current knowledge.
        elif state.get('mode') == 'comment' and inputs_path.exists() and not feedback:
            # Replay results from before the consolidated-document protocol.
            previous = read_knowledge(reader, rva)
            notes[rva] = (previous + '\n\n### 建议处理\n' + knowledge) if previous else knowledge
        else:
            notes[rva] = knowledge
        published.append(rva)
    if notes:
        save_knowledge_batch(reader, notes)
    state['generated'] += generated
    state['done_rvas'].extend(published)
    state['processed'] = len(set(state['done_rvas']))
    for rva in published:
        state.setdefault('failed_rvas', {}).pop(f'{rva:#x}', None)
    missing = [fn for fn in running['batch'] if fn['rva'] not in set(state['done_rvas'])]
    if missing:
        raise BatchFailure(error or 'Codex 未返回本批全部有效函数结果', missing, limited)


def _record_failed(reader, state, batch, reason):
    """Execution failures remain distinct from LLM findings and never erase notes."""
    notes = {}
    for fn in batch:
        rva = fn['rva']
        message = '模型调用执行失败（非模型分析结论）：' + reason
        state.setdefault('failed_rvas', {})[f'{rva:#x}'] = message
        state['unresolved'][f'{rva:#x}'] = message
        if state.get('mode') != 'comment':
            notes[rva] = read_knowledge(reader, rva) + '\n\n### 执行失败\n' + message
    save_knowledge_batch(reader, notes)
    state['done_rvas'].extend(fn['rva'] for fn in batch)
    state['processed'] = len(set(state['done_rvas']))


def _terminate(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def task_steps(reader, identifier=None, pool=None):
    """Advance one task without blocking other tasks while its CLIs run."""
    from .queue import has_priority
    state_path = (reader.directory / 'tasks' / identifier / 'state.json' if identifier
                  else reader.directory / 'tasks/state.json')
    state = json.loads(state_path.read_text())
    run_started_clock = time.monotonic()
    state.update(status='running', pid=os.getpid(), run_started_at=time.time())
    state.setdefault('started_at', time.time())
    state.setdefault('done_rvas', [])
    state.setdefault('failed_rvas', {})
    state.setdefault('failure_counts', {})
    state.setdefault('batches', {})
    state.pop('error', None)
    directory = Path(state['directory'])
    existing = [int(path.name.split('-')[1].split('.')[0]) for path in directory.glob('batch-*.prompt.md')]
    state.setdefault('next_batch', max(existing, default=-1) + 1)
    workers = state.setdefault('workers', 8)
    state.setdefault('effective_workers', workers)
    state['effective_workers'] = min(workers, state['effective_workers'])
    timeout = (INITIAL_BATCH_TIMEOUT if state.get('mode') == 'initial' and state.get('input_mode') != 'assembly'
               else FEEDBACK_BATCH_TIMEOUT)
    state['batch_timeout'] = timeout
    active, pending = {}, deque()

    def checkpoint():
        state['active_batches'] = len(active)
        state['current'] = [f"{fn['rva']:#x}" for running in active.values() for fn in running['batch']]
        state['failed_count'] = len(state['failed_rvas'])
        _write(state_path, state)

    def failed(number, batch, problem):
        remaining = problem.missing if problem.missing is not None else batch
        record = state['batches'].setdefault(str(number), {})
        record.update(status='failed', error=str(problem), finished_at=time.time())
        if problem.rate_limited:
            state['effective_workers'] = max(1, state['effective_workers'] // 2)
        retry, exhausted = [], []
        for fn in remaining:
            key = f"{fn['rva']:#x}"
            count = state['failure_counts'].get(key, 0) + 1
            state['failure_counts'][key] = count
            (retry if problem.retryable and count <= MAX_BATCH_RETRIES else exhausted).append(fn)
        if exhausted:
            _record_failed(reader, state, exhausted, str(problem))
        if retry:
            # Shorter retry output helps a truncated/malformed response finish;
            # rate limits instead keep one batch and reduce concurrent requests.
            width = len(retry) if problem.rate_limited else max(1, (len(retry) + 1) // 2)
            for offset in range(0, len(retry), width):
                pending.append({'batch': retry[offset:offset + width], 'ready_at': time.monotonic() + RETRY_BACKOFF})
            record['status'] = 'retry_wait'
        checkpoint()

    try:
        if (directory / 'cancel').exists():
            raise InterruptedError('任务已取消')
        migrate_candidates(reader)
        migrate_knowledge(reader)
        functions = {fn['rva']: dict(fn) for fn in reader.catalog()['functions']}
        if state.get('input_mode') == 'assembly' or state.get('mode') == 'comment':
            state['ghidra'] = {'status': 'skipped', 'reason': 'assembly input mode or focused feedback'}
            references = []
        else:
            state['phase'] = 'ghidra'
            checkpoint()
            state['ghidra'] = prepare_references(reader)
            references = read_references(reader, list(functions)) if state['ghidra']['status'] == 'ready' else []
            state['phase'] = 'source'
            checkpoint()
            state['source'] = prepare_source(reader, references)
        for item in references:
            if item['rva'] in functions:
                functions[item['rva']]['module'] = module_name(item.get('module', 'recovered'))
                functions[item['rva']]['context_chars'] = (len(item.get('code') or '')
                                                          + len(read_knowledge(reader, item['rva'])))
        reference_codes = {item['rva']: item.get('code') for item in references}
        for rva in state['rvas']:
            fn = functions[rva]
            fn['input_mode'] = state.get('input_mode', 'reference')
            # Missing drafts need all instructions and file-reading tools in
            # this same pass, rather than an unusable head/tail-only prompt.
            if (state.get('mode') == 'initial' and fn['input_mode'] == 'reference'
                    and not reference_codes.get(rva) and not read_candidate(reader, rva)['code']):
                fn['input_mode'] = 'assembly'
        state['phase'] = 'recovery'
        # A CLI may have finished just before worker interruption. Consume its
        # saved output first; don't discard it or pay for the same function again.
        for number, record in list(state['batches'].items()):
            if record.get('status') != 'running':
                continue
            batch = [functions[rva] for rva in record['rvas'] if rva in functions]
            prefix = directory / f'batch-{int(number):04d}'
            if prefix.with_suffix('.result.json').exists():
                running = {'process': SimpleNamespace(returncode=0), 'prefix': prefix, 'batch': batch,
                           'before': {int(rva): digest for rva, digest in record['before'].items()}}
                try:
                    _publish_batch(reader, state, running)
                    record.update(status='complete', recovered=True, finished_at=time.time())
                except BatchFailure:
                    record['status'] = 'interrupted'
            else:
                record['status'] = 'interrupted'
        checkpoint()
        done = set(state['done_rvas'])
        functions_to_run = sorted((functions[rva] for rva in state['rvas'] if rva not in done),
                                  key=lambda fn: (fn.get('module', 'recovered'), fn['instructions'], fn['rva']))
        pending.extend({'batch': batch, 'ready_at': 0} for batch in _batches(functions_to_run))
        while active or pending:
            if (directory / 'cancel').exists():
                raise InterruptedError('任务已取消')
            if (reader.directory / 'tasks/.paused').exists():
                raise InterruptedError('队列已暂停')
            yielding = pool is None and identifier is not None and has_priority(reader, state)
            if yielding and not active:
                state['status'] = 'queued'
                return
            while not yielding and len(active) < state['effective_workers']:
                index = next((i for i, entry in enumerate(pending) if entry['ready_at'] <= time.monotonic()
                              and (pool is None or pool.available(state, entry['batch']))), None)
                if index is None:
                    break
                entry = pending[index]
                del pending[index]
                batch = entry['batch']
                if pool is not None:
                    batch = pool.available(state, batch)
                    blocked = [fn for fn in entry['batch'] if fn not in batch]
                    if blocked:
                        pending.appendleft({**entry, 'batch': blocked})
                exhausted = [fn for fn in batch if state['failure_counts'].get(f"{fn['rva']:#x}", 0) > MAX_BATCH_RETRIES]
                if exhausted:
                    _record_failed(reader, state, exhausted, '此前批次已耗尽重试次数')
                    batch = [fn for fn in batch if fn not in exhausted]
                    checkpoint()
                    if not batch:
                        continue
                if state.get('mode') == 'initial':
                    present = candidate_rvas(reader, optimized_only=True)
                    skipped = [fn['rva'] for fn in batch if fn['rva'] in present]
                    state['done_rvas'].extend(skipped)
                    state['processed'] = len(set(state['done_rvas']))
                    batch = [fn for fn in batch if fn['rva'] not in present]
                    if not batch:
                        continue
                number = state['next_batch']
                state['next_batch'] += 1
                try:
                    running = _start_batch(reader, state, number, batch)
                except BatchFailure as problem:
                    failed(number, batch, problem)
                    continue
                active[number] = running
                if pool is not None:
                    pool.acquire(state['id'], number, batch)
                state['batches'][str(number)] = {'status': 'running', 'rvas': [fn['rva'] for fn in batch],
                                                'before': running['before'], 'started_at': running['started_at'],
                                                'input_mode': running['input_mode'], 'timeout': running['timeout']}
                checkpoint()
            completed = []
            for number, running in list(active.items()):
                returncode = running['process'].poll()
                batch_timeout = running['timeout']
                if returncode is None and time.monotonic() - running['started_clock'] >= batch_timeout:
                    running['failure'] = f'Codex 批次超时（{batch_timeout} 秒）'
                    _terminate(running['process'])
                    returncode = running['process'].returncode
                if returncode is not None:
                    completed.append(number)
                    del active[number]
                    try:
                        _publish_batch(reader, state, running)
                        state['batches'][str(number)].update(status='complete', finished_at=time.time())
                    except BatchFailure as problem:
                        failed(number, running['batch'], problem)
                    finally:
                        if pool is not None:
                            pool.release(state['id'], number)
                    checkpoint()
            if active or pending:
                yield
        state['status'] = 'partial' if state['unresolved'] or state['failed_rvas'] else 'complete'
        if state.get('mode') == 'initial' and state.get('auto_refine') and not state.get('followup_id'):
            unresolved = sorted(int(rva, 0) for rva in state['unresolved'] if rva not in state['failed_rvas'])
            if unresolved:
                from .queue import submit
                followup = submit(reader, unresolved, workers=workers, parent_task=state['id'])
                state['followup_id'] = followup['id']
    except (InterruptedError, GeneratorExit) as error:
        state.update(status='cancelled' if (directory / 'cancel').exists() else 'interrupted',
                     error=str(error) or '任务执行已中断，未完成函数可继续')
    except Exception as error:
        # Storage/provenance failures remain task errors; CLI failures above are
        # isolated and never terminate other healthy batches.
        state.update(status='error', error=str(error))
    finally:
        for number, running in active.items():
            _terminate(running['process'])
            if pool is not None:
                pool.release(state['id'], number)
        active.clear()
        if state['status'] in ('complete', 'partial', 'error', 'cancelled'):
            state['finished_at'] = time.time()
        state['run_duration_seconds'] = round(time.monotonic() - run_started_clock, 3)
        checkpoint()


def run_task(reader, identifier=None):
    """Standalone entry retained for tools and recovery of legacy task files."""
    def interrupted(signum, frame):
        raise InterruptedError('用户停止了任务；已保存候选保留，未完成函数可继续')

    previous = {signum: signal.signal(signum, interrupted) for signum in (signal.SIGTERM, signal.SIGINT)}
    steps = task_steps(reader, identifier)
    try:
        for _ in steps:
            time.sleep(0.1)
    except InterruptedError as error:
        try:
            steps.throw(error)
        except StopIteration:
            pass
    finally:
        steps.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
