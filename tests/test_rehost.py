"""Workflow orchestration with real files/child processes, without a model or emulator."""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rehost.runner import run
from rehost.storage import digest, read_json, write_json
from rehost import start, resume
from rehost.workflow import continue_analysis


class RehostTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.directory = self.root / 'unknown-so'
        (self.directory / 'binary').mkdir(parents=True)
        self.binary = self.directory / 'binary/original.so'
        self.binary.write_bytes(b'\x7fELF\x02\x01' + b'\0' * 10 + b'\x03\x00\xb7\x00')
        with sqlite3.connect(self.directory / 'index.sqlite') as conn:
            conn.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)')
            conn.execute('INSERT INTO metadata VALUES (?,?)', ('sha256', digest(self.binary)))
        self.seed = self.root / 'unknown-seed.bin'
        self.seed.write_bytes(b'non-special-cased bytes')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.jar = self.root / 'runtime.jar'
        self.jar.write_bytes(b'runtime fixture')
        self.tool('readelf', 'print("arbitrary target headers, symbols and dependencies")')
        self.tool('mvn', f'''
from pathlib import Path
if '-version' not in sys.argv:
    assert Path('src/main/java/lightre/RehostHarness.java').is_file()
    Path('classpath.txt').write_text({str(self.jar)!r})
''')
        self.tool('java', '''
import json
from pathlib import Path
if '-version' not in sys.argv:
    assert 'lightre.RehostHarness' in sys.argv
    source=Path('src/main/java/lightre/RehostHarness.java').read_text()
    if 'MISSING_ENV' in source:
        raise RuntimeError('unimplemented environment: example/Unknown->getValue()I at module+0x24')
    request=Path(sys.argv[-2]); params=json.loads(request.read_text())
    data=(request.parent/params['input_file']).read_bytes() if 'input_file' in params else b''
    Path(sys.argv[-1]).write_text(json.dumps({'entry':'investigated unknown entry',
        'status':'returned','native_calls':1,'missing_environment':[], 'result':{'size':len(data)}}))
''')
        self.tool('codex', '''
from pathlib import Path
prompt=sys.stdin.read()
assert '只读 workflow 目录：' in prompt
assert '从零实现最小 unidbg harness' in prompt
assert not Path('src/main/java/lightre/DgHarness.java').exists()
source=Path('src/main/java/lightre/RehostHarness.java')
if source.exists():
    assert '上轮完整返回：' in prompt
    assert '上轮检查：' in prompt
    source.write_text('FIXED_ENV')
else:
    # No target Java implementation was copied or generated before this exec.
    assert not list(Path('.').rglob('*.java'))
    source.parent.mkdir(parents=True)
    source.write_text('MISSING_ENV')
guide=Path('HARNESS.md')
guide.write_text(guide.read_text()+'\\n调查与适配记录：动态补齐具体环境，等待外部重跑。\\n')
Path(sys.argv[sys.argv.index('-o')+1]).write_text('完整本轮报告：声称完成，仍须实际运行。\\n')
print('{"type":"turn.completed"}')
''')
        self.enterContext(patch.dict(os.environ, {
            'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
            'LIGHTRE_CODEX_BIN': str(self.bin / 'codex'),
        }))

    def tool(self, name, body):
        path = self.bin / name
        path.write_text(f'#!{sys.executable}\nimport sys\n' + body)
        path.chmod(0o755)

    def start(self, **kwargs):
        return start(self.directory, description='调查真实入口并运行给定未知输入',
                     inputs=[self.seed], max_rounds=2, **kwargs)

    def test_automatic_start_uses_rehosts_own_default_task_and_continues_it(self):
        state = continue_analysis(self.directory, materials=[self.seed], max_rounds=1)
        self.assertEqual(state['status'], 'limit', state)
        config = read_json(self.directory / 'harness.json')
        body = Path(config['task_description']).read_text(encoding='utf-8')
        self.assertIn('调查目标 ARM64 SO，生成可真实运行的 unidbg harness', body)
        continued = continue_analysis(self.directory, max_rounds=1)
        self.assertEqual(continued['id'], state['id'])
        self.assertEqual(continued['status'], 'observed', continued)

    def test_empty_project_generation_environment_feedback_and_observation_replay(self):
        state = self.start()
        self.assertEqual(state['status'], 'observed', state)
        self.assertEqual(state['round'], 2)
        task = Path(state['directory'])
        report = read_json(state['latest_verification'])
        self.assertFalse(report['passed'])
        first = read_json(task / 'round-0001/verification.json')
        self.assertEqual(first['status'], 'execution_failed', first)
        failed_run = Path(first['runs'][0]['run'])
        self.assertIn('example/Unknown->getValue', (failed_run / 'execute/stderr.log').read_text())
        self.assertIn(str(task / 'round-0001/result.md'), (task / 'round-0002/prompt.md').read_text())
        saved = Path(report['runs'][0]['run'])
        self.seed.unlink()
        replay = run(self.directory, replay=saved)
        self.assertEqual(replay['status'], 'observed', replay)
        self.assertFalse(replay['passed'])
        self.assertEqual(replay['observation']['result'], {'size': 23})

    def test_fresh_start_preserves_prior_project_and_resume_only_continues_same_task(self):
        first = start(self.directory, description='调查入口', inputs=[self.seed], max_rounds=1)
        self.assertEqual(first['status'], 'limit', first)
        config = read_json(self.directory / 'harness.json')
        harness = Path(config['path'])
        state = resume(self.directory, first['directory'], max_rounds=1)
        self.assertEqual(state['status'], 'observed', state)
        self.assertEqual(read_json(self.directory / 'harness.json'), config)
        before = (harness / 'src/main/java/lightre/RehostHarness.java').read_bytes()
        again = self.start()
        self.assertEqual(again['status'], 'observed', again)
        self.assertNotEqual(read_json(self.directory / 'harness.json')['path'], str(harness))
        self.assertEqual((harness / 'src/main/java/lightre/RehostHarness.java').read_bytes(), before)
        archives = list((self.directory / 'rehost').glob('*/previous-association.json'))
        self.assertTrue(any(read_json(path) == config for path in archives))

    def test_service_restart_continues_same_task_and_keeps_completed_work(self):
        first = start(self.directory, description='调查入口', inputs=[self.seed], max_rounds=1)
        self.assertEqual(first['status'], 'limit', first)
        config = read_json(self.directory / 'harness.json')
        continued = continue_analysis(self.directory, description='继续分析', max_rounds=1)
        self.assertEqual(continued['status'], 'observed', continued)
        self.assertEqual(continued['id'], first['id'])
        self.assertEqual(continued['round'], 2)
        self.assertEqual(read_json(self.directory / 'harness.json'), config)
        with patch('rehost.workflow.start', side_effect=AssertionError('must reuse')):
            again = continue_analysis(self.directory, description='继续分析')
        self.assertEqual(again['id'], first['id'])
        self.assertEqual(again['round'], 2)
        self.assertEqual(len(list((self.directory / 'tasks').glob('harness-*'))), 1)

    def test_restart_after_association_publication_reuses_bootstrap_project(self):
        with patch('rehost.workflow.repair', side_effect=RuntimeError('process exited before task creation')):
            with self.assertRaisesRegex(RuntimeError, 'process exited'):
                self.start()
        config = read_json(self.directory / 'harness.json')
        continued = continue_analysis(self.directory, description='继续分析', max_rounds=2)
        self.assertEqual(continued['status'], 'observed', continued)
        self.assertEqual(read_json(self.directory / 'harness.json'), config)
        self.assertEqual(len(list((self.directory.parent / 'harnesses').iterdir())), 1)

    def test_independent_fixed_expected_results_can_complete_but_cannot_be_invented(self):
        cases = self.root / 'fixed.json'
        write_json(cases, [{'id': 'known', 'input_sha256': digest(self.seed),
                            'request': {'input_file': self.seed.name}, 'expected': {'result': {'size': 23}}}])
        state = start(self.directory, description='运行固定输入并检查已知输出', cases_file=cases, max_rounds=2)
        self.assertEqual(state['status'], 'complete', state)
        self.assertTrue(read_json(state['latest_verification'])['passed'])
        self.assertEqual(read_json(cases)[0]['expected'], {'result': {'size': 23}})

    def test_no_seed_uses_explicit_entry_investigation_and_does_not_claim_validation(self):
        state = start(self.directory, description='调查入口并记录自然执行观察', max_rounds=2)
        self.assertEqual(state['status'], 'observed', state)
        task = Path(state['directory'])
        self.assertEqual(read_json(task / 'contract.json')['cases'][0]['request'], {})

    def test_incomplete_observations_and_altered_seeds_are_not_accepted(self):
        self.start()
        self.tool('java', '''
from pathlib import Path
if '-version' not in sys.argv:
    Path(sys.argv[-1]).write_text('{"status":"returned","entry":"loader only","native_calls":0,"missing_environment":[]}')
''')
        report = run(self.directory)
        self.assertEqual(report['status'], 'mismatch', report)
        self.assertIn('No native calls reported', report['differences'])
        self.seed.write_bytes(b'modified seed')
        failed = run(self.directory)
        self.assertFalse(failed['passed'])
        self.assertIn('fixed task seeds', failed['error'])


if __name__ == '__main__':
    unittest.main()
