import fcntl
import json
import io
import os
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from recovery import merge_sources, read_candidate, save_candidate, read_knowledge
from recovery.candidates import GHIDRA_MARKER, candidate_rvas, migrate_candidates
from recovery.jobs import BusyError, _batches, progress, run_task, start_task, task_state
from recovery.source import prepare_source
from workspace import RESULT_SCHEMA, WorkspaceReader


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.reader = self.make_reader("one", "a" * 64)
        ghidra = patch("recovery.jobs.prepare_references", return_value={"status": "unavailable"})
        ghidra.start()
        self.addCleanup(ghidra.stop)
        # Storage/queue fixtures keep exact input text; formatter integration has
        # separate tiny fixtures and does not share the fake Codex Popen calls.
        for target in ('recovery.jobs.format_code', 'recovery.source.format_code'):
            formatter = patch(target, side_effect=lambda reader, code: code)
            formatter.start()
            self.addCleanup(formatter.stop)

    def make_reader(self, name, digest):
        directory = self.directory / name
        directory.mkdir()
        with sqlite3.connect(directory / "index.sqlite") as conn:
            conn.executescript("""
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE functions (rva INTEGER PRIMARY KEY, name TEXT,
                                        instructions INTEGER, ranges TEXT);
                CREATE TABLE assembly (function_rva INTEGER, ordinal INTEGER,
                                       rva INTEGER, bytes TEXT, opcode TEXT);
                INSERT INTO functions VALUES (4,'test',0,'[]');
            """)
            conn.executemany("INSERT INTO metadata VALUES (?,?)", {
                "name": name, "sha256": digest, "source_path": "/inputs/test.so",
                "state": "ready", "schema_version": RESULT_SCHEMA,
            }.items())
        return WorkspaceReader(directory)

    def test_initial_unknown_stops_after_one_pass_unless_legacy_job_requested_followup(self):
        from recovery.queue import snapshot, submit
        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/fake/codex'):
            initial = submit(self.reader)
            class FakeCodex:
                returncode = 0
                def __init__(process, command, **kwargs):
                    process.output = Path(command[command.index('-o') + 1])
                    process.stdin = io.BytesIO()
                def poll(process):
                    process.output.write_text(json.dumps({'results': [
                        {'rva': '0x4', 'code': None, 'notes': '完整控制流仍缺失'}]}))
                    return 0
            with patch('recovery.jobs.subprocess.Popen', FakeCodex), patch('recovery.jobs.signal.signal'):
                run_task(self.reader, initial['id'])
                first = json.loads((Path(initial['directory']) / 'state.json').read_text())
                self.assertFalse(first['auto_refine'])
                self.assertNotIn('followup_id', first)
                self.assertEqual(len(snapshot(self.reader)), 1)
                # Persisted jobs retain their explicit continuation policy.
                first['auto_refine'] = True
                (Path(initial['directory']) / 'state.json').write_text(json.dumps(first))
                run_task(self.reader, initial['id'])
                first = json.loads((Path(initial['directory']) / 'state.json').read_text())
                followup = next(job for job in snapshot(self.reader) if job['id'] == first['followup_id'])
                self.assertEqual(followup['parent_task'], initial['id'])
                self.assertEqual(followup['mode'], 'targeted')
                self.assertFalse(followup['auto_refine'])
                self.assertIn('自动续做', (Path(followup['directory']) / 'task.md').read_text())
                run_task(self.reader, followup['id'])
        self.assertEqual(len(snapshot(self.reader)), 2)
        self.assertEqual([job['status'] for job in snapshot(self.reader)], ['partial', 'partial'])

    def test_assembly_mode_supplies_every_page_without_ghidra_or_seed_code(self):
        from recovery.queue import submit
        from recovery import save_knowledge
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute("UPDATE functions SET instructions=201,ranges='[[4,808]]'")
            conn.executemany('INSERT INTO assembly VALUES (4,?,?,?,?)', [
                (n, 4 + n * 4, 'c0035fd6' if n == 200 else '00040091',
                 'ret' if n == 200 else 'add x0, x0, #1') for n in range(201)])
        save_knowledge(self.reader, 4, '入口参数位于 x0，尚未执行验证。')
        reader, commands = self.reader, []

        class FakeCodex:
            returncode = 0
            def __init__(process, command, **kwargs):
                commands.append(command)
                process.output = Path(command[command.index('-o') + 1])
            def poll(process):
                process.output.write_text(json.dumps({'results': [
                    {'rva': '0x4', 'code': 'long test(long value) { return value + 200; }',
                     'notes': '按完整保存汇编整理，未经执行验证。'}]}))
                return 0

        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/fake/codex'):
            job = submit(reader, input_mode='assembly')
        with patch('recovery.jobs.prepare_references', side_effect=AssertionError('no Ghidra')), \
             patch('recovery.jobs.read_references', side_effect=AssertionError('no pseudocode')), \
             patch('recovery.jobs.prepare_source', side_effect=AssertionError('no draft seeding')), \
             patch('recovery.jobs.subprocess.Popen', FakeCodex), patch('recovery.jobs.signal.signal'):
            run_task(reader, job['id'])
        directory = Path(job['directory'])
        text = (directory / 'batch-0000-0x00000004.asm').read_text()
        instructions = [line for line in text.splitlines() if not line.startswith('#')]
        self.assertEqual(len(instructions), 201)
        self.assertIn('00000324  c0035fd6  ret', instructions[-1])
        self.assertIn('x0', (directory / 'batch-0000-0x00000004.knowledge.md').read_text())
        prompt = (directory / 'batch-0000.prompt.md').read_text()
        inline = prompt.split('```asm\n', 1)[1].split('\n```', 1)[0].splitlines()
        self.assertEqual(len(inline), 201)
        self.assertEqual(inline[0], '4 add x0,x0,#1')
        self.assertEqual(inline[-1], '324 ret')
        self.assertIn('入口参数位于 x0', prompt)
        command = commands[0]
        self.assertNotIn('shell_tool', command)
        self.assertNotIn('model_reasoning_effort="low"', command)
        schema = json.loads((directory / 'batch-0000.schema.json').read_text())
        self.assertNotIn('edits', schema['properties']['results']['items']['properties'])
        state = json.loads((directory / 'state.json').read_text())
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(state['ghidra']['status'], 'skipped')
        self.assertEqual(state['batch_timeout'], 600)
        self.assertNotIn('followup_id', state)
        self.assertIn('value + 200', read_candidate(reader, 4)['code'])

    def test_queue_keeps_assembly_requests_distinct_and_rejects_invalid_mode(self):
        from recovery.queue import submit
        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/fake/codex'):
            reference = submit(self.reader)
            assembly = submit(self.reader, input_mode='assembly')
            self.assertNotEqual(reference['id'], assembly['id'])
            self.assertEqual(submit(self.reader, input_mode='assembly')['id'], assembly['id'])
            self.assertEqual(submit(self.reader)['id'], reference['id'])
            with self.assertRaisesRegex(ValueError, 'input_mode'):
                submit(self.reader, input_mode='invalid')

    def test_default_pass_routes_missing_drafts_to_complete_assembly_batches(self):
        from recovery.queue import snapshot, submit
        sizes = {4: 4001, 8: 1, 12: 201}
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute("UPDATE functions SET instructions=4001,ranges='[[4,16008]]'")
            conn.executemany("INSERT INTO functions VALUES (?,'test',?,'[]')", [(8, 1), (12, 201)])
            for rva, count in sizes.items():
                conn.executemany('INSERT INTO assembly VALUES (?,?,?,?,?)', [
                    (rva, n, rva + n * 4, 'c0035fd6' if n == count - 1 else '00040091',
                     'ret' if n == count - 1 else 'add x0, x0, #1') for n in range(count)])
        references = self.reader.directory / 'references/ghidra'
        references.mkdir(parents=True)
        draft = 'long fcn_00000008(long value) { return value; }'
        (references / 'functions.json').write_text(json.dumps({
            'sha256': 'a' * 64, 'functions': [
                {'rva': rva, 'module': '.text', 'code': draft if rva == 8 else None,
                 'status': 'ok' if rva == 8 else 'timeout'} for rva in sizes]}))
        commands, clock = {}, [0]

        class FakeCodex:
            returncode = None
            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])
                schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
                props = schema['properties']['results']['items']['properties']
                process.rvas = props['rva']['enum']
                process.polls = 0
                for rva in process.rvas:
                    commands[int(rva, 0)] = (command, props)
            def poll(process):
                process.polls += 1
                if process.rvas != ['0x8'] and process.polls == 1:
                    clock[0] += 200  # Assembly batches must survive the fast-pass 180s deadline.
                    return None
                process.output.write_text(json.dumps({'results': [
                    {'rva': rva, 'code': None, 'edits': [], 'notes': '保留初稿，未经执行验证。'}
                    if int(rva, 0) == 8 else
                    {'rva': rva, 'code': f'long fcn_{int(rva, 0):08x}(long value) {{ return value + {sizes[int(rva, 0)] - 1}; }}',
                     'notes': '读取完整汇编，未经执行验证。'} for rva in process.rvas]}))
                process.returncode = 0
                return 0

        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/fake/codex'):
            job = submit(self.reader, workers=1)
        with patch('recovery.jobs.prepare_references', return_value={'status': 'ready'}), \
             patch('recovery.jobs.subprocess.Popen', FakeCodex), patch('recovery.jobs.signal.signal'), \
             patch('recovery.jobs.time.monotonic', side_effect=lambda: clock[0]), patch('recovery.jobs.time.sleep'), \
             patch('recovery.jobs._terminate', side_effect=AssertionError('assembly deadline is 600s')):
            run_task(self.reader, job['id'])
        directory = Path(job['directory'])
        state = json.loads((directory / 'state.json').read_text())
        self.assertEqual(state['input_mode'], 'reference')
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(state['generated'], 3)
        self.assertEqual(len(state['batches']), 3)
        self.assertEqual(len(snapshot(self.reader)), 1)
        self.assertNotIn('followup_id', state)
        for record in state['batches'].values():
            self.assertEqual(len(record['rvas']), 1)
            rva = record['rvas'][0]
            assembly_only = rva != 8
            self.assertEqual(record['input_mode'], 'assembly' if assembly_only else 'reference')
            self.assertEqual(record['timeout'], 600 if assembly_only else 180)
            command, props = commands[rva]
            self.assertEqual('edits' not in props, assembly_only)
            self.assertEqual('shell_tool' not in command, assembly_only)
            self.assertEqual('model_reasoning_effort="low"' not in command, assembly_only)
            if assembly_only:
                path = next(directory.glob(f'batch-*-{rva:#010x}.asm'))
                lines = [line for line in path.read_text().splitlines() if not line.startswith('#')]
                self.assertEqual(len(lines), sizes[rva])
                self.assertTrue(lines[-1].endswith('ret'))
                prompt = path.with_name(path.name.split(f'-{rva:#010x}')[0] + '.prompt.md').read_text()
                inline = prompt.split('```asm\n', 1)[1].split('\n```', 1)[0].splitlines()
                self.assertEqual(len(inline), sizes[rva])
                self.assertEqual(inline[-1], f'{rva + (sizes[rva] - 1) * 4:x} ret')
                self.assertIn(f'value + {sizes[rva] - 1}', read_candidate(self.reader, rva)['code'])
            else:
                self.assertIn(draft, read_candidate(self.reader, rva)['display_code'])
        self.assertEqual(candidate_rvas(self.reader, optimized_only=True), set(sizes))

    def test_inline_assembly_normalizes_only_direct_branch_addresses(self):
        from recovery.assembly import _inline_instruction
        def render(opcode):
            return _inline_instruction({'rva': 4, 'opcode': opcode}, 0x100000)
        self.assertEqual(render('cbz w8, 0x00100100'), '4 cbz w8,0x100')
        self.assertEqual(render('b.eq 0x00100100'), '4 b.eq 0x100')
        self.assertEqual(render('bl 0x00100100'), '4 bl 0x100')
        self.assertEqual(render('mov x0, #0x100100'), '4 mov x0,#0x100100')
        self.assertEqual(render('adr x8, 0x00100100'), '4 adr x8,0x00100100')
        self.assertEqual(render('blr x8'), '4 blr x8')

    def test_oversized_prompt_keeps_complete_input_without_calling_model_or_retrying(self):
        from recovery.queue import submit
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute("UPDATE functions SET instructions=1,ranges='[[4,8]]'")
            conn.execute("INSERT INTO assembly VALUES (4,0,4,'c0035fd6','ret')")
        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/fake/codex'):
            job = submit(self.reader, input_mode='assembly')
        with patch('recovery.jobs.MAX_PROMPT_CHARS', 100), \
             patch('recovery.jobs.subprocess.Popen', side_effect=AssertionError('input rejected before model')), \
             patch('recovery.jobs.signal.signal'):
            run_task(self.reader, job['id'])
        directory = Path(job['directory'])
        state = json.loads((directory / 'state.json').read_text())
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(state['failed_count'], 1)
        self.assertEqual(state['failure_counts'], {'0x4': 1})
        self.assertEqual(state['generated'], 0)
        self.assertEqual(len(state['batches']), 1)
        self.assertEqual(state['batches']['0']['status'], 'failed')
        self.assertIn('未截断输入、未调用模型', state['failed_rvas']['0x4'])
        self.assertIn('超过 Codex 单条输入上限 100', state['failed_rvas']['0x4'])
        self.assertIn('```asm\n4 ret\n```', (directory / 'batch-0000.prompt.md').read_text())
        self.assertEqual(read_candidate(self.reader, 4)['status'], 'missing')

    def test_format_failure_and_partial_output_do_not_abort_retries(self):
        from recovery.formatting import FormattingError
        state_path = self.parallel_task()
        state = json.loads(state_path.read_text())
        state['rvas'] = [4, 8]
        state_path.write_text(json.dumps(state))
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute('UPDATE functions SET instructions=0')
        calls = []
        class FakeCodex:
            returncode = 0
            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])
                process.stdin = io.BytesIO()
                schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
                process.rvas = schema['properties']['results']['items']['properties']['rva']['enum']
                calls.extend(process.rvas)
            def poll(process):
                rvas = process.rvas[:1] if len(process.rvas) > 1 else process.rvas
                process.output.write_text(json.dumps({'results': [
                    {'rva': rva, 'code': 'void recovered(void) {}', 'notes': '保留实际副作用'} for rva in rvas]}))
                return 0
        with patch('recovery.jobs.subprocess.Popen', FakeCodex), \
             patch('recovery.jobs.format_code', side_effect=FormattingError('incomplete draft')), \
             patch('recovery.jobs.signal.signal'), patch('recovery.jobs.RETRY_BACKOFF', 0):
            run_task(self.reader)
        state = json.loads(state_path.read_text())
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(state['generated'], 2)
        self.assertEqual(calls.count('0x8'), 2)
        self.assertIn('格式化未完成', read_knowledge(self.reader, 4))

    def test_persists_exact_source_fragment_and_rebuilds_index_after_external_edits(self):
        database = self.reader.database.read_bytes()
        with patch("subprocess.Popen", side_effect=AssertionError("must not run tools")):
            self.assertEqual(read_candidate(self.reader, 4)["status"], "missing")
            self.assertFalse((self.reader.directory / "candidates").exists())
            self.assertEqual(self.reader.database.read_bytes(), database)
            path = save_candidate(self.reader, 4, "int test(void) { return 1; }\n")
            first = read_candidate(WorkspaceReader(self.reader.directory), 4)
            self.assertIn(first["code"], path.read_text())
            self.assertIn('"origins":["0x4"]', first["code"])
            self.assertNotIn("a" * 64, first["code"])
            self.assertEqual(first['display_code'], 'int test(void) { return 1; }\n')
            path.write_text(path.read_text().replace("return 1", "return 2"))
            self.assertIn("return 2", read_candidate(self.reader, 4)["code"])
            save_candidate(self.reader, 4, read_candidate(self.reader, 4)["code"])
            self.assertEqual(path.read_text().count("// @lightre.begin"), 1)
            path.unlink()
            self.assertEqual(read_candidate(self.reader, 4)["status"], "missing")
        with self.reader._connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM source_index").fetchone()[0], 0)

    def test_same_rva_in_another_binary_is_independent_and_legacy_identity_is_checked(self):
        from recovery.candidates import _legacy_header, _identity
        other = self.make_reader("two", "b" * 64)
        path = save_candidate(self.reader, 4, "void test(void) {}\n")
        self.assertIsNone(read_candidate(other, 4)["code"])
        meta, _ = _identity(self.reader)
        legacy = _legacy_header(meta, 4) + '\nvoid test(void) {}\n'
        with self.assertRaisesRegex(ValueError, "来源注释"):
            save_candidate(other, 4, legacy)
        # New local markers deliberately omit binary identity; each directory
        # supplies that identity. Equal local IDs never join separate databases.
        save_candidate(other, 4, 'void other(void) {}')
        self.assertIn('void test', read_candidate(self.reader, 4)['display_code'])
        self.assertIn('void other', read_candidate(other, 4)['display_code'])

    def test_unknown_function_and_empty_code_do_not_create_candidate_files(self):
        with self.assertRaises(KeyError):
            save_candidate(self.reader, 8, "void missing(void) {}")
        with self.assertRaises(KeyError):
            read_candidate(self.reader, 8)
        with self.assertRaises(ValueError):
            save_candidate(self.reader, 4, " \n")
        self.assertFalse((self.reader.directory / "candidates").exists())

    def test_web_body_hides_only_metadata_and_keeps_raw_hash_and_producer(self):
        body = '// Keep this explanation\nint test(void) { return 1; }\n'
        save_candidate(self.reader, 4, GHIDRA_MARKER + body)
        candidate = read_candidate(self.reader, 4)
        self.assertEqual(candidate['display_code'], body)
        self.assertIn(GHIDRA_MARKER, candidate['code'])
        self.assertEqual(candidate['sha256'], hashlib.sha256(candidate['code'].encode()).hexdigest())
        self.assertEqual(candidate['producer'], 'ghidra')
        self.assertEqual(candidate_rvas(self.reader, optimized_only=True), set())
        save_candidate(self.reader, 4, GHIDRA_MARKER.replace('\n', '\r\n') + body.replace('\n', '\r\n'))
        self.assertEqual(read_candidate(self.reader, 4)['display_code'], body.replace('\n', '\r\n'))
        self.assertEqual(read_candidate(self.reader, 4)['producer'], 'ghidra')
        save_candidate(self.reader, 4, body)
        self.assertEqual(read_candidate(self.reader, 4)['producer'], 'llm')

    def test_external_id_survives_moves_and_body_edits(self):
        path = save_candidate(self.reader, 4, '// 解释\nvoid first(void) {}')
        text = path.read_text().replace('f00000004', 'custom-stable-id')
        moved = path.with_name('renamed.c')
        path.unlink()
        moved.write_text(text)
        candidate = read_candidate(self.reader, 4)
        self.assertIn('custom-stable-id', candidate['code'])
        save_candidate(self.reader, 4, '// 新解释\nvoid renamed(void) {}')
        self.assertIn('custom-stable-id', read_candidate(self.reader, 4)['code'])
        self.assertEqual(read_candidate(self.reader, 4)['path'], str(moved))
        with self.assertRaisesRegex(ValueError, '保留已有源码 ID'):
            save_candidate(self.reader, 4, candidate['code'].replace('custom-stable-id', 'other-id'))

    def test_legacy_module_and_single_file_convert_without_changing_body(self):
        from recovery.candidates import _identity, _legacy_header, _legacy_module_header, _read_text
        meta, _ = _identity(self.reader)
        body = '// 中文\r\nint test(void) { return 1; }\r\n'
        raw = _legacy_header(meta, 4) + '\n' + GHIDRA_MARKER + body
        directory = self.reader.directory / 'source/src'
        directory.mkdir(parents=True)
        path = directory / 'old.c'
        path.write_bytes((_legacy_module_header(meta) + raw + '\n/* LightRE end function 0x00000004 */\n').encode())
        before = read_candidate(self.reader, 4)
        self.assertEqual(before['display_code'], body)
        save_candidate(self.reader, 4, before['code'])
        self.assertEqual(read_candidate(self.reader, 4)['display_code'], body)
        self.assertEqual(read_candidate(self.reader, 4)['producer'], 'ghidra')
        self.assertNotIn('a' * 64, _read_text(path))
        self.assertNotIn('LightRE analysis candidate', _read_text(path))
        path.unlink()
        legacy = self.reader.directory / 'candidates/0x00000004.c'
        legacy.parent.mkdir()
        legacy.write_bytes(raw.encode())
        migrate_candidates(self.reader)
        self.assertEqual(read_candidate(self.reader, 4)['display_code'], body)
        self.assertFalse(legacy.exists())

    def test_duplicate_ids_across_modules_and_wrong_rva_are_rejected(self):
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute("INSERT INTO functions VALUES (8,'other',1,'[]')")
        path = save_candidate(self.reader, 4, 'void first(void) {}', module='one')
        other = save_candidate(self.reader, 8, 'void other(void) {}', module='two')
        with self.assertRaisesRegex(ValueError, '来源注释'):
            save_candidate(self.reader, 8, read_candidate(self.reader, 4)['code'])
        before = other.stat()
        other.write_text(other.read_text().replace('f00000008', 'f00000004'))
        os.utime(other, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, '重复源码 ID'):
            read_candidate(self.reader, 4)

    def test_explicit_metadata_migration_preserves_shared_code_and_is_idempotent(self):
        from recovery import migrate_source_markers
        from recovery.candidates import _identity, _legacy_header, _legacy_module_header
        meta, _ = _identity(self.reader)
        directory = self.reader.directory / 'source/src'
        directory.mkdir(parents=True)
        path = directory / 'old.c'
        shared = '// shared explanation\ntypedef unsigned int word;\n'
        body = GHIDRA_MARKER + '// original\nint test(void) { return 1; }\n'
        path.write_text(_legacy_module_header(meta) + shared + _legacy_header(meta, 4) + '\n' +
                        body + '\n/* LightRE end function 0x00000004 */\n')
        before = read_candidate(self.reader, 4)
        self.assertEqual(migrate_source_markers(self.reader), {'files': 1, 'functions': 1})
        after = read_candidate(self.reader, 4)
        self.assertEqual(before['display_code'], after['display_code'])
        self.assertEqual(before['producer'], after['producer'])
        self.assertNotEqual(before['sha256'], after['sha256'])
        self.assertIn(shared, path.read_text())
        snapshot = path.read_bytes()
        self.assertEqual(migrate_source_markers(self.reader), {'files': 0, 'functions': 0})
        self.assertEqual(snapshot, path.read_bytes())

    def test_metadata_migration_refuses_an_active_worker(self):
        from recovery import migrate_source_markers
        path = save_candidate(self.reader, 4, 'void test(void) {}')
        root = self.reader.directory / 'tasks'
        root.mkdir()
        before = path.read_bytes()
        with (root / '.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, '后台任务结束'):
                migrate_source_markers(self.reader)
        self.assertEqual(path.read_bytes(), before)

    def test_failed_replace_preserves_previous_file_and_cleans_temporary(self):
        path = save_candidate(self.reader, 4, "int test(void) { return 1; }\n")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                save_candidate(self.reader, 4, "int test(void) { return 2; }\n")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_suggestion_queues_without_approval_and_rejects_stale_content(self):
        save_candidate(self.reader, 4, "int test(void) { return 1; }")
        original = read_candidate(self.reader, 4)
        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/fake/codex'):
            task = start_task(self.reader, [4], '重命名为 read_count', original['sha256'])
            self.assertEqual(task['status'], 'queued')
            self.assertEqual(task['comment'], '重命名为 read_count')
            self.assertEqual((Path(task['directory']) / 'reviewed.c').read_text(), original['code'])
            save_candidate(self.reader, 4, "int test(void) { return 2; }")
            with self.assertRaisesRegex(ValueError, '候选已更新'):
                start_task(self.reader, [4], '检查返回值', original['sha256'])
        with self.reader._connect() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='reviews'").fetchone())

    def test_serial_lock_reuses_initial_task_and_queues_comment(self):
        root = self.reader.directory / "tasks"
        root.mkdir()
        state = {"status": "running", "id": "existing"}
        (root / "state.json").write_text(json.dumps(state))
        with (root / ".lock").open("w") as lock, \
             patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/fake/codex'):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(start_task(self.reader)["id"], "existing")
            queued = start_task(self.reader, [4], "改进")
            self.assertEqual(queued['status'], 'queued')
            self.assertEqual(queued['priority'], 0)
            self.assertEqual(queued['rvas'], [4])
        self.assertEqual(task_state(self.reader)["status"], "interrupted")

    def test_runner_publishes_structured_output_and_preserves_unknowns(self):
        class FakeCodex:
            returncode = 0
            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index("-o") + 1])
                process.stdin = io.BytesIO()
            def poll(process):
                process.output.write_text(json.dumps({"results": [result]}))
                return 0

        root = self.reader.directory / "tasks"
        root.mkdir()
        directory = root / "test"
        directory.mkdir()
        (directory / "task.md").write_text("还原函数")
        state = {"status": "queued", "directory": str(directory), "rvas": [4],
                 "codex": "fake-codex", "processed": 0, "generated": 0, "unresolved": {}}
        state_path = root / "state.json"
        result = {"rva": "0x4", "code": None, "notes": "未知：间接调用目标，需要动态实验"}
        with patch("recovery.jobs.subprocess.Popen", FakeCodex), patch("recovery.jobs.signal.signal"):
            state_path.write_text(json.dumps(state))
            run_task(self.reader)
            self.assertEqual(json.loads(state_path.read_text())["status"], "partial")
            self.assertIsNone(read_candidate(self.reader, 4)["code"])
            self.assertIn("间接调用", read_knowledge(self.reader, 4))
            result = {"rva": "0x4", "code": "int test(void) { return 1; }", "notes": "事实：返回常量"}
            state_path.write_text(json.dumps(state))
            run_task(self.reader)
            self.assertEqual(json.loads(state_path.read_text())["status"], "complete")
            self.assertIn("return 1", read_candidate(self.reader, 4)["code"])

    def parallel_task(self):
        rvas = [4, 8, 12, 16, 20, 24]
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute("UPDATE functions SET instructions=4000")
            conn.executemany("INSERT INTO functions VALUES (?, 'test', 4000, '[]')", [(rva,) for rva in rvas[1:]])
        root = self.reader.directory / "tasks"
        root.mkdir()
        directory = root / "parallel"
        directory.mkdir()
        (directory / "task.md").write_text("并行还原")
        state_path = root / "state.json"
        state_path.write_text(json.dumps({"id": "parallel", "created_at": 0, "status": "queued", "directory": str(directory),
            "rvas": rvas, "codex": "fake-codex", "workers": 3,
            "processed": 0, "generated": 0, "unresolved": {}}))
        return state_path

    def test_parallel_batches_are_bounded_and_publish_without_waiting_for_first(self):
        state_path = self.parallel_task()
        live, maximum, order = [], [], []

        class FakeCodex:
            returncode = None
            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index("-o") + 1])
                schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
                process.rvas = schema["properties"]["results"]["items"]["properties"]["rva"]["enum"]
                process.remaining = 3 if process.rvas == ["0x4"] else 1
                process.stdin = io.BytesIO()
                live.append(process)
                maximum.append(len(live))
            def poll(process):
                if process.returncode is None:
                    process.remaining -= 1
                    if process.remaining <= 0:
                        process.returncode = 0
                        live.remove(process)
                        order.extend(process.rvas)
                        process.output.write_text(json.dumps({"results": [
                            {"rva": rva, "code": f"int fcn_{int(rva, 0):08x}(void) {{ return 1; }}", "notes": "事实"}
                            for rva in process.rvas]}))
                return process.returncode

        with patch("recovery.jobs.subprocess.Popen", FakeCodex), patch("recovery.jobs.signal.signal"), patch("recovery.jobs.time.sleep"):
            run_task(self.reader)
        state = json.loads(state_path.read_text())
        self.assertEqual(max(maximum), 3)
        self.assertNotEqual(order[0], "0x4")
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["processed"], 6)
        self.assertEqual(state["generated"], 6)
        self.assertEqual(len(list((self.reader.directory / "source/src").glob("*.c"))), 1)
        self.assertEqual(len(candidate_rvas(self.reader)), 6)
        self.assertEqual(state["active_batches"], 0)

    def test_interrupt_cleans_up_all_parallel_batches(self):
        state_path = self.parallel_task()
        started, stopped = [], []

        class FakeCodex:
            def __init__(process, command, **kwargs):
                process.stdin = io.BytesIO()
                started.append(process)
            def poll(process):
                raise InterruptedError("停止测试")

        with patch("recovery.jobs.subprocess.Popen", FakeCodex), patch("recovery.jobs.signal.signal"), patch("recovery.jobs._terminate", side_effect=stopped.append):
            run_task(self.reader)
        self.assertEqual(len(started), 3)
        self.assertEqual(stopped, started)
        state = json.loads(state_path.read_text())
        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(state["active_batches"], 0)
        self.assertEqual(state["current"], [])

    def test_rate_limit_retries_are_bounded_and_other_batches_publish(self):
        state_path = self.parallel_task()
        calls = []

        class BrokenInput:
            def write(self, value):
                raise BrokenPipeError('CLI 已退出')

        class FakeCodex:
            returncode = None
            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])
                schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
                process.rva = schema['properties']['results']['items']['properties']['rva']['enum'][0]
                process.stdin = io.BytesIO()
                calls.append(process.rva)
                if process.rva == '0x4':
                    kwargs['stderr'].write(b'429 Too Many Requests')
                    if calls.count('0x4') == 2:
                        process.stdin = BrokenInput()
            def poll(process):
                if process.rva == '0x4':
                    process.returncode = 1
                else:
                    process.returncode = 0
                    process.output.write_text(json.dumps({'results': [
                        {'rva': process.rva, 'code': 'void recovered(void) {}', 'notes': '事实'}]}))
                return process.returncode

        with patch('recovery.jobs.subprocess.Popen', FakeCodex), patch('recovery.jobs.signal.signal'), \
             patch('recovery.jobs.RETRY_BACKOFF', 0):
            run_task(self.reader)
        state = json.loads(state_path.read_text())
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(state['processed'], 6)
        self.assertEqual(state['generated'], 5)
        self.assertEqual(calls.count('0x4'), 2)
        self.assertEqual(len(calls), 7)
        self.assertEqual(state['effective_workers'], 1)
        self.assertEqual(state['failed_count'], 1)
        self.assertIn('非模型分析结论', read_knowledge(self.reader, 4))
        self.assertEqual(progress(self.reader)['function_statuses'][4], 'failed')
        self.assertEqual(progress(self.reader)['failed'], 1)
        save_candidate(self.reader, 4, 'void recovered(void) {}')
        self.assertEqual(progress(self.reader)['failed'], 0)

    def test_partial_json_preserves_valid_results_and_retries_only_missing(self):
        state_path = self.parallel_task()
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute('UPDATE functions SET instructions=0')
        calls = []

        class FakeCodex:
            returncode = None
            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])
                schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
                process.rvas = schema['properties']['results']['items']['properties']['rva']['enum']
                process.first = not calls
                calls.append(process.rvas)
                process.stdin = io.BytesIO()
                if process.first:
                    process.output.with_name(process.output.name.replace('.result.json', '.input.json')).write_text(
                        json.dumps({rva: 'void initial(void) {}' for rva in process.rvas}))
            def poll(process):
                results = [{'rva': rva, 'code': 'void recovered(void) {}', 'notes': '事实'} for rva in process.rvas]
                if process.first:
                    results = [results[0], {'rva': process.rvas[1], 'notes': '缺少 code，不应当作未知项'},
                               {'rva': process.rvas[2], 'code': 'void invalid(void) {}', 'notes': '```\n未闭合'},
                               {'rva': process.rvas[3], 'code': None, 'notes': '无效补丁',
                                'edits': [{'old': 'does not exist', 'new': 'wrong'}]}]
                process.output.write_text(json.dumps({'results': results}))
                process.returncode = 0
                return 0

        with patch('recovery.jobs.subprocess.Popen', FakeCodex), patch('recovery.jobs.signal.signal'), \
             patch('recovery.jobs.RETRY_BACKOFF', 0):
            run_task(self.reader)
        state = json.loads(state_path.read_text())
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(state['generated'], 6)
        self.assertEqual(sum('0x4' in batch for batch in calls), 1)
        self.assertEqual(sum(map(len, calls[1:])), 5)

    def test_timeout_isolated_and_exhausted_function_recorded(self):
        state_path = self.parallel_task()
        terminated = []

        class FakeCodex:
            returncode = None
            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])
                schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
                process.rva = schema['properties']['results']['items']['properties']['rva']['enum'][0]
                process.stdin = io.BytesIO()
            def poll(process):
                if process.rva != '0x4':
                    process.output.write_text(json.dumps({'results': [
                        {'rva': process.rva, 'code': 'void recovered(void) {}', 'notes': '事实'}]}))
                    process.returncode = 0
                return process.returncode
        def terminate(process):
            terminated.append(process.rva)
            process.returncode = -15

        with patch('recovery.jobs.subprocess.Popen', FakeCodex), patch('recovery.jobs.signal.signal'), \
             patch('recovery.jobs.FEEDBACK_BATCH_TIMEOUT', 0), patch('recovery.jobs.RETRY_BACKOFF', 0), \
             patch('recovery.jobs._terminate', side_effect=terminate):
            run_task(self.reader)
        state = json.loads(state_path.read_text())
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(state['generated'], 5)
        self.assertEqual(terminated, ['0x4', '0x4'])
        self.assertIn('超时', state['failed_rvas']['0x4'])

    def test_large_prompt_is_passed_as_file_without_dispatcher_pipe_writes(self):
        from recovery.jobs import _start_batch
        save_candidate(self.reader, 4, 'int test(void) { return 1; }\n/*' + 'context ' * 20000 + '*/')
        task_dir = self.reader.directory / 'test-task'
        task_dir.mkdir()
        received = []

        class FakeCodex:
            def __init__(process, command, **kwargs):
                received.append(kwargs['stdin'].read())
                self.assertIn('--ephemeral', command)
                # File-backed input means Popen has no parent-side stdin pipe.
                process.stdin = None

        with patch('recovery.jobs.subprocess.Popen', FakeCodex):
            running = _start_batch(self.reader, {'directory': str(task_dir), 'codex': 'fake',
                                   'mode': 'initial'}, 0, [{'rva': 4, 'instructions': 1}])
        self.assertGreater(len(received[0]), 65536)
        self.assertEqual(received[0], (task_dir / 'batch-0000.prompt.md').read_bytes())
        self.assertIn('started_clock', running)

    def test_wall_clock_jump_does_not_timeout_healthy_batches(self):
        state_path = self.parallel_task()

        class FakeCodex:
            returncode = None
            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])
                schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
                process.rva = schema['properties']['results']['items']['properties']['rva']['enum'][0]
                process.polls = 0
            def poll(process):
                process.polls += 1
                if process.polls >= 2:
                    process.output.write_text(json.dumps({'results': [
                        {'rva': process.rva, 'code': 'void recovered(void) {}', 'notes': '事实'}]}))
                    process.returncode = 0
                return process.returncode

        with patch('recovery.jobs.subprocess.Popen', FakeCodex), patch('recovery.jobs.signal.signal'), \
             patch('recovery.jobs.time.time', side_effect=iter(range(0, 1000000, 1000))), \
             patch('recovery.jobs.time.monotonic', return_value=100), patch('recovery.jobs.time.sleep'), \
             patch('recovery.jobs._terminate', side_effect=AssertionError('wall clock is not a deadline')):
            run_task(self.reader)
        state = json.loads(state_path.read_text())
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(state['generated'], 6)
        self.assertEqual(state['run_duration_seconds'], 0)

    def test_resume_publishes_saved_output_without_repeating_cli(self):
        state_path = self.parallel_task()
        state = json.loads(state_path.read_text())
        state['rvas'] = [4]
        state['batches'] = {'0': {'status': 'running', 'rvas': [4], 'before': {'4': None}}}
        state_path.write_text(json.dumps(state))
        prefix = Path(state['directory']) / 'batch-0000.result.json'
        prefix.with_name('batch-0000.input.json').write_text(json.dumps({'0x4': 'void recovered(void) {}'}))
        prefix.write_text(json.dumps({'results': [{'rva': '0x4', 'code': None,
            'edits': [{'old': '{}', 'new': '{ return; }'}], 'notes': '事实'}]}))
        # Simulate a crash after source publication but before its checkpoint.
        save_candidate(self.reader, 4, 'void recovered(void) { return; }')
        with patch('recovery.jobs.subprocess.Popen', side_effect=AssertionError('already have output')), \
             patch('recovery.jobs.save_candidate', side_effect=AssertionError('already published')), \
             patch('recovery.jobs.signal.signal'):
            run_task(self.reader)
        state = json.loads(state_path.read_text())
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(state['generated'], 1)
        self.assertTrue(state['batches']['0']['recovered'])

    def test_many_functions_share_modules_and_keep_independent_content_hashes(self):
        with sqlite3.connect(self.reader.database) as conn:
            conn.executemany("INSERT INTO functions VALUES (?,'test',1,'[]')", [(rva,) for rva in range(8, 88, 4)])
        for rva in range(4, 88, 4):
            save_candidate(self.reader, rva, f"int fcn_{rva:08x}(void) {{ return 1; }}", module="runtime_002")
        paths = list((self.reader.directory / "source/src").glob("*.c"))
        self.assertEqual(len(paths), 2)
        self.assertEqual({path.name for path in paths}, {"runtime.c", "runtime_002.c"})
        original = read_candidate(self.reader, 4)
        sibling = read_candidate(self.reader, 8)
        self.assertEqual(original['path'], sibling['path'])
        save_candidate(self.reader, 8, "int test(void) { return 2; }")
        self.assertEqual(read_candidate(self.reader, 4)['sha256'], original['sha256'])
        self.assertEqual(len(candidate_rvas(self.reader)), 21)

    def test_source_index_rebuilds_after_manual_move_delete_and_rejects_duplicates(self):
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute("INSERT INTO functions VALUES (8,'other',1,'[]')")
        path = save_candidate(self.reader, 4, "void first(void) {}")
        save_candidate(self.reader, 8, "void other(void) {}")
        renamed = path.with_name('runtime.c')
        path.rename(renamed)
        self.assertEqual(read_candidate(self.reader, 4)['path'], str(renamed))
        other = read_candidate(self.reader, 8)['code']
        renamed.write_text(renamed.read_text().replace(other + '\n// @lightre.end {"id":"f00000008"}', ''))
        self.assertIsNone(read_candidate(self.reader, 8)['code'])
        with self.reader._connect() as conn:
            self.assertEqual([row[0] for row in conn.execute('SELECT function_rva FROM source_index')], [4])
        duplicate = renamed.with_name('duplicate.c')
        duplicate.write_text(renamed.read_text())
        with self.assertRaisesRegex(ValueError, '重复'):
            read_candidate(self.reader, 4)

    def test_legacy_migration_preserves_code_without_a_second_copy(self):
        path = save_candidate(self.reader, 4, "int test(void) { return 1; }")
        original = read_candidate(self.reader, 4)
        legacy = self.reader.directory / 'candidates/0x00000004.c'
        legacy.parent.mkdir()
        legacy.write_text(original['code'])
        path.unlink()
        migrate_candidates(self.reader)
        candidate = read_candidate(self.reader, 4)
        self.assertEqual(candidate['code'], original['code'])
        self.assertEqual(candidate['sha256'], original['sha256'])
        self.assertFalse(legacy.parent.exists())
        self.assertIn('source/src', candidate['path'])

    def test_worker_reads_saved_ghidra_references_without_starting_tools(self):
        from analyzers.ghidra import prepare_references, read_references
        root = self.reader.directory / 'references/ghidra'
        root.mkdir(parents=True)
        (root / 'state.json').write_text(json.dumps({'status': 'ready', 'available': 1}))
        path = root / 'functions.json'
        path.write_text(json.dumps({'sha256': 'a' * 64, 'image_base': 0x1000,
            'functions': [{'rva': 4, 'status': 'ok', 'code': 'return 1;'}]}))
        with patch('subprocess.Popen', side_effect=AssertionError('must not start Ghidra')):
            self.assertEqual(prepare_references(self.reader)['available'], 1)
            self.assertEqual(read_references(self.reader, [4])[0]['image_base'], 0x1000)
            self.assertEqual(read_references(self.reader, [4])[0]['code'], 'return 1;')
        path.write_text(path.read_text().replace('a' * 64, 'b' * 64))
        with self.assertRaisesRegex(ValueError, '其他 SO'):
            read_references(self.reader, [4])

    def test_merge_packs_small_files_preserving_hashes_and_index(self):
        rvas = list(range(4, 88, 4))
        with sqlite3.connect(self.reader.database) as conn:
            conn.executemany("INSERT INTO functions VALUES (?,'test',1,'[]')", [(rva,) for rva in rvas[1:]])
        for rva in rvas:
            save_candidate(self.reader, rva, f'int fcn_{rva:08x}(void) {{ return 1; }}', module=f'part_{rva:x}')
        before = {rva: read_candidate(self.reader, rva)['sha256'] for rva in rvas}
        result = merge_sources(self.reader, {rva: 'runtime' for rva in rvas})
        self.assertEqual(result, {'functions': 21, 'files_before': 21, 'files_after': 2})
        self.assertEqual({path.name for path in (self.reader.directory / 'source/src').glob('*.c')},
                         {'runtime.c', 'runtime_002.c'})
        self.assertEqual({rva: read_candidate(self.reader, rva)['sha256'] for rva in rvas}, before)
        with self.reader._connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM source_index').fetchone()[0], 21)
            self.assertEqual(conn.execute('SELECT count(DISTINCT path) FROM source_index').fetchone()[0], 2)

    def test_merge_keeps_shared_declarations_and_recovers_directory_swap(self):
        path = save_candidate(self.reader, 4, 'void test(void) {}', module='manual')
        path.write_text(path.read_text() + '\ntypedef int shared_type;\n')
        header = path.with_suffix('.h')
        header.write_text('/* shared declarations */')
        original = path.read_text()
        merge_sources(self.reader, {4: 'runtime'})
        self.assertEqual(path.read_text(), original)
        self.assertTrue(header.exists())
        directory = path.parent
        directory.rename(directory.parent / '.previous')
        self.assertIn('void test', read_candidate(self.reader, 4)['code'])
        self.assertEqual(path.read_text(), original)
        self.assertFalse((directory.parent / '.previous').exists())

    def test_ghidra_seed_preserves_llm_and_remains_pending_for_automatic_task(self):
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute("INSERT INTO functions VALUES (8,'other',1,'[]')")
        save_candidate(self.reader, 8, 'void optimized(void) {}')
        original = read_candidate(self.reader, 8)
        references = [{'rva': rva, 'module': '.text', 'status': 'ok',
                       'code': 'void initial(void) {}'} for rva in (4, 8)]
        self.assertEqual(prepare_source(self.reader, references)['seeded'], 1)
        self.assertEqual(read_candidate(self.reader, 4)['status'], 'reference')
        self.assertEqual(read_candidate(self.reader, 4)['producer'], 'ghidra')
        self.assertEqual(read_candidate(self.reader, 8)['sha256'], original['sha256'])
        self.assertEqual(candidate_rvas(self.reader, optimized_only=True), {8})
        self.assertEqual(progress(self.reader)['initial'], 1)
        with patch('recovery.queue.shutil.which', return_value='/fake/codex'), patch('recovery.queue.subprocess.Popen'):
            task = start_task(self.reader)
        self.assertEqual(task['rvas'], [4])
        self.assertEqual(prepare_source(self.reader, references)['seeded'], 0)

    def test_automatic_pipeline_seeds_then_runs_llm_and_preserves_unknown_initial_code(self):
        state_path = self.parallel_task()
        state = json.loads(state_path.read_text())
        state.update(mode='initial', rvas=[4, 8])
        state_path.write_text(json.dumps(state))
        references = [{'rva': rva, 'module': '.text', 'status': 'ok',
                       'code': f'void fcn_{rva:08x}(void) {{ /* initial */ }}'} for rva in (4, 8)]
        reader = self.reader
        checked = []

        class FakeCodex:
            returncode = None
            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])
                schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
                props = schema['properties']['results']['items']['properties']
                self.assertIn('model_reasoning_effort="low"', command)
                self.assertEqual([command[i + 1] for i, arg in enumerate(command) if arg == '--disable'],
                                 ['shell_tool', 'plugins', 'apps', 'multi_agent'])
                self.assertNotIn('module', props)
                process.rvas = props['rva']['enum']
                process.stdin = io.BytesIO()
                inputs = json.loads(process.output.with_name(process.output.name.replace('.result.json', '.input.json')).read_text())
                self.assertNotIn('LightRE analysis candidate', inputs[process.rvas[0]])
                checked.extend(read_candidate(reader, int(rva, 0))['producer'] for rva in process.rvas)
            def poll(process):
                if process.returncode is None:
                    process.returncode = 0
                    process.output.write_text(json.dumps({'results': [
                        {'rva': rva, 'code': None,
                         'edits': [{'old': '/* initial */', 'new': '/* conservative improvement */'}] if rva == '0x4' else None,
                         'notes': '未知：需要动态运行'} for rva in process.rvas]}))
                return process.returncode

        with patch('recovery.jobs.prepare_references', return_value={'status': 'ready'}), \
             patch('recovery.jobs.read_references', return_value=references), \
             patch('recovery.jobs.subprocess.Popen', FakeCodex), patch('recovery.jobs.signal.signal'):
            run_task(reader)
        state = json.loads(state_path.read_text())
        self.assertEqual(checked, ['ghidra', 'ghidra'])
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(state['generated'], 1)
        self.assertEqual(state['source']['seeded'], 2)
        self.assertEqual(read_candidate(reader, 4)['producer'], 'llm')
        self.assertIn('conservative improvement', read_candidate(reader, 4)['code'])
        self.assertEqual(read_candidate(reader, 8)['producer'], 'ghidra')
        self.assertEqual(Path(read_candidate(reader, 4)['path']).name, 'text.c')
        self.assertIn('需要动态', state['unresolved']['0x8'])

    def test_batches_do_not_mix_modules(self):
        functions = [{'rva': 4, 'instructions': 10, 'module': 'imports'},
                     {'rva': 8, 'instructions': 10, 'module': 'text'},
                     {'rva': 12, 'instructions': 10, 'module': 'text'}]
        self.assertEqual([len(batch) for batch in _batches(functions)], [1, 2])

    def test_batches_pack_short_functions_and_preserve_large_functions(self):
        def functions(sizes):
            return [{'rva': index * 4, 'instructions': size, 'module': 'text'}
                    for index, size in enumerate(sizes)]

        batches = list(_batches(functions([100] * 40)))
        self.assertEqual([len(batch) for batch in batches], [32, 8])
        batches = list(_batches(functions([800] * 10)))
        self.assertEqual([sum(fn['instructions'] for fn in batch) for batch in batches], [4000, 4000])
        original = functions([1000, 4000, 5000, 10])
        batches = list(_batches(original))
        self.assertEqual([len(batch) for batch in batches], [1, 1, 1, 1])
        self.assertEqual([fn for batch in batches for fn in batch], original)
        original = functions([10, 10, 10])
        for fn in original:
            fn['context_chars'] = 40000
        self.assertEqual([len(batch) for batch in _batches(original)], [1, 1, 1])

    def test_progress_distinguishes_llm_initial_unresolved_and_pending_after_new_tasks(self):
        with sqlite3.connect(self.reader.database) as conn:
            conn.executemany("INSERT INTO functions VALUES (?,'test',1,'[]')", [(8,), (12,), (16,)])
        save_candidate(self.reader, 4, 'void recovered(void) {}')
        save_candidate(self.reader, 8, GHIDRA_MARKER + 'void initial(void) {}', module='text')
        notes = self.reader.directory / 'knowledge'
        notes.mkdir()
        (notes / '0x00000004.md').write_text('已有候选，未经验证')
        (notes / '0x0000000c.md').write_text('未知：需要动态验证')
        with patch('recovery.jobs.task_state', return_value={'status': 'idle'}), \
             patch('recovery.jobs.subprocess.Popen', side_effect=AssertionError('must not run tools')):
            result = progress(self.reader)
        self.assertEqual(result['function_statuses'], {4: 'llm', 8: 'ghidra', 12: 'unresolved', 16: 'pending'})
        self.assertEqual(result['candidates'], 1)
        self.assertEqual(result['initial'], 1)
        save_candidate(self.reader, 12, 'void newly_recovered(void) {}')
        with patch('recovery.jobs.task_state', return_value={'status': 'complete'}):
            self.assertEqual(progress(self.reader)['function_statuses'][12], 'llm')

    def test_queue_deduplicates_comments_and_keeps_fifo_order_and_submission_snapshot(self):
        from recovery.queue import snapshot, submit
        save_candidate(self.reader, 4, 'int old(void) { return 1; }')
        original = read_candidate(self.reader, 4)
        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/fake/codex'):
            first = submit(self.reader, [4], '核对副作用', original['sha256'])
            duplicate = submit(self.reader, [4], '核对副作用', original['sha256'])
            second = submit(self.reader, [4], '再核对边界', original['sha256'])
        self.assertEqual(first['id'], duplicate['id'])
        self.assertEqual([job['id'] for job in snapshot(self.reader)], [first['id'], second['id']])
        directory = Path(first['directory'])
        self.assertEqual((directory / 'reviewed.c').read_text(), original['code'])
        self.assertIn('核对副作用', (directory / 'task.md').read_text())
        save_candidate(self.reader, 4, 'int latest(void) { return 2; }')
        self.assertEqual((directory / 'reviewed.c').read_text(), original['code'])
        with patch('recovery.queue._ensure_worker'):
            self.assertEqual(submit(self.reader, [4], '核对副作用', original['sha256'])['id'], first['id'])
        seen = []
        reader = self.reader
        class FakeCodex:
            returncode = None
            def __init__(process, command, **kwargs):
                seen.append(read_candidate(reader, 4)['code'])
                process.output = Path(command[command.index('-o') + 1])
                process.stdin = io.BytesIO()
            def poll(process):
                process.returncode = 0
                process.output.write_text(json.dumps({'results': [
                    {'rva': '0x4', 'code': 'int latest(void) { return 3; }', 'notes': 'latest 返回常量 3。',
                     'edits': None, 'knowledge_edits': None, 'reply': '结合最新代码继续'}]}))
                return 0
        with patch('recovery.jobs.subprocess.Popen', FakeCodex), patch('recovery.jobs.signal.signal'):
            run_task(reader, first['id'])
        self.assertIn('return 2', seen[0])
        self.assertIn('return 3', read_candidate(reader, 4)['code'])

    def test_queue_pause_resume_and_cancel_survive_reopening(self):
        from recovery.queue import cancel, pause, resume, snapshot, submit
        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/fake/codex'):
            job = submit(self.reader, [4], '核对入口', None)
            pause(self.reader)
            self.assertTrue(progress(WorkspaceReader(self.reader.directory))['paused'])
            resume(self.reader)
            self.assertFalse(progress(self.reader)['paused'])
            cancel(self.reader, job['id'])
            self.assertEqual(snapshot(WorkspaceReader(self.reader.directory))[0]['status'], 'cancelled')
            self.assertEqual(progress(self.reader)['queued'], 0)

    def test_spawn_failure_keeps_durable_request_for_resume(self):
        from recovery.queue import snapshot, submit
        with patch('recovery.queue.shutil.which', return_value='/fake/codex'), \
             patch('recovery.queue.subprocess.Popen', side_effect=OSError('spawn failed')):
            with self.assertRaisesRegex(OSError, 'spawn failed'):
                submit(self.reader, [4])
        jobs = snapshot(WorkspaceReader(self.reader.directory))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['status'], 'queued')

    def test_feedback_preempts_between_batches_and_initial_pass_resumes_without_duplicates(self):
        from recovery.queue import snapshot, submit
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute('UPDATE functions SET instructions=4000')
            conn.executemany("INSERT INTO functions VALUES (?,'test',4000,'[]')", [(8,), (12,), (16,)])
            for rva in (4, 8, 12, 16):
                conn.executemany('INSERT INTO assembly VALUES (?,?,?,?,?)', [
                    (rva, n, rva + n * 4, '00040091', 'add x0, x0, #1') for n in range(4000)])
        launched, feedback = [], []
        reader = self.reader
        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/fake/codex'):
            initial = submit(reader, workers=2)

            class FakeCodex:
                returncode = None
                def __init__(process, command, **kwargs):
                    process.output = Path(command[command.index('-o') + 1])
                    schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
                    process.rvas = schema['properties']['results']['items']['properties']['rva']['enum']
                    process.stdin = io.BytesIO()
                    launched.extend(process.rvas)
                def poll(process):
                    if process.returncode is None:
                        if not feedback:
                            feedback.append(submit(reader, [16], '优先核对', None))
                        process.returncode = 0
                        process.output.write_text(json.dumps({'results': [
                            {'rva': rva, 'code': f'int fcn_{int(rva, 0):08x}(void) {{ return 1; }}', 'notes': '事实',
                             'edits': None, 'knowledge_edits': None, 'reply': '保留实际行为。'}
                            for rva in process.rvas]}))
                    return process.returncode

            with patch('recovery.jobs.subprocess.Popen', FakeCodex), patch('recovery.jobs.signal.signal'):
                run_task(reader, initial['id'])
                state = json.loads((Path(initial['directory']) / 'state.json').read_text())
                self.assertEqual(state['status'], 'queued')
                self.assertEqual(state['processed'], 2)
                self.assertEqual(launched, ['0x4', '0x8'])
                run_task(reader, feedback[0]['id'])
                run_task(reader, initial['id'])
        self.assertEqual(launched, ['0x4', '0x8', '0x10', '0xc'])
        states = {job['id']: job for job in snapshot(reader)}
        self.assertEqual(states[initial['id']]['status'], 'complete')
        self.assertEqual(states[initial['id']]['processed'], 4)
        self.assertEqual(sorted(states[initial['id']]['done_rvas']), [4, 8, 12, 16])
        self.assertEqual(states[feedback[0]['id']]['status'], 'complete')

    def test_queue_dispatches_feedback_fifo_then_bulk_and_releases_idle_lock(self):
        from recovery.queue import run_queue, snapshot, submit, write_state
        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/fake/codex'):
            bulk = submit(self.reader)
            first = submit(self.reader, [4], '第一条', None)
            second = submit(self.reader, [4], '第二条', None)
        order = []
        class FakeCodex:
            returncode = None

            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])
                order.append(process.output.parent.name)

            def poll(process):
                process.returncode = 0
                process.output.write_text(json.dumps({'results': [{
                    'rva': '0x4', 'code': None, 'notes': '保留未知项',
                    'edits': None, 'knowledge_edits': None, 'reply': '仍需核对未知项。'}]}))
                return 0
        lock_path = self.reader.directory / 'tasks/.dispatcher.lock'
        with lock_path.open('a+') as dispatcher, patch('recovery.jobs.subprocess.Popen', FakeCodex), \
                patch('recovery.jobs.signal.signal'), patch('recovery.jobs.time.sleep'):
            fcntl.flock(dispatcher, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run_queue(self.reader, dispatcher.fileno())
            with lock_path.open('a+') as another:
                fcntl.flock(another, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(order, [first['id'], second['id'], bulk['id']])

    def test_cancelling_running_task_cleans_its_batch_and_continues_queue(self):
        from recovery.queue import cancel, run_queue, snapshot, submit
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute("INSERT INTO functions VALUES (8,'other',1,'[]')")
        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/fake/codex'):
            first = submit(self.reader, [4])
            second = submit(self.reader, [8])
        stopped = []
        reader = self.reader
        class FakeCodex:
            returncode = None
            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])
                process.stdin = io.BytesIO()
            def poll(process):
                if process.output.parent.name == first['id']:
                    cancel(reader, first['id'])
                    return None
                process.returncode = 0
                process.output.write_text(json.dumps({'results': [
                    {'rva': '0x8', 'code': 'void recovered(void) {}', 'notes': '事实'}]}))
                return 0
        lock_path = reader.directory / 'tasks/.dispatcher.lock'
        with lock_path.open('a+') as dispatcher, patch('recovery.jobs.subprocess.Popen', FakeCodex), \
             patch('recovery.jobs.signal.signal'), patch('recovery.jobs.time.sleep'), \
             patch('recovery.jobs._terminate', side_effect=lambda process: stopped.append(process)):
            fcntl.flock(dispatcher, fcntl.LOCK_EX)
            run_queue(reader, dispatcher.fileno())
        states = {job['id']: job for job in snapshot(reader)}
        self.assertEqual(states[first['id']]['status'], 'cancelled')
        self.assertEqual(states[second['id']]['status'], 'complete')
        self.assertEqual(len(stopped), 1)
        self.assertIsNone(read_candidate(reader, 4)['code'])
        self.assertEqual(read_candidate(reader, 8)['producer'], 'llm')


if __name__ == "__main__":
    unittest.main()
