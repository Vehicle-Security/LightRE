"""Real cross-process registry/queue tests against a local fake CLI; no model."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from agents import AgentManager
from agents.codex import process_identity, read_events

PROJECT = Path(__file__).resolve().parents[1]
CLI = PROJECT / 'scripts/agent.py'
FAKE = r'''
import json, os, subprocess, sys, time, uuid
from pathlib import Path
args = sys.argv[1:]
prompt = sys.stdin.read()
data = json.loads(prompt)
thread = args[args.index('resume') + 1] if 'resume' in args else str(uuid.uuid4())
print(json.dumps({'type':'thread.started', 'thread_id':thread}), flush=True)
print(json.dumps({'type':'turn.started'}), flush=True)
print(json.dumps({'type':'fixture.input', 'prompt':prompt, 'cwd':os.getcwd(), 'argv':args}), flush=True)
if data.get('marker'):
    Path(data['marker']).write_text(thread)
if data.get('child'):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    print(json.dumps({'type':'fixture.child', 'pid':child.pid}), flush=True)
if data.get('gate'):
    while not Path(data['gate']).exists(): time.sleep(0.02)
time.sleep(data.get('delay', 0))
if data.get('mode') != 'missing':
    Path(args[args.index('-o') + 1]).write_text(data.get('reply', '完成。'), encoding='utf-8')
if data.get('mode') == 'failure':
    print('simulated CLI failure', file=sys.stderr)
    sys.exit(7)
if data.get('mode') == 'turn-failed':
    print(json.dumps({'type':'turn.failed','error':{'message':'simulated'}}), flush=True)
elif data.get('mode') != 'incomplete-events':
    print(json.dumps({'type':'turn.completed'}), flush=True)
'''


class AgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.cwd = self.directory / 'work 中文 $literal'
        self.cwd.mkdir()
        self.executable = self.directory / 'fake-codex'
        self.executable.write_text(f'#!{sys.executable}\n' + FAKE)
        self.executable.chmod(0o755)
        self.root = self.directory / 'agents'
        self.manager = AgentManager(self.root)
        self.addCleanup(self.cleanup_agents)

    def cleanup_agents(self):
        for session in self.manager.list():
            self.manager.get(session['id']).cancel()
        self.until(lambda: all(not session['worker_alive'] for session in self.manager.list()))

    def until(self, condition, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.025)
        self.fail('Timed out waiting for the local fixture')

    def session(self, **options):
        return self.manager.create(self.cwd, executable=self.executable, **options)

    def send(self, session, **message):
        return session.send_message(json.dumps(message, ensure_ascii=False))

    def cli(self, *args, input=None, expected=0):
        process = subprocess.run([sys.executable, str(CLI), '--root', str(self.root), *map(str, args)],
                                 input=input, text=True, capture_output=True, timeout=8)
        self.assertEqual(process.returncode, expected, process.stdout + process.stderr)
        return json.loads(process.stdout if expected != 2 else process.stderr)

    def test_python_can_reattach_using_local_session_run_and_codex_thread_ids(self):
        session = self.session()
        self.assertEqual(session.snapshot()['status'], 'idle')
        self.assertEqual(session.snapshot()['runs'], [])
        run = self.send(session, reply='中文最终回复。')
        result = run.wait(6)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['text'], '中文最终回复。')
        other = AgentManager(self.root)
        self.assertEqual(other.get(session.id).get_run().id, run.id)
        self.assertEqual(other.get_run(run.id).result()['text'], result['text'])
        self.assertEqual(other.get(result['thread_id']).id, session.id)
        self.assertNotEqual(session.id, result['thread_id'])
        self.assertEqual(result['exit_code'], 0)
        for name in ('prompt.md', 'request.json', 'execution.json', 'result.txt', 'events.jsonl', 'stderr.log'):
            self.assertTrue((run.directory / name).exists(), name)

    def test_another_cli_process_can_queue_message_and_read_result_and_events(self):
        created = self.cli('create', '--cwd', self.cwd, '--codex', self.executable)
        session_id = created['id']
        marker, gate = self.directory / 'started', self.directory / 'gate'
        first = self.cli('send', '--session', session_id, '--message-file', '-',
                         input=json.dumps({'marker': str(marker), 'gate': str(gate), 'reply': '第一轮'}))
        self.until(marker.exists)
        live = self.cli('run', '--session', session_id)
        self.assertEqual(live['id'], first['id'])
        self.assertEqual(live['status'], 'running')
        prompt = json.dumps({'reply': '第二轮\n`literal` $(literal)'}, ensure_ascii=False)
        second = self.cli('send', '--session', session_id, '--message', prompt, '--request-id', 'comment-2')
        self.assertEqual(second['status'], 'queued')
        state = self.cli('status', '--session', session_id)
        self.assertEqual(state['queued'], 1)
        gate.touch()
        result = self.cli('wait', '--session', session_id, '--run', second['id'], '--timeout', 6)
        self.assertEqual(result['text'], '第二轮\n`literal` $(literal)')
        self.assertEqual(result['thread_id'], marker.read_text())
        page = self.cli('events', '--session', session_id, '--run', second['id'])
        event = next(item for item in page['events'] if item['type'] == 'fixture.input')
        self.assertEqual(event['prompt'], prompt)
        self.assertEqual(event['cwd'], str(self.cwd))
        self.assertEqual(event['argv'][event['argv'].index('resume') + 1], result['thread_id'])
        self.assertNotIn('--last', event['argv'])
        self.assertNotIn('--ephemeral', event['argv'])
        self.assertEqual(self.cli('result', '--session', session_id, '--run', second['id'])['text'], result['text'])
        duplicate = self.manager.get(session_id).send_message(prompt, timeout=600.0, request_id='comment-2')
        self.assertEqual(duplicate.id, second['id'], 'Python and CLI must normalize equivalent options identically')

    def test_session_serialization_independent_sessions_and_idempotent_submission(self):
        first, second = self.session(), self.session()
        gate = self.directory / 'gate'
        markers = [self.directory / f'marker-{index}' for index in range(2)]
        runs = [self.send(session, marker=str(marker), gate=str(gate))
                for session, marker in zip((first, second), markers)]
        self.until(lambda: all(marker.exists() for marker in markers))
        prompt = json.dumps({'reply': 'one request'})
        with ThreadPoolExecutor(max_workers=4) as pool:
            submitted = list(pool.map(lambda _: first.send_message(prompt, request_id='unique').id, range(4)))
        self.assertEqual(len(set(submitted)), 1)
        pending = self.manager.get_run(submitted[0])
        self.assertEqual(pending.poll()['status'], 'queued')
        self.assertEqual(sum(row['status'] == 'running' for row in first.snapshot()['runs']), 1)
        with self.assertRaisesRegex(ValueError, 'request_id'):
            first.send_message('different', request_id='unique')
        gate.touch()
        self.assertTrue(all(run.wait(6)['status'] == 'completed' for run in [*runs, pending]))

    def test_failure_pauses_queued_messages_and_explicit_resume_does_not_retry_failed_run(self):
        session = self.session()
        gate, marker = self.directory / 'gate', self.directory / 'started'
        first = self.send(session, marker=str(marker), gate=str(gate), mode='failure', reply='partial output')
        self.until(marker.exists)
        second = self.send(session, reply='after review')
        gate.touch()
        failed = first.wait(6)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['text'], 'partial output')
        self.assertEqual(failed['exit_code'], 7)
        self.until(lambda: not session.snapshot()['worker_alive'])
        self.assertEqual(session.snapshot()['status'], 'paused')
        self.assertEqual(second.poll()['status'], 'queued')
        with self.assertRaises(TimeoutError):
            second.wait(0.03, interval=0.01)
        self.assertEqual(second.poll()['status'], 'queued')
        session.resume()
        self.assertEqual(second.wait(6)['status'], 'completed')
        self.assertEqual(first.poll()['status'], 'failed')
        self.assertEqual(len(session.snapshot()['runs']), 2)

    def test_timeout_cancel_pending_and_cancel_session(self):
        session = self.session()
        timed = session.send_message(json.dumps({'delay': 5}), timeout=0.15)
        self.assertEqual(timed.wait(6)['status'], 'timeout')
        self.assertTrue(session.snapshot()['paused'])
        marker, gate = self.directory / 'started', self.directory / 'gate'
        active = self.send(session, marker=str(marker), gate=str(gate))
        pending = self.send(session, reply='should not execute')
        pending.cancel()
        self.assertEqual(pending.result()['status'], 'cancelled')
        self.assertFalse((pending.directory / 'events.jsonl').exists())
        session.resume()
        self.until(marker.exists)
        session.cancel()
        self.assertEqual(active.wait(6)['status'], 'cancelled')
        self.assertTrue(session.snapshot()['paused'])

    def test_successful_exit_still_requires_final_output_and_valid_requested_json(self):
        for data in ({'mode': 'missing'}, {'reply': ''}, {'mode': 'turn-failed'},
                     {'mode': 'incomplete-events', 'reply': '{}'}, {'reply': 'not JSON'}):
            with self.subTest(data=data):
                session = self.session()
                run = session.send_message(json.dumps(data), output_schema={'type': 'object'})
                result = run.wait(6)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['exit_code'], 0)
                self.assertTrue(result['error'])
        session = self.session()
        run = session.send_message(json.dumps({'reply': '{"answer":42}'}), output_schema={'type': 'object'})
        self.assertEqual(run.wait(6)['parsed'], {'answer': 42})

    def test_ephemeral_turns_are_independent_and_live_messages_are_explicitly_unsupported(self):
        session = self.session(persistent=False)
        first = self.send(session).wait(6)
        second = self.send(session).wait(6)
        self.assertNotEqual(first['thread_id'], second['thread_id'])
        self.assertIsNone(session.snapshot()['thread_id'])
        self.assertFalse(session.snapshot()['capabilities']['conversation_context'])
        with self.assertRaisesRegex(NotImplementedError, 'live steering'):
            session.send_message('now', delivery='immediate')
        error = self.cli('send', '--session', session.id, '--message', 'now', '--delivery', 'immediate', expected=2)
        self.assertEqual(error['type'], 'NotImplementedError')

    def test_worker_death_is_not_completion_and_no_live_writer_is_duplicated(self):
        session = self.session()
        marker, gate = self.directory / 'started', self.directory / 'gate'
        run = self.send(session, marker=str(marker), gate=str(gate))
        self.until(marker.exists)
        owner = session.snapshot()['worker_pid']
        os.kill(owner, signal.SIGKILL)
        self.until(lambda: process_identity(owner) is None)
        queued = self.send(session, reply='must wait for review')
        self.assertEqual(run.poll()['status'], 'running', 'CLI still holds the writer lease')
        self.assertEqual(queued.poll()['status'], 'queued')
        run.cancel()  # A different process can stop the known CLI after its worker died.
        self.until(lambda: run.poll()['terminal'])
        self.assertEqual(run.result()['status'], 'cancelled')
        self.assertTrue(session.snapshot()['paused'])
        self.assertEqual(queued.poll()['status'], 'queued')

    def test_worker_crash_never_turns_an_unrecorded_final_file_into_success(self):
        session = self.session()
        marker, gate = self.directory / 'started', self.directory / 'gate'
        run = self.send(session, marker=str(marker), gate=str(gate), reply='unrecorded final')
        self.until(marker.exists)
        owner = session.snapshot()['worker_pid']
        os.kill(owner, signal.SIGKILL)
        self.until(lambda: process_identity(owner) is None)
        queued = self.send(session, reply='must not start')
        gate.touch()
        self.until(lambda: run.poll()['terminal'])
        self.assertEqual(run.result()['status'], 'interrupted')
        self.assertEqual(run.result()['text'], 'unrecorded final')
        self.assertTrue(session.snapshot()['paused'])
        self.assertEqual(queued.poll()['status'], 'queued')

    def test_descendants_are_stopped_before_completed_run_is_visible(self):
        session = self.session()
        run = self.send(session, child=True)
        self.assertEqual(run.wait(6)['status'], 'completed')
        child = next(event['pid'] for event in run.read_events()['events'] if event['type'] == 'fixture.child')
        self.assertIsNone(process_identity(child))

    def test_event_cursor_preserves_partial_lines_and_reports_invalid_events(self):
        path = self.directory / 'events.jsonl'
        first = json.dumps({'type': 'first', 'text': '中文'}, ensure_ascii=False).encode() + b'\n'
        path.write_bytes(first + b'{"type":')
        page = read_events(path)
        self.assertEqual(page['cursor'], len(first))
        self.assertEqual(len(page['events']), 1)
        with path.open('ab') as stream:
            stream.write(b'"second"}\ninvalid\n')
        page = read_events(path, page['cursor'])
        self.assertEqual(page['events'], [{'type': 'second'}])
        self.assertEqual(len(page['errors']), 1)
        self.assertEqual(page['cursor'], path.stat().st_size)

    def test_spawn_failure_preserves_queued_request_for_explicit_resume(self):
        session = self.session()
        with patch('agents.manager.subprocess.Popen', side_effect=OSError('cannot spawn')):
            run = self.send(session, reply='saved')
        self.assertEqual(run.poll()['status'], 'queued')
        self.assertIn('cannot spawn', session.snapshot()['pause_reason'])
        self.assertEqual(session.resume().get_run(run.id).wait(6)['text'], 'saved')

    def test_pause_finishes_active_run_but_holds_the_next_message(self):
        session = self.session()
        marker, gate = self.directory / 'started', self.directory / 'gate'
        first = self.send(session, marker=str(marker), gate=str(gate))
        self.until(marker.exists)
        self.cli('pause', '--session', session.id)
        second = self.send(session)
        gate.touch()
        self.assertEqual(first.wait(6)['status'], 'completed')
        self.until(lambda: not session.snapshot()['worker_alive'])
        self.assertEqual(second.poll()['status'], 'queued')
        self.cli('resume', '--session', session.id)
        self.assertEqual(second.wait(6)['status'], 'completed')

    def test_known_thread_ids_are_required_and_run_lookup_cannot_cross_sessions(self):
        session, other = self.session(), self.session()
        run = self.send(session)
        with self.assertRaises(KeyError):
            other.get_run(run.id)
        with self.assertRaises(KeyError):
            self.manager.get('../unknown')
        with self.assertRaises(KeyError):
            self.manager.get_run('unknown')
        self.assertEqual(run.wait(6)['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
