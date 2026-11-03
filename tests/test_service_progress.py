"""The service reports preparation before a workspace reader is available."""

import fcntl
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from lightre.api import create_app
from lightre.server import PendingWorkspace, main
from rehost.storage import write_json


class ServiceProgressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pending = PendingWorkspace()

    def test_status_and_page_are_available_without_a_catalog(self):
        app = create_app(self.pending, start_worker=False, status=self.pending.status)
        with TestClient(app) as client:
            self.assertEqual(client.get('/').status_code, 200)
            for phase in ('starting', 'exploring', 'static_analysis'):
                self.pending.update(phase=phase, message=phase)
                response = client.get('/api/status')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertFalse(response.json()['ready'])
                self.assertEqual(response.json()['phase'], phase)
                self.assertGreaterEqual(response.json()['phase_elapsed_seconds'], 0)
            self.assertEqual(client.get('/api/functions').status_code, 503)

    def test_log_tail_is_bounded_and_survives_missing_log(self):
        log = self.root / 'headless.log'
        self.pending.update(phase='static_analysis', log_path=str(log))
        self.assertEqual(self.pending.status()['log_tail'], '')
        log.write_text('旧日志\n' * 10000 + '\n'.join(f'函数 {i}/100' for i in range(20)), encoding='utf-8')
        tail = self.pending.status()['log_tail']
        self.assertEqual(tail.splitlines(), [f'函数 {i}/100' for i in range(8, 20)])
        self.assertLess(len(tail.encode('utf-8')), 8192)

    def test_workflow_status_is_read_from_durable_updates(self):
        self.pending.reader = SimpleNamespace(directory=self.root)
        self.pending.update(phase='recovery', message='Recovery 已启动')
        app = create_app(self.pending, include_ui=False, status=self.pending.status)
        with TestClient(app) as client:
            for state in ('waiting_for_recovery', 'running', 'observed', 'error'):
                write_json(self.root / 'main-workflow.json', {'rehost': {'status': state}})
                response = client.get('/api/status')
                self.assertTrue(response.json()['ready'])
                self.assertEqual(response.json()['workflow']['rehost']['status'], state)

    def test_web_reports_static_analysis_then_starts_recovery_without_a_request(self):
        exporting, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        reader = SimpleNamespace(directory=self.root, catalog=lambda: {'functions': []})
        log = self.root / 'headless.log'

        def prepare(target, *, workspace, on_progress):
            log.write_text('Ghidra 正在分析 ARM64\n', encoding='utf-8')
            on_progress(phase='static_analysis', message='Ghidra 分析中',
                        directory=str(self.root), log_path=str(log))
            exporting.set()
            if not release.wait(5):
                raise RuntimeError('test did not release export')
            return reader

        def serve(app, **kwargs):
            self.assertTrue(exporting.wait(5))
            with TestClient(app) as client:
                self.assertEqual(client.get('/').status_code, 200)
                response = client.get('/api/status')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['phase'], 'static_analysis')
                self.assertIn('正在分析 ARM64', response.json()['log_tail'])
                workflow.assert_not_called()
                release.set()
                self.assertTrue(app.state.analysis_finished.wait(5))
                workflow.assert_called_once_with(reader)
                self.assertTrue(client.get('/api/status').json()['ready'])
                self.assertEqual(client.get('/api/functions').status_code, 200)

        with patch('lightre.server.ensure'), \
                patch('lightre.server.prepare_analysis', side_effect=prepare), \
                patch('lightre.server.start_workflow') as workflow, \
                patch('lightre.server.uvicorn.run', side_effect=serve):
            main([str(self.root)])

    def test_startup_and_workflow_errors_are_reported_through_status(self):
        for prepared in (False, True):
            with self.subTest(prepared=prepared):
                reader = SimpleNamespace(directory=self.root)

                def serve(app, **kwargs):
                    self.assertTrue(app.state.analysis_finished.wait(5))
                    with TestClient(app) as client:
                        response = client.get('/api/status')
                        self.assertEqual(response.status_code, 200)
                        data = response.json()
                        self.assertEqual(data['status'], 'error')
                        self.assertEqual(data['ready'], prepared)
                        self.assertEqual(data['error'], '工具执行失败')

                with patch('lightre.server.ensure'), \
                        patch('lightre.server.prepare_analysis', return_value=reader,
                              side_effect=None if prepared else RuntimeError('工具执行失败')), \
                        patch('lightre.server.start_workflow', side_effect=RuntimeError('工具执行失败')), \
                        patch('lightre.server.uvicorn.run', side_effect=serve):
                    main([str(self.root)])

    def test_duplicate_service_does_not_start_another_workflow(self):
        reader = SimpleNamespace(directory=self.root)

        def serve(app, **kwargs):
            self.assertTrue(app.state.analysis_finished.wait(5))
            self.assertIsInstance(app.state.startup_error, RuntimeError)
            with TestClient(app) as client:
                status = client.get('/api/status').json()
                self.assertEqual(status['status'], 'error')
                self.assertIn('服务已运行', status['error'])

        with (self.root / '.service.lock').open('a+') as owner, \
                patch('lightre.server.ensure'), \
                patch('lightre.server.prepare_analysis', return_value=reader), \
                patch('lightre.server.start_workflow') as workflow, \
                patch('lightre.server.uvicorn.run', side_effect=serve):
            fcntl.flock(owner, fcntl.LOCK_EX)
            main([str(self.root)])
            workflow.assert_not_called()


if __name__ == '__main__':
    unittest.main()
