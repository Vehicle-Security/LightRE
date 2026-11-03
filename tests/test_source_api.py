"""Fast API checks with real temporary storage; no browser, server or model."""

import json
import fcntl
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from recovery import save_candidate, save_knowledge
from recovery.jobs import run_task
from recovery.queue import run_queue, submit, write_state
from webui.app import create_app
from lightre.workspace import RESULT_SCHEMA, WorkspaceReader


class SourceApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # Any accidental worker, Codex, formatter or analyzer invocation fails.
        self.enterContext(patch('subprocess.Popen', side_effect=AssertionError('unexpected tool')))
        self.reader = self.make_reader('one', 'a' * 64)
        self.client = self.open_client(self.reader)

    def make_reader(self, name, digest):
        directory = self.root / name
        directory.mkdir()
        with sqlite3.connect(directory / 'index.sqlite') as conn:
            conn.executescript('''
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE functions (rva INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    instructions INTEGER NOT NULL, ranges TEXT NOT NULL);
                CREATE TABLE assembly (function_rva INTEGER REFERENCES functions(rva),
                    ordinal INTEGER, rva INTEGER, bytes TEXT, opcode TEXT,
                    PRIMARY KEY(function_rva, ordinal));
                INSERT INTO functions VALUES (4,'same_native_name',1,'[[4,8]]');
                INSERT INTO functions VALUES (8,'same_native_name',1,'[[8,12]]');
                INSERT INTO assembly VALUES (4,0,4,'c0035fd6','ret');
                INSERT INTO assembly VALUES (8,0,8,'00040091','add x0, x0, #1');
            ''')
            conn.executemany('INSERT INTO metadata VALUES (?,?)', {
                'name': name, 'sha256': digest, 'source_path': '/inputs/example.so',
                'state': 'ready', 'schema_version': RESULT_SCHEMA,
            }.items())
        return WorkspaceReader(directory)

    def open_client(self, reader):
        # Empty fixture queues need no worker; real routes/readers are used.
        return self.enterContext(TestClient(create_app(reader)))

    def source(self, rva, client=None):
        response = (client or self.client).get(f'/api/functions/{rva:#x}/candidate')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        result = response.json()
        self.assertEqual(result['rva'], rva)
        return result

    def identity(self, candidate):
        opening = candidate['code'].splitlines()[0]
        return json.loads(opening.removeprefix('// @lightre.begin '))

    def native_rows(self):
        with sqlite3.connect(self.reader.database) as conn:
            return (conn.execute('SELECT * FROM functions ORDER BY rva').fetchall(),
                    conn.execute('SELECT * FROM assembly ORDER BY function_rva,ordinal').fetchall())

    def test_web_continue_delivers_inline_assembly_and_publishes_to_source_routes(self):
        with patch('recovery.queue._ensure_worker'), \
             patch('recovery.queue.shutil.which', return_value='/unused/codex'):
            response = self.client.post('/api/recovery')
        self.assertEqual(response.status_code, 200, response.text)
        job = response.json()
        self.assertEqual(job['input_mode'], 'reference')
        self.assertEqual(job['mode'], 'initial')
        self.assertEqual(job['rvas'], [4, 8])
        received = []

        class FakeCodex:
            returncode = 0

            def __init__(process, command, **kwargs):
                received.append(kwargs['stdin'].read().decode('utf-8'))
                process.output = Path(command[command.index('-o') + 1])

            def poll(process):
                process.output.write_text(json.dumps({'results': [
                    {'rva': hex(rva), 'code': f'long fcn_{rva:08x}(long value) {{ return value + {rva == 8:d}; }}',
                     'notes': '从完整汇编生成的测试候选，未经执行验证。'} for rva in (4, 8)]}))
                return 0

        before = self.native_rows()
        with patch('recovery.jobs.prepare_references', return_value={'status': 'unavailable'}), \
             patch('recovery.jobs.subprocess.Popen', FakeCodex), \
             patch('recovery.jobs.format_code', side_effect=lambda reader, code: code), \
             patch('recovery.jobs.signal.signal'):
            run_task(self.reader, job['id'])
        self.assertEqual(len(received), 1)
        self.assertIn('```asm\n4 ret\n```', received[0])
        self.assertIn('```asm\n8 add x0,x0,#1\n```', received[0])
        result = self.client.get('/api/recovery').json()
        self.assertEqual(result['task']['status'], 'complete')
        self.assertEqual(result['task']['generated'], 2)
        batch = result['task']['batches']['0']
        self.assertEqual(batch['rvas'], [4, 8])
        self.assertEqual(batch['input_mode'], 'assembly')
        self.assertEqual(batch['timeout'], 600)
        for rva in (4, 8):
            candidate = self.source(rva)
            self.assertEqual(candidate['producer'], 'llm')
            self.assertIn(f'fcn_{rva:08x}', candidate['display_code'])
            self.assertIn('未经执行验证', candidate['notes'])
        self.assertEqual(self.native_rows(), before)

    def submit_suggestion(self, comment='请修改返回值', rva=4):
        with patch('recovery.queue._ensure_worker'), \
                patch('recovery.queue.shutil.which', return_value='/unused/codex'):
            response = self.client.post(f'/api/functions/{rva:#x}/feedback', json={
                'comment': comment, 'expected_sha256': self.source(rva)['sha256']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def finish_suggestion(self, job, code, reply, concurrent_code=None, *, result=None, concurrent_knowledge=None):
        reader = self.reader

        class FakeCodex:
            returncode = 0

            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])

            def poll(process):
                if concurrent_code is not None:
                    save_candidate(reader, 4, concurrent_code)
                if concurrent_knowledge is not None:
                    save_knowledge(reader, 4, concurrent_knowledge)
                item = {'rva': '0x4', 'code': code, 'edits': None, 'reply': reply,
                        'notes': '### 当前分析\n保留已观察行为，未经执行验证。', 'knowledge_edits': None}
                item.update(result or {})
                process.output.write_text(json.dumps({'results': [item]}))
                return 0

        # Real task execution/publication; only external tools are replaced.
        with patch('recovery.jobs.subprocess.Popen', FakeCodex), \
                patch('recovery.jobs.prepare_references', return_value={'status': 'unavailable'}), \
                patch('recovery.jobs.format_code', side_effect=lambda reader, code: code), \
                patch('recovery.jobs.signal.signal'):
            run_task(reader, job['id'])

    def suggestions(self, rva=4, client=None):
        response = (client or self.client).get(f'/api/functions/{rva:#x}/feedback')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['rva'], rva)
        return response.json()

    def test_same_native_name_does_not_mix_sources_or_assembly(self):
        save_candidate(self.reader, 4, 'int first(void) { return 41; }')
        save_candidate(self.reader, 8, 'int second(void) { return 82; }')
        response = self.client.get('/api/functions')
        self.assertEqual(response.status_code, 200)
        functions = response.json()['functions']
        self.assertEqual([fn['rva'] for fn in functions], [4, 8])
        self.assertEqual(functions[0]['name'], functions[1]['name'])
        self.assertIn('return 41', self.source(4)['display_code'])
        self.assertIn('return 82', self.source(8)['display_code'])
        self.assertEqual(self.client.get('/api/functions/4/candidate').json(), self.source(4))
        for rva, opcode in ((4, 'ret'), (8, 'add x0, x0, #1')):
            response = self.client.get(f'/api/functions/{rva:#x}/assembly')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['rva'], rva)
            self.assertEqual(response.json()['instructions'][0]['opcode'], opcode)

    def test_repeated_renames_keep_existing_id_rva_knowledge_and_neighbors(self):
        path = save_candidate(self.reader, 4, 'int original(void) { return 41; }')
        save_candidate(self.reader, 8, 'int neighbor(void) { return 82; }')
        # An imported ID need not follow the default f<RVA> naming convention.
        path.write_text(path.read_text().replace('f00000004', 'imported-stable-id'))
        save_knowledge(self.reader, 4, '入口 x0；此知识属于 RVA 0x4。')
        save_knowledge(self.reader, 8, '此知识属于 RVA 0x8。')
        before = self.source(4)
        neighbor = self.source(8)
        native_rows = self.native_rows()
        assembly = self.client.get('/api/functions/0x4/assembly').json()
        for name in ('parse_config', 'load_settings', 'renamed_again'):
            with self.subTest(name=name):
                save_candidate(self.reader, 4, f'int {name}(void) {{ return 41; }}')
                current = self.source(4)
                self.assertIn(f'int {name}(', current['display_code'])
                self.assertEqual(self.identity(current), self.identity(before))
                self.assertEqual(current['notes'], before['notes'])
                self.assertNotEqual(current['sha256'], before['sha256'])
                self.assertEqual(self.source(8), neighbor)
                self.assertEqual(self.native_rows(), native_rows)
                self.assertEqual(self.client.get('/api/functions/0x4/assembly').json(), assembly)

    def test_external_same_size_rename_and_file_move_refreshes_api(self):
        path = save_candidate(self.reader, 4, '// 中文说明\nint first_name(void) { return 41; }')
        save_candidate(self.reader, 8, 'int neighbor(void) { return 82; }')
        before, neighbor = self.source(4), self.source(8)
        native_rows = self.native_rows()
        stat = path.stat()
        moved = path.with_name('moved_module.c')
        path.rename(moved)
        moved.write_text(moved.read_text().replace('first_name', 'other_name'))
        self.assertEqual(moved.stat().st_size, stat.st_size)
        os.utime(moved, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        current = self.source(4)
        self.assertEqual(Path(current['path']), moved)
        self.assertEqual(self.identity(current), self.identity(before))
        self.assertIn('int other_name(', current['display_code'])
        self.assertNotEqual(current['sha256'], before['sha256'])
        self.assertEqual(self.source(8)['sha256'], neighbor['sha256'])
        self.assertEqual(Path(self.source(8)['path']), moved)
        self.assertEqual(self.native_rows(), native_rows)

    def test_reopened_app_rebuilds_derived_index_without_changing_native_data(self):
        save_candidate(self.reader, 4, 'int recovered(void) { return 41; }')
        save_knowledge(self.reader, 4, '重建索引仍保留知识。')
        before = self.source(4)
        native_rows = self.native_rows()
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute('DROP TABLE source_index')
            conn.execute("DELETE FROM metadata WHERE key='source_fingerprint'")
        reopened = self.open_client(WorkspaceReader(self.reader.directory))
        self.assertEqual(self.source(4, reopened), before)
        self.assertEqual(self.native_rows(), native_rows)
        with sqlite3.connect(self.reader.database) as conn:
            self.assertEqual(conn.execute('SELECT function_rva FROM source_index').fetchall(), [(4,)])

    def test_same_rva_in_different_so_directories_stays_independent(self):
        other = self.make_reader('two', 'b' * 64)
        save_candidate(self.reader, 4, 'int first_binary(void) { return 41; }')
        save_candidate(other, 4, 'int other_binary(void) { return 99; }')
        other_client = self.open_client(other)
        first, second = self.source(4), self.source(4, other_client)
        self.assertEqual(self.identity(first), self.identity(second))
        self.assertIn('return 41', first['display_code'])
        self.assertIn('return 99', second['display_code'])
        self.assertNotEqual(first['path'], second['path'])
        self.assertNotEqual(first['sha256'], second['sha256'])

    def test_feedback_keeps_rva_and_snapshot_and_rejects_stale_source(self):
        save_candidate(self.reader, 4, 'int original(void) { return 41; }')
        before = self.source(4)
        # Only execution/discovery is stubbed; validation and task persistence run.
        with patch('recovery.queue._ensure_worker'), \
                patch('recovery.queue.shutil.which', return_value='/unused/codex'):
            body = {'comment': '请检查命名', 'expected_sha256': before['sha256']}
            response = self.client.post('/api/functions/0x4/feedback', json=body)
            self.assertEqual(response.status_code, 200, response.text)
            queued = response.json()
            self.assertEqual(queued['rvas'], [4])
            reviewed = Path(queued['directory']) / 'reviewed.c'
            self.assertEqual(reviewed.read_text(), before['code'])
            save_candidate(self.reader, 4, 'int parse_config(void) { return 41; }')
            current = self.source(4)
            states_before = sorted((self.reader.directory / 'tasks').glob('*/state.json'))
            duplicate = self.client.post('/api/functions/0x4/feedback', json=body)
            self.assertEqual(duplicate.status_code, 200, duplicate.text)
            self.assertEqual(duplicate.json()['id'], queued['id'])
            body['comment'] = '另请核对新的调用名称'
            stale = self.client.post('/api/functions/0x4/feedback', json=body)
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertEqual(sorted((self.reader.directory / 'tasks').glob('*/state.json')), states_before)
            body['expected_sha256'] = current['sha256']
            response = self.client.post('/api/functions/4/feedback', json=body)
            self.assertEqual(response.status_code, 200, response.text)
            latest = response.json()
            self.assertEqual(latest['rvas'], [4])
            self.assertNotEqual(latest['id'], queued['id'])
            self.assertEqual(reviewed.read_text(), before['code'])
            persisted = json.loads((Path(queued['directory']) / 'state.json').read_text())
            self.assertEqual(persisted['rvas'], [4])
            self.assertEqual(persisted['expected_sha256'], before['sha256'])

    def test_damaged_markers_return_conflict_without_clearing_valid_index(self):
        path = save_candidate(self.reader, 4, 'int first(void) { return 41; }')
        save_candidate(self.reader, 8, 'int neighbor(void) { return 82; }')
        original = path.read_text()
        before = self.source(4)
        native_rows = self.native_rows()
        with sqlite3.connect(self.reader.database) as conn:
            index_before = conn.execute('SELECT * FROM source_index ORDER BY function_rva').fetchall()
        path.write_text(original.replace('@lightre.end {"id":"f00000004"}',
                                         '@lightre.end {"id":"wrong-id"}'))
        for rva in (4, 8):
            response = self.client.get(f'/api/functions/{rva:#x}/candidate')
            self.assertEqual(response.status_code, 409, response.text)
        with sqlite3.connect(self.reader.database) as conn:
            self.assertEqual(conn.execute('SELECT * FROM source_index ORDER BY function_rva').fetchall(),
                             index_before)
        self.assertEqual(self.native_rows(), native_rows)
        path.write_text(original)
        self.assertEqual(self.source(4), before)

    def test_missing_source_unknown_rva_and_invalid_address_are_distinct(self):
        missing = self.source(4)
        self.assertEqual(missing['status'], 'missing')
        self.assertIsNone(missing['code'])
        self.assertEqual(self.client.get('/api/functions/0xc/candidate').status_code, 404)
        self.assertEqual(self.client.get('/api/functions/not-an-address/candidate').status_code, 400)

    def test_feedback_queue_position_state_and_function_isolation(self):
        save_candidate(self.reader, 4, 'int first(void) { return 41; }')
        save_candidate(self.reader, 8, 'int other(void) { return 82; }')
        first = self.submit_suggestion('第一条建议')
        other = self.submit_suggestion('其他函数的建议', 8)
        second = self.submit_suggestion('第二条建议')
        data = self.suggestions()
        self.assertEqual([item['id'] for item in data['items']], [second['id'], first['id']])
        self.assertEqual([item['ahead'] for item in data['items']], [2, 0])
        self.assertNotIn(other['id'], [item['id'] for item in data['items']])
        self.assertTrue(all(item['result'] is None for item in data['items']))
        first.update(status='running', started_at=first['created_at'])
        write_state(Path(first['directory']) / 'state.json', first)
        with (self.reader.directory / 'tasks/.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.suggestions()['items'][1]['status'], 'running')
        self.assertEqual(self.suggestions()['items'][1]['status'], 'interrupted')
        (self.reader.directory / 'tasks/.paused').touch()
        self.assertTrue(self.suggestions()['paused'])

    def test_suggestion_activity_reports_actual_cli_events_and_silence(self):
        save_candidate(self.reader, 4, 'int first(void) { return 41; }')
        job = self.submit_suggestion()
        directory = Path(job['directory'])
        job.update(status='running', phase='recovery', started_at=job['created_at'],
                   current=['0x4'], batches={'0': {'status': 'running', 'rvas': [4]}})
        write_state(directory / 'state.json', job)
        events = directory / 'batch-0000.events.jsonl'
        with (self.reader.directory / 'tasks/.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.suggestions()['items'][0]['activity']['stage'], 'starting')
            events.write_text(json.dumps({'type': 'turn.started'}) + '\n')
            self.assertEqual(self.suggestions()['items'][0]['activity']['stage'], 'model')
            command = {'type': 'command_execution', 'command': 'objdump -d binary/original.so',
                       'aggregated_output': 'this output must not be sent to the page'}
            with events.open('a') as stream:
                stream.write(json.dumps({'type': 'item.started', 'item': command}) + '\n')
            active = self.suggestions()['items'][0]['activity']
            self.assertEqual(active['stage'], 'tool')
            self.assertEqual(active['detail'], command['command'])
            self.assertNotIn('aggregated_output', active)
            # Another function must not show this function's activity.
            self.assertEqual(self.suggestions(8)['items'], [])
            with events.open('a') as stream:
                stream.write(json.dumps({'type': 'item.completed', 'item': command}) + '\n')
                stream.write('{"type":"turn.completed"')  # still being appended
            os.utime(events, (1000, 1000))
            with patch('recovery.feedback.time.time', return_value=1042):
                active = self.suggestions()['items'][0]['activity']
            self.assertEqual(active['stage'], 'model')
            self.assertEqual(active['silent_seconds'], 42)
            with events.open('a') as stream:
                stream.write('}\n')
            self.assertEqual(self.suggestions()['items'][0]['activity']['stage'], 'publishing')
            job.update(status='complete')
            write_state(directory / 'state.json', job)
            self.assertIsNone(self.suggestions()['items'][0]['activity'])

    def test_suggestion_activity_handles_large_and_invalid_log_records(self):
        save_candidate(self.reader, 4, 'int first(void) { return 41; }')
        job = self.submit_suggestion()
        directory = Path(job['directory'])
        job.update(status='running', phase='recovery', batches={
            '0': {'status': 'running', 'rvas': [4]}})
        write_state(directory / 'state.json', job)
        events = directory / 'batch-0000.events.jsonl'
        events.write_text(json.dumps({'type': 'item.completed', 'item': {
            'type': 'command_execution', 'aggregated_output': 'x' * 300000}})
            + '\ninvalid\n[]\n' + json.dumps({'type': 'turn.completed'}) + '\n')
        with (self.reader.directory / 'tasks/.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.suggestions()['items'][0]['activity']['stage'], 'publishing')

    def test_cancel_api_targets_only_selected_suggestion_without_pausing_queue(self):
        save_candidate(self.reader, 4, 'int first(void) { return 41; }')
        save_candidate(self.reader, 8, 'int other(void) { return 82; }')
        before = self.source(4)['sha256']
        first = self.submit_suggestion('当前建议')
        other = self.submit_suggestion('其他函数的建议', 8)
        response = self.client.post(f"/api/tasks/{first['id']}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'cancelled')
        self.assertEqual(self.suggestions()['items'][0]['status'], 'cancelled')
        self.assertEqual(self.suggestions(8)['items'][0]['status'], 'queued')
        # Running cancellation is acknowledged first; the worker observes the
        # durable marker and stops its own batch, leaving other tasks intact.
        other.update(status='running')
        write_state(Path(other['directory']) / 'state.json', other)
        with (self.reader.directory / 'tasks/.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            response = self.client.post(f"/api/tasks/{other['id']}/cancel")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'cancelling')
            self.assertTrue((Path(other['directory']) / 'cancel').exists())
        self.assertFalse((self.reader.directory / 'tasks/.paused').exists())
        self.assertEqual(self.source(4)['sha256'], before)
        self.assertEqual(self.client.post('/api/tasks/unknown/cancel').status_code, 404)

    def test_shared_eight_slots_run_independent_suggestions_and_serialize_same_rva(self):
        rvas = list(range(4, 40, 4))
        with sqlite3.connect(self.reader.database) as conn:
            conn.executemany("INSERT INTO functions VALUES (?,'extra',1,'[]')", [(rva,) for rva in rvas[2:]])
        for rva in rvas:
            save_candidate(self.reader, rva, f'int fn_{rva}(void) {{ return 1; }}')
        jobs = [self.submit_suggestion(f'建议 {rva}', rva) for rva in rvas]
        second = self.submit_suggestion('同一函数的下一条建议', 4)
        live, launched, maximum, checked = {}, [], [], []
        case = self

        class FakeCodex:
            returncode = None
            ticks = 0

            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])
                process.inputs = json.loads(process.output.with_suffix('').with_suffix('.input.json').read_text())
                process.rva = int(next(iter(process.inputs)), 0)
                case.assertNotIn(process.rva, live, 'Two jobs must never lease the same RVA')
                if process.output.parent.name == second['id']:
                    case.assertIn('return 2', process.inputs['0x4'], 'FIFO successor must read the published predecessor')
                live[process.rva] = process
                launched.append(process.output.parent.name)
                maximum.append(len(live))

            def poll(process):
                process.ticks += 1
                if len(launched) < 8 or process.ticks < 3:
                    return None
                if not checked:
                    checked.append(True)
                    capacity = case.client.get('/api/recovery').json()['concurrency']
                    case.assertEqual(capacity, {'in_use': 8, 'limit': 8})
                    case.assertEqual(case.suggestions(rvas[-1])['items'][0]['wait_reason'], 'capacity')
                    case.assertEqual(case.suggestions(4)['items'][0]['wait_reason'], 'same_function')
                if process.returncode is None:
                    code = process.inputs[f'{process.rva:#x}']
                    old, new = ('return 2', 'return 3') if 'return 2' in code else ('return 1', 'return 2')
                    process.output.write_text(json.dumps({'results': [{
                        'rva': f'{process.rva:#x}', 'code': None, 'edits': [{'old': old, 'new': new}],
                        'notes': f'当前返回值为 {new.split()[-1]}，未经执行验证。',
                        'reply': '按建议修改返回值。', 'knowledge_edits': None}]}))
                    process.returncode = 0
                    del live[process.rva]
                return 0

        dispatcher = self.reader.directory / 'tasks/.dispatcher.lock'
        with dispatcher.open('a+') as lock, patch('recovery.jobs.subprocess.Popen', FakeCodex), \
                patch('recovery.jobs.time.sleep'), patch.dict(os.environ, {'LIGHTRE_CODEX_WORKERS': '8'}), \
                patch('recovery.jobs.prepare_references', side_effect=AssertionError('comment must not run Ghidra')), \
                patch('recovery.jobs.prepare_source', side_effect=AssertionError('comment must not prepare all sources')), \
                patch('recovery.jobs.format_code', side_effect=lambda reader, code: code):
            fcntl.flock(lock, fcntl.LOCK_EX)
            run_queue(self.reader, lock.fileno())
        self.assertEqual(max(maximum), 8)
        self.assertEqual(set(launched), {job['id'] for job in jobs} | {second['id']})
        self.assertIn('return 3', self.source(4)['display_code'])
        self.assertEqual([item['status'] for item in self.suggestions()['items']], ['complete', 'complete'])
        self.assertEqual(self.client.get('/api/recovery').json()['concurrency'], {'in_use': 0, 'limit': 8})

    def test_pause_stops_all_shared_batches_and_resume_keeps_tasks_and_source(self):
        for rva in (4, 8):
            save_candidate(self.reader, rva, f'int fn_{rva}(void) {{ return 1; }}')
        jobs = [self.submit_suggestion('本轮建议', rva) for rva in (4, 8)]
        started, stopped, paused = [], [], []
        resuming = False
        case = self

        class FakeCodex:
            returncode = None

            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])
                schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
                process.rva = int(schema['properties']['results']['items']['properties']['rva']['enum'][0], 0)
                started.append(process)

            def poll(process):
                if not resuming:
                    if len(started) == 2 and not paused:
                        paused.append(case.client.post('/api/recovery/stop').status_code)
                    return None
                process.output.write_text(json.dumps({'results': [{
                    'rva': f'{process.rva:#x}', 'code': None,
                    'edits': [{'old': 'return 1', 'new': 'return 2'}], 'notes': '当前返回值为 2。',
                    'reply': '已完成本次建议', 'knowledge_edits': None}]}))
                process.returncode = 0
                return 0

        dispatcher = self.reader.directory / 'tasks/.dispatcher.lock'
        with patch('recovery.jobs.subprocess.Popen', FakeCodex), patch('recovery.jobs.time.sleep'), \
                patch('recovery.queue._signal'), patch('recovery.queue._ensure_worker'), \
                patch('recovery.jobs._terminate', side_effect=stopped.append), \
                patch('recovery.jobs.format_code', side_effect=lambda reader, code: code):
            with dispatcher.open('a+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                run_queue(self.reader, lock.fileno())
            self.assertEqual(paused, [200])
            self.assertEqual(len(stopped), 2)
            self.assertTrue(self.client.get('/api/recovery').json()['paused'])
            self.assertEqual({self.suggestions(rva)['items'][0]['status'] for rva in (4, 8)}, {'interrupted'})
            self.assertIn('return 1', self.source(4)['display_code'])
            resuming = True
            self.assertEqual(self.client.post('/api/recovery').status_code, 200)
            with dispatcher.open('a+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                run_queue(self.reader, lock.fileno())
        queue = self.client.get('/api/recovery').json()['queue']
        self.assertEqual({item['id'] for item in queue}, {job['id'] for job in jobs})
        self.assertTrue(all(item['status'] == 'complete' for item in queue))
        self.assertIn('return 2', self.source(4)['display_code'])
        self.assertIn('return 2', self.source(8)['display_code'])

    def test_comment_uses_first_free_slot_without_waiting_for_unrelated_bulk_batch(self):
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute('UPDATE functions SET instructions=4000')
            conn.executemany("INSERT INTO functions VALUES (?,'extra',4000,'[]')", [(12,), (16,)])
            conn.execute('DELETE FROM assembly')
            for rva in (4, 8, 12, 16):
                conn.executemany('INSERT INTO assembly VALUES (?,?,?,?,?)', [
                    (rva, n, rva + n * 4, '1f2003d5', 'nop') for n in range(4000)])
        save_candidate(self.reader, 16, 'int comment_target(void) { return 1; }')
        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/unused/codex'):
            bulk = submit(self.reader, workers=2)
        comments, events, live, maximum = [], [], set(), []
        case = self

        class FakeCodex:
            returncode = None
            ticks = 0

            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])
                schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
                process.rva = int(schema['properties']['results']['items']['properties']['rva']['enum'][0], 0)
                process.kind = 'bulk' if process.output.parent.name == bulk['id'] else 'comment'
                case.assertNotIn(process.rva, live)
                live.add(process.rva)
                maximum.append(len(live))
                events.append(('start', process.kind, process.rva))

            def poll(process):
                process.ticks += 1
                if not comments:
                    comments.append(case.submit_suggestion('同函数建议', 4))
                    comments.append(case.submit_suggestion('其他函数建议', 16))
                if process.kind == 'bulk' and process.rva == 4 and ('finish', 'comment', 16) not in events:
                    case.assertLess(process.ticks, 100, 'An unrelated comment must get the free slot')
                    return None
                if process.returncode is None:
                    process.output.write_text(json.dumps({'results': [{
                        'rva': f'{process.rva:#x}', 'code': f'int fn_{process.rva}(void) {{ return 2; }}',
                        'notes': '当前返回值为 2。', 'edits': None,
                        'reply': '按建议修改返回值。', 'knowledge_edits': None}]}))
                    process.returncode = 0
                    live.remove(process.rva)
                    events.append(('finish', process.kind, process.rva))
                return 0

        dispatcher = self.reader.directory / 'tasks/.dispatcher.lock'
        with dispatcher.open('a+') as lock, patch('recovery.jobs.subprocess.Popen', FakeCodex), \
                patch('recovery.jobs.time.sleep'), patch.dict(os.environ, {'LIGHTRE_CODEX_WORKERS': '2'}), \
                patch('recovery.jobs.prepare_references', return_value={'status': 'unavailable'}), \
                patch('recovery.jobs.format_code', side_effect=lambda reader, code: code):
            fcntl.flock(lock, fcntl.LOCK_EX)
            run_queue(self.reader, lock.fileno())
        self.assertEqual(max(maximum), 2)
        self.assertLess(events.index(('finish', 'comment', 16)), events.index(('finish', 'bulk', 4)))
        self.assertLess(events.index(('finish', 'bulk', 4)), events.index(('start', 'comment', 4)))
        self.assertLess(events.index(('start', 'comment', 16)), events.index(('start', 'bulk', 12)))
        self.assertEqual(self.suggestions(16)['items'][0]['status'], 'complete')

    def test_focused_feedback_inlines_context_and_publishes_patch_with_short_reply(self):
        save_candidate(self.reader, 4, 'int first(void) { int param_1 = 41; return param_1; }')
        save_knowledge(self.reader, 4, '### 既有证据\n返回值来自常量 41，未经执行验证。')
        before = self.source(4)
        job = self.submit_suggestion('把 param_1 改成 result')
        case = self

        class FakeCodex:
            returncode = None

            def __init__(process, command, **kwargs):
                process.output = Path(command[command.index('-o') + 1])
                prompt = kwargs['stdin'].read().decode()
                case.assertIn('把 param_1 改成 result', prompt)
                case.assertIn('int param_1 = 41', prompt)
                case.assertIn('返回值来自常量 41', prompt)
                case.assertNotIn('shell_tool', command, 'Necessary read-only evidence access stays available')
                schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
                case.assertIn('edits', schema['properties']['results']['items']['properties'])

            def poll(process):
                process.returncode = 0
                process.output.write_text(json.dumps({'results': [{
                    'rva': '0x4', 'code': None,
                    'edits': [{'old': 'int param_1 = 41; return param_1;', 'new': 'int result = 41; return result;'}],
                    'notes': '### 既有证据\nresult 的返回值来自常量 41，未经执行验证。',
                    'knowledge_edits': None, 'reply': '将声明和返回处的 param_1 改为 result，保持逻辑。'}]}))
                return 0

        with patch('recovery.jobs.subprocess.Popen', FakeCodex), patch('recovery.jobs.signal.signal'), \
                patch('recovery.jobs.prepare_references', side_effect=AssertionError('must not run Ghidra')), \
                patch('recovery.jobs.prepare_source', side_effect=AssertionError('must not seed other functions')), \
                patch('recovery.jobs.format_code', side_effect=lambda reader, code: code):
            run_task(self.reader, job['id'])
        result = self.source(4)
        self.assertEqual(self.identity(result), self.identity(before))
        self.assertIn('int result = 41; return result;', result['display_code'])
        self.assertIn('返回值来自常量 41', result['notes'])
        self.assertIn('result 的返回值', result['notes'])
        self.assertNotIn('param_1', result['notes'])
        reply = self.suggestions()['items'][0]['result']
        self.assertEqual(reply['outcome'], 'updated')
        self.assertNotIn('既有证据', reply['reply'])
        self.assertIn('+int first', reply['diff'])

    def test_queue_reports_issue_rvas_for_failed_and_unresolved_navigation(self):
        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/unused/codex'):
            job = submit(self.reader, [4, 8])
        job.update(status='partial', done_rvas=[4, 8], processed=2, failed_count=1,
                   failed_rvas={'0x4': 'CLI 超时'},
                   unresolved={'0x4': '重复的执行失败记录', '0x8': '尚未确定返回类型'})
        write_state(Path(job['directory']) / 'state.json', job)
        response = self.client.get('/api/recovery')
        self.assertEqual(response.status_code, 200, response.text)
        queued = next(item for item in response.json()['queue'] if item['id'] == job['id'])
        self.assertEqual(queued['rvas'], [4, 8])
        self.assertEqual(queued['issues'], [
            {'rva': 4, 'status': 'failed', 'reason': 'CLI 超时'},
            {'rva': 8, 'status': 'unresolved', 'reason': '尚未确定返回类型'},
        ])
        # Links can read each original RVA even if no C candidate exists yet.
        for item in queued['issues']:
            self.assertEqual(self.source(item['rva'])['status'], 'missing')
            self.assertEqual(self.client.get(f"/api/functions/{item['rva']:#x}/assembly").status_code, 200)

    def test_successive_feedback_consolidates_names_behavior_and_evidence(self):
        save_candidate(self.reader, 4, 'int first(void) { int param_1 = 41; return param_1; }')
        evidence = '### 证据与疑点\n0x4 的立即数为 41；调用约定尚待确认，未经执行验证。'
        save_knowledge(self.reader, 4, '### 当前行为\nfirst 通过 param_1 返回 41。\n\n' + evidence)
        save_knowledge(self.reader, 8, '其他函数的独立知识。')
        native = self.native_rows()
        identity = self.identity(self.source(4))
        first = self.submit_suggestion('将 param_1 重命名为 result')
        self.finish_suggestion(first, None, '已重命名局部变量。', result={
            'edits': [{'old': 'param_1', 'new': 'result'}], 'notes': None,
            'knowledge_edits': [{'old': 'first 通过 param_1 返回 41。', 'new': 'first 通过 result 返回 41。'}]})
        interim = self.source(4)
        self.assertNotIn('param_1', interim['notes'])
        self.assertIn('first 通过 result 返回 41。', interim['notes'])
        second = self.submit_suggestion('将函数命名为 read_value，并把先前的业务含义猜测改为常量返回')
        final_notes = '### 当前行为\nread_value 通过 result 返回常量 41，业务含义未知。\n\n' + evidence
        self.finish_suggestion(second, None, '已更新函数名和分析结论。', result={
            'edits': [{'old': 'int first(void)', 'new': 'int read_value(void)'}],
            'notes': final_notes, 'knowledge_edits': None})
        final = self.source(4)
        self.assertEqual(final['notes'], final_notes)
        self.assertNotIn('first', final['notes'])
        self.assertNotIn('建议处理', final['notes'])
        self.assertNotIn('已更新函数名', final['notes'])
        self.assertEqual(self.identity(final), identity)
        self.assertEqual(self.native_rows(), native)
        self.assertEqual(self.source(8)['notes'], '其他函数的独立知识。')
        history = self.suggestions()['items']
        self.assertEqual([item['result']['reply'] for item in history],
                         ['已更新函数名和分析结论。', '已重命名局部变量。'])
        baselines = json.loads((Path(second['directory']) / 'batch-0000.before.json').read_text())
        self.assertEqual(baselines['4']['knowledge'], interim['notes'])

    def test_feedback_can_consolidate_old_round_logs_without_changing_source(self):
        save_candidate(self.reader, 4, 'int read_value(void) { return 41; }')
        save_knowledge(self.reader, 4, 'first 返回 41。\n\n### 建议处理\nfirst 已改名为 read_value。'
                       '\n\n### 建议处理\n返回值仍为 41，含义未知。')
        before = self.source(4)
        notes = '### 当前分析\nread_value 返回常量 41，业务含义未知，未经执行验证。'
        job = self.submit_suggestion('整合重复说明')
        self.finish_suggestion(job, None, '已整合当前分析。', result={'edits': [], 'notes': notes})
        after = self.source(4)
        self.assertEqual(after['sha256'], before['sha256'])
        self.assertEqual(after['notes'], notes)
        self.assertEqual(self.suggestions()['items'][0]['result']['outcome'], 'unchanged')
        self.assertEqual(self.source(4, client=self.open_client(WorkspaceReader(self.reader.directory)))['notes'], notes)

    def test_feedback_knowledge_edits_apply_in_order_and_may_keep_current_document(self):
        save_candidate(self.reader, 4, 'int read_value(void) { return 41; }')
        save_knowledge(self.reader, 4, '### 当前行为\n返回值是 41，业务含义未知。\n\n### 证据\n0x4：常量 41。')
        job = self.submit_suggestion('修正说明中的名字')
        self.finish_suggestion(job, None, '已修正说明。', result={
            'edits': [], 'notes': None, 'knowledge_edits': [
                {'old': '返回值是 41，业务含义未知。', 'new': 'value 是常量 41，业务含义未知。'},
                {'old': 'value 是常量 41，业务含义未知。', 'new': 'read_value 返回常量 41，业务含义未知。'}]})
        current = self.source(4)
        self.assertIn('read_value 返回常量 41', current['notes'])
        self.assertIn('0x4：常量 41。', current['notes'])
        job = self.submit_suggestion('核对说明是否还有效')
        self.finish_suggestion(job, None, '现有说明仍然适用。', result={
            'edits': [], 'notes': None, 'knowledge_edits': []})
        self.assertEqual(self.source(4), current)
        self.assertEqual(self.suggestions()['items'][0]['status'], 'complete')

    def test_invalid_knowledge_update_never_publishes_source_or_overwrites_notes(self):
        body = 'int original(void) { return 41; }'
        existing = '### 当前分析\n旧名称存在；旧名称有待确认。'
        invalid = [
            {'notes': None, 'knowledge_edits': [{'old': '旧名称', 'new': '新名称'}]},
            {'notes': None, 'knowledge_edits': [{'old': '不存在的段落', 'new': '新名称'}]},
            {'notes': '新说明', 'knowledge_edits': []},
            {'notes': None, 'knowledge_edits': [{'old': existing, 'new': ''}]},
            {'notes': '```c\n未闭合的代码块', 'knowledge_edits': None},
            {'notes': None, 'knowledge_edits': [{'old': existing, 'new': '```\n未闭合'}]},
            {'notes': '', 'knowledge_edits': None},
            {'reply': None},
        ]
        for number, result in enumerate(invalid):
            with self.subTest(result=result):
                save_candidate(self.reader, 4, body)
                save_knowledge(self.reader, 4, existing)
                before = self.source(4)
                job = self.submit_suggestion(f'无效知识更新 {number}')
                with patch('recovery.jobs.MAX_BATCH_RETRIES', 0):
                    self.finish_suggestion(job, 'int proposed(void) { return 42; }', '修改提案。', result=result)
                self.assertEqual(self.source(4), before)
                self.assertEqual(self.suggestions()['items'][0]['status'], 'partial')
                self.assertIn('0x4', self.client.get('/api/recovery').json()['task']['failed_rvas'])

    def test_empty_knowledge_requires_a_complete_document(self):
        save_candidate(self.reader, 4, 'int original(void) { return 41; }')
        before = self.source(4)
        job = self.submit_suggestion('不能用空补丁代替第一份说明')
        with patch('recovery.jobs.MAX_BATCH_RETRIES', 0):
            self.finish_suggestion(job, 'int renamed(void) { return 41; }', '改名提案。', result={
                'notes': None, 'knowledge_edits': []})
        self.assertEqual(self.source(4), before)
        self.assertEqual(self.suggestions()['items'][0]['status'], 'partial')

    def test_external_knowledge_change_rejects_both_proposed_source_and_notes(self):
        save_candidate(self.reader, 4, 'int original(void) { return 41; }')
        save_knowledge(self.reader, 4, '### 当前分析\noriginal 返回 41。')
        before = self.source(4)
        job = self.submit_suggestion('修改函数名')
        external = '### 新增证据\n外部刚补充的地址与待验证结论。'
        self.finish_suggestion(job, 'int proposed(void) { return 41; }', '已改名。',
                               concurrent_knowledge=external, result={'notes': 'proposed 返回 41。'})
        after = self.source(4)
        self.assertEqual(after['sha256'], before['sha256'])
        self.assertEqual(after['notes'], external)
        result = self.suggestions()['items'][0]['result']
        self.assertEqual(result['outcome'], 'conflict')
        self.assertIn('未发布', result['reply'])
        self.assertEqual(result['diff'], '')

    def test_source_conflict_keeps_current_knowledge(self):
        save_candidate(self.reader, 4, 'int original(void) { return 41; }')
        notes = '### 当前分析\n仍需核对外部调用。'
        save_knowledge(self.reader, 4, notes)
        job = self.submit_suggestion('修改返回值')
        self.finish_suggestion(job, 'int proposed(void) { return 42; }', '改为 42。',
                               concurrent_code='int external(void) { return 99; }',
                               result={'notes': 'proposed 返回 42。'})
        self.assertEqual(self.source(4)['notes'], notes)
        self.assertIn('return 99', self.source(4)['display_code'])
        self.assertEqual(self.suggestions()['items'][0]['result']['outcome'], 'conflict')

    def test_replaying_published_feedback_keeps_consolidated_document_without_duplicates(self):
        save_candidate(self.reader, 4, 'int original(void) { return 41; }')
        save_knowledge(self.reader, 4, '### 当前行为\noriginal 返回 41。')
        job = self.submit_suggestion('重命名函数')
        notes = '### 当前行为\nrenamed 返回 41，未经执行验证。'
        self.finish_suggestion(job, None, '已重命名函数。', result={
            'edits': [{'old': 'int original(void)', 'new': 'int renamed(void)'}], 'notes': notes})
        published = self.source(4)
        directory = Path(job['directory'])
        state = json.loads((directory / 'state.json').read_text())
        # Simulate interruption after files were saved but before the task checkpoint.
        state.update(status='interrupted', done_rvas=[], processed=0, generated=0)
        state['batches']['0']['status'] = 'running'
        write_state(directory / 'state.json', state)
        with patch('recovery.jobs.subprocess.Popen', side_effect=AssertionError('saved output must be replayed')), \
                patch('recovery.jobs.save_candidate', side_effect=AssertionError('already published')), \
                patch('recovery.jobs.format_code', side_effect=lambda reader, code: code), \
                patch('recovery.jobs.signal.signal'):
            run_task(self.reader, job['id'])
        self.assertEqual(self.source(4), published)
        result = self.suggestions()['items'][0]['result']
        self.assertEqual(result['outcome'], 'updated')
        self.assertEqual(result['reply'], '已重命名函数。')
        self.assertIn('+int renamed', result['diff'])

    def test_legacy_saved_feedback_remains_recoverable(self):
        save_candidate(self.reader, 4, 'int original(void) { return 41; }')
        save_knowledge(self.reader, 4, '旧协议的有效证据。')
        before = self.source(4)
        job = self.submit_suggestion('旧任务恢复')
        directory = Path(job['directory'])
        baseline = {key: before[key] for key in ('display_code', 'sha256', 'path')}
        write_state(directory / 'batch-0000.before.json', {'4': baseline})
        write_state(directory / 'batch-0000.input.json', {'0x4': before['display_code']})
        write_state(directory / 'batch-0000.result.json', {'results': [{
            'rva': '0x4', 'code': 'int original(void) { return 42; }', 'notes': '旧协议的增量回复。'}]})
        job['batches'] = {'0': {'status': 'running', 'rvas': [4], 'before': {'4': before['sha256']}}}
        write_state(directory / 'state.json', job)
        with patch('recovery.jobs.subprocess.Popen', side_effect=AssertionError('saved output must be replayed')), \
                patch('recovery.jobs.format_code', side_effect=lambda reader, code: code), \
                patch('recovery.jobs.signal.signal'):
            run_task(self.reader, job['id'])
        result = self.suggestions()['items'][0]
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['result']['reply'], '旧协议的增量回复。')
        self.assertIn('return 42', self.source(4)['display_code'])
        self.assertIn('旧协议的有效证据。', self.source(4)['notes'])

    def test_clearing_finished_tasks_preserves_published_code_knowledge_and_notes(self):
        save_candidate(self.reader, 4, 'int first(void) { int param_1 = 41; return param_1; }')
        save_candidate(self.reader, 8, 'int other(void) { return 8; }')
        save_knowledge(self.reader, 4, '### 已有证据\n返回值为 41。')
        native = self.native_rows()
        job = self.submit_suggestion('重命名变量')
        code = 'int first(void) { int result = 41; return result; }'
        reply = '### 本次修改\n将声明和返回处的 param_1 改为 result；返回值仍为 41。'
        self.finish_suggestion(job, code, reply, result={'notes': '### 已有证据\nresult 的返回值为 41。'})
        published = self.source(4)
        self.assertIn('int result = 41', published['display_code'])
        self.assertEqual(published['notes'], '### 已有证据\nresult 的返回值为 41。')
        self.assertEqual(self.suggestions()['items'][0]['result']['outcome'], 'updated')
        queued = self.submit_suggestion('尚未执行的建议', 8)
        response = self.client.post('/api/tasks/clear')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'cleared': 1})
        queue = self.client.get('/api/recovery').json()['queue']
        self.assertEqual([item['id'] for item in queue], [queued['id']])
        self.assertEqual(self.suggestions()['items'], [])
        self.assertEqual(self.source(4), published)
        self.assertEqual(self.native_rows(), native)
        self.assertTrue((Path(job['directory']) / 'feedback-result.json').exists(), 'Evidence remains archived')
        with patch('recovery.queue._ensure_worker'):
            reopened = self.open_client(WorkspaceReader(self.reader.directory))
        self.assertEqual(self.suggestions(client=reopened)['items'], [])
        self.assertEqual(self.source(4, client=reopened), published)
        self.assertEqual(self.client.post('/api/tasks/clear').json(), {'cleared': 0})

    def test_clear_function_replies_is_scoped_and_keeps_active_and_resumable_tasks(self):
        for rva in (4, 8):
            save_candidate(self.reader, rva, f'int fn_{rva}(void) {{ return 1; }}')
        first = self.submit_suggestion('当前函数的已结束建议')
        other = self.submit_suggestion('另一个函数的已结束建议', 8)
        active = self.submit_suggestion('仍在排队')
        running = self.submit_suggestion('正在执行的建议')
        running.update(status='running', phase='recovery', current=['0x4'])
        write_state(Path(running['directory']) / 'state.json', running)
        resumable = self.submit_suggestion('暂停后还能继续')
        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/unused/codex'):
            bulk = submit(self.reader, [4, 8])
        for job, status in [(first, 'complete'), (other, 'error'), (resumable, 'interrupted'), (bulk, 'partial')]:
            job['status'] = status
            write_state(Path(job['directory']) / 'state.json', job)
        lock = self.enterContext((self.reader.directory / 'tasks/.lock').open('a+'))
        fcntl.flock(lock, fcntl.LOCK_EX)
        response = self.client.post('/api/functions/0x4/feedback/clear')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'cleared': 1})
        self.assertEqual({item['id'] for item in self.suggestions()['items']}, {active['id'], running['id'], resumable['id']})
        self.assertEqual(self.suggestions(8)['items'][0]['id'], other['id'])
        visible = {job['id'] for job in self.client.get('/api/recovery').json()['queue']}
        self.assertEqual(visible, {other['id'], active['id'], running['id'], resumable['id'], bulk['id']})
        self.assertEqual(self.client.post('/api/tasks/clear').json(), {'cleared': 2})
        visible = {job['id'] for job in self.client.get('/api/recovery').json()['queue']}
        self.assertEqual(visible, {active['id'], running['id'], resumable['id']})
        self.assertEqual(self.client.post('/api/functions/bad/feedback/clear').status_code, 400)
        self.assertEqual(self.client.post('/api/functions/0xc/feedback/clear').status_code, 404)

    def test_clear_hides_finished_failure_without_changing_function_result(self):
        with patch('recovery.queue._ensure_worker'), patch('recovery.queue.shutil.which', return_value='/unused/codex'):
            job = submit(self.reader, [4])
        job.update(status='partial', processed=1, done_rvas=[4], failed_rvas={'0x4': 'CLI 超时'})
        write_state(Path(job['directory']) / 'state.json', job)
        save_knowledge(self.reader, 4, '### 尚未解决\n本次 CLI 超时，尚无源码。')
        before = self.source(4)
        self.assertEqual(self.client.get('/api/recovery').json()['function_statuses']['4'], 'failed')
        self.assertEqual(self.client.post('/api/tasks/clear').json(), {'cleared': 1})
        progress = self.client.get('/api/recovery').json()
        self.assertEqual(progress['queue'], [])
        self.assertEqual(progress['task']['status'], 'idle')
        self.assertEqual(progress['function_statuses']['4'], 'failed')
        self.assertEqual(self.source(4), before)

    def test_feedback_reply_and_diff_are_published_and_survive_later_edits(self):
        save_candidate(self.reader, 4, 'int original(void) { return 41; }')
        job = self.submit_suggestion()
        reply = '已将 original 的返回值由 41 改为 42，尚未执行验证。'
        self.finish_suggestion(job, 'int original(void) { return 42; }', reply)
        item = self.suggestions()['items'][0]
        self.assertEqual(item['status'], 'complete')
        self.assertEqual(self.suggestions()['progress']['state'], 'complete')
        result = item['result']
        self.assertEqual(result['outcome'], 'updated')
        self.assertEqual(result['reply'], reply)
        self.assertIn('-int original(void) { return 41; }', result['diff'])
        self.assertIn('+int original(void) { return 42; }', result['diff'])
        self.assertEqual((result['added_lines'], result['removed_lines']), (1, 1))
        self.assertEqual(result['after_sha256'], self.source(4)['sha256'])
        self.assertTrue(result['current_matches'])
        self.assertEqual(Path(result['path']), Path(self.source(4)['path']))
        save_candidate(self.reader, 4, 'int later(void) { return 99; }')
        current = self.suggestions()['items'][0]['result']
        self.assertFalse(current['current_matches'])
        self.assertEqual(current['diff'], result['diff'])
        self.assertEqual(current['reply'], result['reply'])
        reopened = self.open_client(WorkspaceReader(self.reader.directory))
        self.assertEqual(self.suggestions(client=reopened)['items'][0]['result'], current)

    def test_feedback_distinguishes_unchanged_source_from_unresolved(self):
        body = 'int original(void) { return 41; }'
        save_candidate(self.reader, 4, body)
        for code, outcome, status in ((body, 'unchanged', 'complete'), (None, 'unresolved', 'partial')):
            with self.subTest(outcome=outcome):
                job = self.submit_suggestion(outcome)
                reply = '保留已有实现，需要更多证据。'
                before = self.source(4)
                self.finish_suggestion(job, code, reply)
                item = self.suggestions()['items'][0]
                self.assertEqual(item['status'], status)
                self.assertEqual(item['result']['outcome'], outcome)
                self.assertEqual(self.suggestions()['progress']['state'],
                                 'unresolved' if outcome == 'unresolved' else 'complete')
                self.assertEqual(item['result']['reply'], reply)
                self.assertEqual(item['result']['diff'], '')
                self.assertEqual(self.source(4)['sha256'], before['sha256'])

    def test_feedback_conflict_does_not_attribute_external_changes_to_agent(self):
        save_candidate(self.reader, 4, 'int original(void) { return 41; }')
        job = self.submit_suggestion()
        self.finish_suggestion(job, 'int proposed(void) { return 42; }', '建议修改为 42。',
                               concurrent_code='int external(void) { return 99; }')
        item = self.suggestions()['items'][0]
        self.assertEqual(item['status'], 'partial')
        self.assertEqual(item['result']['outcome'], 'conflict')
        self.assertEqual(item['result']['diff'], '')
        self.assertIn('保留当前文件', item['result']['reply'])
        self.assertIn('return 99', self.source(4)['display_code'])

    def test_legacy_feedback_shows_notes_without_inventing_a_published_diff(self):
        save_candidate(self.reader, 4, 'int actual(void) { return 41; }')
        job = self.submit_suggestion()
        job.update(status='complete', done_rvas=[4], processed=1)
        directory = Path(job['directory'])
        write_state(directory / 'state.json', job)
        write_state(directory / 'batch-0000.result.json', {'results': [{
            'rva': '0x4', 'code': 'int never_published(void) { return 99; }', 'notes': '旧任务的分析回复。'}]})
        result = self.suggestions()['items'][0]['result']
        self.assertEqual(result['outcome'], 'unrecorded')
        self.assertEqual(result['reply'], '旧任务的分析回复。')
        self.assertEqual(result['diff'], '')
        self.assertIsNone(result['after_sha256'])
        self.assertIn('return 41', self.source(4)['display_code'])
        write_state(directory / 'feedback-result.json', {'rva': 8})
        item = self.suggestions()['items'][0]
        self.assertIsNone(item['result'])
        self.assertIn('RVA 不匹配', item['result_error'])
        (directory / 'feedback-result.json').unlink()
        (directory / 'batch-0000.result.json').unlink()
        job.update(status='partial', failed_rvas={'0x4': 'CLI 超时，重试已耗尽'})
        write_state(directory / 'state.json', job)
        item = self.suggestions()['items'][0]
        self.assertEqual(item['error'], 'CLI 超时，重试已耗尽')
        self.assertIsNone(item['result'])

    def test_feedback_history_empty_unknown_address_and_limit(self):
        self.assertEqual(self.suggestions()['items'], [])
        self.assertEqual(self.suggestions()['progress']['state'], 'idle')
        self.assertEqual(self.client.get('/api/functions/no-address/feedback').status_code, 400)
        self.assertEqual(self.client.get('/api/functions/0xc/feedback').status_code, 404)
        self.assertEqual(self.client.get('/api/functions/0x4/feedback?limit=0').status_code, 422)

    def test_current_source_progress_tracks_real_batch_rvas_without_comments(self):
        with patch('recovery.queue._ensure_worker'), \
                patch('recovery.queue.shutil.which', return_value='/unused/codex'):
            response = self.client.post('/api/recovery')
        self.assertEqual(response.status_code, 200, response.text)
        job = response.json()
        self.assertEqual(set(job['rvas']), {4, 8})
        self.assertEqual(self.suggestions()['items'], [])
        self.assertEqual(self.suggestions()['progress']['state'], 'queued')
        state_path = Path(job['directory']) / 'state.json'
        job.update(status='running', phase='ghidra', started_at=job['created_at'], current=[])
        write_state(state_path, job)
        with (self.reader.directory / 'tasks/.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.suggestions()['progress']['state'], 'preparing')
            self.assertTrue(self.suggestions()['progress']['indeterminate'])
            job.update(phase='recovery', current=['0x8'])
            write_state(state_path, job)
            self.assertEqual(self.suggestions(4)['progress']['state'], 'waiting')
            self.assertEqual(self.suggestions(8)['progress']['state'], 'running')
            self.assertTrue(self.suggestions(8)['progress']['indeterminate'])
            job.update(done_rvas=[4], processed=1)
            write_state(state_path, job)
            completed = self.suggestions(4)['progress']
            self.assertEqual(completed['state'], 'complete')
            self.assertEqual(completed['value'], 1)
            self.assertFalse(completed['indeterminate'])
            self.assertEqual(self.suggestions(8)['progress']['state'], 'running')
            job.update(done_rvas=[4, 8], failed_rvas={'0x8': 'CLI 超时'}, processed=2)
            write_state(state_path, job)
            failed = self.suggestions(8)['progress']
            self.assertEqual(failed['state'], 'error')
            self.assertEqual(failed['error'], 'CLI 超时')

    def test_source_calls_bind_actual_declarations_and_utf16_ranges(self):
        save_candidate(self.reader, 8, 'int read_count(void) { return 82; }')
        body = '\r\n// 中文 😀，read_count() 不是调用\r\nint caller(void) { return read_count(); }\r\n'
        save_candidate(self.reader, 4, body)
        result = self.source(4)
        self.assertEqual(result['source_name'], 'caller')
        self.assertEqual(result['source_id'], 'f00000004')
        self.assertEqual(result['reference_offset_encoding'], 'utf-16')
        self.assertEqual(len(result['references']), 1)
        ref = result['references'][0]
        self.assertEqual(ref['target_rva'], 8)
        self.assertEqual(ref['target_source_id'], 'f00000008')
        displayed = result['display_code'].encode('utf-16-le')
        self.assertEqual(displayed[ref['start'] * 2:ref['end'] * 2].decode('utf-16-le'), 'read_count')
        self.assertGreater(ref['start'], result['display_code'].index('int caller'))
        self.assertEqual(self.source(ref['target_rva'])['source_name'], 'read_count')

    def test_call_target_survives_rename_move_and_rejects_stale_symbol(self):
        path = save_candidate(self.reader, 8, 'int alpha(void) { return 82; }', module='callee')
        save_candidate(self.reader, 4, 'int caller(void) { return alpha(); }', module='caller')
        first = self.source(4)
        stable_id = first['references'][0]['target_source_id']
        stat = path.stat()
        moved = path.with_name('moved.c')
        path.rename(moved)
        moved.write_text(moved.read_text().replace('alpha', 'bravo'))
        os.utime(moved, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        stale = self.source(4)
        self.assertEqual(stale['sha256'], first['sha256'])
        self.assertEqual(stale['references'], [])
        self.assertEqual(stale['unresolved_references'][0]['name'], 'alpha')
        save_candidate(self.reader, 4, 'int caller(void) { return bravo(); }')
        changed = self.source(4)
        self.assertEqual(changed['references'][0]['target_rva'], 8)
        self.assertEqual(changed['references'][0]['target_source_id'], stable_id)
        self.assertEqual(self.source(8)['path'], str(moved))

    def test_static_calls_use_file_scope_even_with_identical_names(self):
        with sqlite3.connect(self.reader.database) as conn:
            conn.executemany('INSERT INTO functions VALUES (?,?,0,?)',
                             [(12, 'third', '[]'), (16, 'fourth', '[]')])
        save_candidate(self.reader, 8, 'static int helper(void) { return 82; }', module='a')
        save_candidate(self.reader, 4, 'int first(void) { return helper(); }', module='a')
        save_candidate(self.reader, 12, 'static int helper(void) { return 123; }', module='b')
        save_candidate(self.reader, 16, 'int second(void) { return helper(); }', module='b')
        self.assertEqual(self.source(4)['references'][0]['target_rva'], 8)
        self.assertEqual(self.source(16)['references'][0]['target_rva'], 12)

    def test_callback_parameters_and_block_variables_do_not_jump_to_global_function(self):
        save_candidate(self.reader, 8, 'int helper(void) { return 82; }')
        save_candidate(self.reader, 4, 'int caller(int (*helper)(void)) { return helper(); }')
        result = self.source(4)
        self.assertEqual(result['references'], [])
        self.assertEqual(result['unresolved_references'][0]['reason'], 'shadowed')
        save_candidate(self.reader, 4, 'int caller(int helper(void)) { return helper(); }')
        result = self.source(4)
        self.assertEqual(result['references'], [])
        self.assertEqual(result['unresolved_references'][0]['reason'], 'shadowed')
        save_candidate(self.reader, 4, '''int caller(void) {
            int result = helper();
            { int (*helper)(void) = 0; result += helper(); }
            return result + helper();
        }''')
        result = self.source(4)
        self.assertEqual([ref['target_rva'] for ref in result['references']], [8, 8])
        self.assertEqual(len(result['unresolved_references']), 1)
        self.assertEqual(result['unresolved_references'][0]['reason'], 'shadowed')

    def test_macros_comments_and_literals_are_not_function_links(self):
        save_candidate(self.reader, 8, 'int helper(void) { return 82; }')
        save_candidate(self.reader, 4, '''int caller(void) {
            const char *s = "helper() <script>"; /* helper(); */
            return helper();
        }''')
        self.assertEqual(len(self.source(4)['references']), 1)
        save_candidate(self.reader, 4, '#define helper() 9\nint caller(void) { return helper(); }')
        result = self.source(4)
        self.assertEqual(result['references'], [])
        self.assertEqual(result['unresolved_references'][0]['reason'], 'macro')

    def test_ambiguous_global_definitions_and_address_like_names_do_not_guess_targets(self):
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute("INSERT INTO functions VALUES (12,'third',0,'[]')")
        save_candidate(self.reader, 8, 'int helper(void) { return 82; }', module='a')
        save_candidate(self.reader, 12, 'int helper(void) { return 123; }', module='b')
        save_candidate(self.reader, 4, '''int caller(void) {
            return helper() + fcn_00000008() + fcn_0000dead();
        }''', module='caller')
        result = self.source(4)
        self.assertEqual(result['references'], [])
        self.assertEqual(len(result['unresolved_references']), 3)
        self.assertEqual(result['unresolved_references'][0]['reason'], 'ambiguous_or_unmapped')

    def test_unique_native_symbol_can_link_before_source_exists(self):
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute("UPDATE functions SET name='missing_body' WHERE rva=8")
        save_candidate(self.reader, 4, 'int caller(void) { return missing_body(); }')
        result = self.source(4)
        self.assertEqual(result['references'][0]['target_rva'], 8)
        self.assertIsNone(result['references'][0]['target_source_id'])
        self.assertEqual(self.source(8)['status'], 'missing')
        # Equal original symbols are ambiguous even if one now has renamed C.
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute("UPDATE functions SET name='missing_body' WHERE rva=4")
        self.assertEqual(self.source(4)['references'], [])

    def test_assembly_links_decode_instruction_bytes_not_display_names_or_va(self):
        cases = [
            (4, 0x94000001, 8, 'call'),
            (8, 0x97ffffff, 4, 'call'),
            (4, 0x94000000, 4, 'call'),
            (4, 0x14000001, 8, 'tail_call'),
            (4, 0x14000000, None, None),
            (4, 0xd63f0100, None, None),
            (4, 0x94000010, None, None),
        ]
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute("INSERT INTO metadata VALUES ('base','1879048192')")
        for rva, word, target, kind in cases:
            with self.subTest(rva=rva, word=hex(word)):
                with sqlite3.connect(self.reader.database) as conn:
                    conn.execute('UPDATE assembly SET bytes=?,opcode=? WHERE function_rva=?',
                                 (word.to_bytes(4, 'little').hex(), 'bl fcn_deadbeef', rva))
                response = self.client.get(f'/api/functions/{rva:#x}/assembly')
                self.assertEqual(response.status_code, 200, response.text)
                instruction = response.json()['instructions'][0]
                self.assertEqual(instruction.get('target_rva'), target)
                self.assertEqual(instruction.get('reference_kind'), kind)
        with sqlite3.connect(self.reader.database) as conn:
            conn.execute("UPDATE functions SET ranges='[[4,12]]' WHERE rva=4")
            conn.execute("UPDATE assembly SET bytes='01000014' WHERE function_rva=4")
        instruction = self.client.get('/api/functions/4/assembly').json()['instructions'][0]
        self.assertNotIn('target_rva', instruction, 'An internal branch is not a callee')


if __name__ == '__main__':
    unittest.main()
