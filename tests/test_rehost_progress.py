"""Live execution evidence and Rehost-only retry through the service API."""

import fcntl
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from lightre.api import create_app
from lightre.server import PendingWorkspace
from rehost.progress import progress
from rehost.storage import read_json, write_json


class RehostProgressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task = self.root / 'tasks/harness-test'
        self.task.mkdir(parents=True)
        self.check = self.task / 'round-0003'
        self.check.mkdir()
        self.run = self.root / 'runs/run-1'
        (self.run / 'build').mkdir(parents=True)
        (self.run / 'execute').mkdir()
        self.state = {'id': 'harness-test', 'mode': 'harness', 'status': 'running',
                      'round': 3, 'created_at': 1, 'phase': 'checking',
                      'latest_round': str(self.check), 'latest_verification': str(self.check / 'verification.json')}
        write_json(self.task / 'state.json', self.state)
        write_json(self.root / 'main-workflow.json', {'rehost': {'status': 'running'}})
        write_json(self.check / 'verification.json', {'status': 'checking', 'runs': [],
                   'current_run': str(self.run), 'total_cases': 2, 'current_case': 'entry'})
        write_json(self.run / 'verification.json', {'run': str(self.run), 'status': 'running', 'phase': 'building'})
        self.lock = self.enterContext((self.root / 'tasks/.lock').open('a+'))
        fcntl.flock(self.lock, fcntl.LOCK_EX)

    def test_status_reads_live_build_and_execution_without_coordinator_updates(self):
        pending = PendingWorkspace()
        pending.reader = SimpleNamespace(directory=self.root)
        app = create_app(pending, include_ui=False, status=pending.status)
        with TestClient(app) as client:
            write_json(self.run / 'build/execution.json', {'status': 'running', 'exit_code': None})
            first = client.get('/api/status').json()['workflow']['rehost']
            self.assertEqual((first['status'], first['round'], first['phase']), ('running', 3, 'building'))
            self.assertFalse(first['can_retry'])
            write_json(self.run / 'build/execution.json', {'status': 'exited', 'exit_code': 0})
            write_json(self.run / 'verification.json', {'run': str(self.run), 'status': 'running', 'phase': 'executing'})
            write_json(self.run / 'execute/execution.json', {'status': 'running', 'exit_code': None})
            (self.run / 'execute/stderr.log').write_text('unidbg: missing JNI environment')
            second = client.get('/api/rehost').json()
            self.assertEqual(second['phase'], 'executing')
            self.assertEqual(second['run']['build']['exit_code'], 0)
            self.assertIn('missing JNI', second['run']['execution']['stderr'])
            self.assertIsNone(second['run']['native_returned'])
            self.assertEqual(client.get('/api/rehost').headers['cache-control'], 'no-store')

    def test_observation_distinguishes_native_return_from_independent_validation(self):
        write_json(self.task / 'state.json', {**self.state, 'status': 'observed'})
        write_json(self.run / 'verification.json', {'run': str(self.run), 'status': 'observed', 'observed': True,
                   'passed': False, 'phase': 'finished', 'observation': {'entry': 'JNI entry', 'status': 'returned',
                   'native_calls': 2, 'missing_environment': []}})
        write_json(self.check / 'verification.json', {'status': 'observed', 'total_cases': 1,
                   'current_run': str(self.run), 'runs': [{'run': str(self.run)}]})
        result = progress(self.root)
        self.assertTrue(result['run']['native_returned'])
        self.assertFalse(result['run']['passed'])
        self.assertTrue(result['can_retry'])
        self.assertEqual(result['completed_cases'], 1)
        write_json(self.run / 'observation.json', {'status': 'returned', 'native_calls': 0})
        write_json(self.run / 'verification.json', {'run': str(self.run), 'status': 'mismatch'})
        self.assertFalse(progress(self.root)['run']['native_returned'])

    def test_logs_are_bounded_and_paths_cannot_escape_workspace(self):
        (self.run / 'execute/stderr.log').write_text('old\n' * 10000 + 'last unidbg error')
        result = progress(self.root)
        self.assertTrue(result['run']['execution']['stderr'].endswith('last unidbg error'))
        self.assertLess(len(result['run']['execution']['stderr']), 9000)
        (self.task / 'outside.log').write_text('visible only inside workspace')
        write_json(self.check / 'verification.json', {'current_run': str(self.root.parent)})
        self.assertIsNone(progress(self.root)['run'])

    def test_new_retry_does_not_display_previous_terminal_state(self):
        write_json(self.task / 'state.json', {**self.state, 'status': 'observed', 'run_started_at': 1})
        result = progress(self.root, {'status': 'running', 'requested_at': time.time()})
        self.assertEqual(result['status'], 'running')
        self.assertEqual(result['phase'], 'starting')
        self.assertFalse(result['can_retry'])

    def test_abandoned_running_task_is_resumable(self):
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        result = progress(self.root)
        self.assertEqual(result['status'], 'interrupted')
        self.assertTrue(result['can_retry'])

    def test_retry_is_exclusive_and_does_not_submit_or_resume_recovery(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def execute(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test did not release Rehost')
            return {'status': 'observed'}
        app = create_app(SimpleNamespace(directory=self.root), include_ui=False)
        with patch('lightre.main_workflow.continue_rehost', side_effect=execute) as rehost, \
                patch('lightre.main_workflow.resume_recovery') as resume, \
                patch('lightre.main_workflow.start_task') as recovery:
            with TestClient(app) as client:
                try:
                    self.assertEqual(client.post('/api/rehost/retry').status_code, 202)
                    self.assertTrue(entered.wait(2))
                    self.assertEqual(client.post('/api/rehost/retry').status_code, 409)
                    self.assertTrue(rehost.call_args.kwargs['retry'])
                    resume.assert_not_called()
                    recovery.assert_not_called()
                finally:
                    release.set()
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        if read_json(self.root / 'main-workflow.json').get('status') == 'rehost_finished':
                            finished.set()
                            break
                        time.sleep(.01)
        self.assertTrue(finished.is_set())


if __name__ == '__main__':
    unittest.main()
