"""Durable workflow handoff with real queue files and no external tools."""

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lightre.main_workflow import start_workflow
from rehost.storage import read_json, write_json


class MainWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'input'
        self.source.mkdir()
        directory = self.root / 'analysis'
        directory.mkdir()
        self.reader = SimpleNamespace(directory=directory)
        write_json(self.root / 'target.json', {'source_dir': 'input'})
        self.worker = patch('recovery.queue._ensure_worker')
        self.worker.start()
        self.addCleanup(self.worker.stop)

    def recovery_job(self, status):
        directory = self.reader.directory / 'tasks/initial-job'
        directory.mkdir(parents=True, exist_ok=True)
        job = {'id': 'initial-job', 'directory': str(directory), 'status': status,
               'mode': 'initial', 'input_mode': 'reference', 'created_at': 1,
               'rvas': [8], 'done_rvas': [], 'current': [], 'active_batches': 0}
        write_json(directory / 'state.json', job)
        return job

    def wait_for_workflow(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = read_json(self.reader.directory / 'main-workflow.json')
            if state['status'] == 'rehost_finished':
                return state
            time.sleep(0.01)
        self.fail('workflow did not finish')

    def test_existing_recovery_is_resumed_without_creating_another_job(self):
        for status in ('interrupted', 'error', 'complete', 'partial'):
            with self.subTest(status=status):
                job = self.recovery_job(status)
                with patch('lightre.main_workflow.start_task') as create:
                    state = start_workflow(self.reader, start_rehost_task=False)
                create.assert_not_called()
                self.assertEqual(state['recovery']['id'], job['id'])
                expected = 'queued' if status in ('interrupted', 'error') else status
                self.assertEqual(read_json(Path(job['directory']) / 'state.json')['status'], expected)

    def test_rehost_waits_for_recovery_and_duplicate_start_keeps_one_coordinator(self):
        job = self.recovery_job('queued')
        with patch('lightre.main_workflow.continue_rehost', return_value={'status': 'observed'}) as rehost:
            try:
                first = start_workflow(self.reader)
                second = start_workflow(self.reader)
                self.assertEqual(first['recovery']['id'], second['recovery']['id'])
                rehost.assert_not_called()
            finally:
                write_json(Path(job['directory']) / 'state.json', {**job, 'status': 'complete'})
                state = self.wait_for_workflow()
        self.assertEqual(state['rehost']['status'], 'observed')
        rehost.assert_called_once()
        self.assertEqual(rehost.call_args.kwargs['materials'], [self.source])


if __name__ == '__main__':
    unittest.main()
