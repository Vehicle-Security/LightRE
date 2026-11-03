"""Internal catalogue routing, immutable inputs and optional real CLI checks."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lightre.prompt import render_prompt
from lightre.workspace import RESULT_SCHEMA, WorkspaceReader
from recovery import read_candidate, read_knowledge, save_candidate, save_knowledge
from recovery.jobs import _publish_batch, _start_batch, _terminate
from recovery.skills import SKILLS, skill_catalog


class SkillFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lightre-skill-')
        self.addCleanup(self.temp.cleanup)
        # Paths are data, including spaces, quotes and literal template syntax.
        self.directory = Path(self.temp.name) / "分析 '空 格{{TASK}}"
        self.directory.mkdir()
        with sqlite3.connect(self.directory / 'index.sqlite') as conn:
            conn.executescript('''
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE functions (rva INTEGER PRIMARY KEY, name TEXT,
                                        instructions INTEGER, ranges TEXT);
                CREATE TABLE assembly (function_rva INTEGER, ordinal INTEGER,
                                       rva INTEGER, bytes TEXT, opcode TEXT);
                INSERT INTO functions VALUES (4,'test',2,'[[4,12]]');
                INSERT INTO assembly VALUES (4,0,4,'001c0091','add x0, x0, #7');
                INSERT INTO assembly VALUES (4,1,8,'c0035fd6','ret');
            ''')
            conn.executemany('INSERT INTO metadata VALUES (?,?)', {
                'name': 'test', 'sha256': 'a' * 64, 'source_path': '/inputs/test.so',
                'state': 'ready', 'schema_version': RESULT_SCHEMA,
            }.items())
        self.reader = WorkspaceReader(self.directory)
        save_candidate(self.reader, 4, 'long fcn_00000004(long value) { return value + 7; }')
        save_knowledge(self.reader, 4, '入口参数 value 位于 x0，返回 value + 7；尚未执行验证。')

    def task(self, mode, input_mode='reference', comment=''):
        directory = self.directory / 'tasks' / (mode + '-' + input_mode)
        directory.mkdir(parents=True)
        state = {'id': directory.name, 'directory': str(directory), 'codex': 'codex',
                 'mode': mode, 'input_mode': input_mode, 'comment': comment,
                 'generated': 0, 'processed': 0, 'done_rvas': [], 'unresolved': {},
                 'expected_sha256': read_candidate(self.reader, 4)['sha256']}
        (directory / 'task.md').write_text(render_prompt(
            Path(SKILLS).parent / 'prompts/TASK.md', DIRECTORY=self.directory,
            RVAS='0x4', INPUT_MODE=input_mode, COMMENT=comment,
            EXPECTED_SHA256=state['expected_sha256']), encoding='utf-8')
        batch = [{'rva': 4, 'instructions': 2, 'input_mode': input_mode}]
        return state, batch

    def assert_snapshot(self, prefix):
        manifest = json.loads(prefix.with_suffix('.skills.json').read_text())
        snapshot = prefix.with_suffix('.skills')
        self.assertEqual(manifest['version'], 1)
        for item in manifest['skills']:
            entry = Path(item['path'])
            self.assertTrue(entry.is_absolute())
            self.assertEqual(entry, snapshot / item['name'] / 'SKILL.md')
            for file in item['files']:
                path = snapshot / file['path']
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), file['sha256'])
                for link in re.findall(r'\]\(([^)]+)\)', path.read_text()):
                    self.assertTrue((path.parent / link).is_file(), (path, link))
        return manifest['skills']


class RecoverySkillTests(SkillFixture):
    def test_cli_receives_only_eligible_catalogue_and_can_read_complete_snapshots(self):
        cases = [
            ('initial', 'reference', '', set()),
            ('initial', 'assembly', '', {'lightre-recover-assembly'}),
            ('targeted', 'reference', '', {'lightre-recover-assembly', 'lightre-refine-candidate'}),
            ('comment', 'reference', '把参数 value 改名为 input',
             {'lightre-recover-assembly', 'lightre-refine-candidate', 'lightre-apply-feedback'}),
            ('comment', 'assembly', '核对返回值', {'lightre-recover-assembly', 'lightre-apply-feedback'}),
        ]
        for mode, input_mode, comment, expected in cases:
            with self.subTest(mode=mode, input_mode=input_mode):
                state, batch = self.task(mode, input_mode, comment)
                prefix = Path(state['directory']) / 'batch-0000'
                def start(command, **kwargs):
                    text = kwargs['stdin'].read().decode('utf-8')
                    self.assertEqual(text, prefix.with_suffix('.prompt.md').read_text())
                    if not expected:
                        self.assertFalse(prefix.with_suffix('.skills.json').exists())
                        self.assertNotIn('内部方法目录', text)
                        self.assertIn('shell_tool', command)
                    else:
                        entries = self.assert_snapshot(prefix)
                        self.assertEqual({item['name'] for item in entries}, expected)
                        self.assertNotIn('shell_tool', command)
                        for item in entries:
                            self.assertIn(json.dumps(item['path'], ensure_ascii=False), text)
                            # The body is read later, rather than preloaded into stdin.
                            body = Path(item['path']).read_text().split('---', 2)[2].strip()
                            self.assertNotIn(body, text)
                    return SimpleNamespace(returncode=0)
                with patch('recovery.jobs.subprocess.Popen', side_effect=start):
                    _start_batch(self.reader, state, 0, batch)
        self.assertFalse((self.directory / '.agents').exists())

    def test_snapshots_survive_source_updates_and_preserve_relative_references(self):
        root = Path(self.temp.name) / 'package-skills'
        shutil.copytree(SKILLS, root)
        prefix = self.directory / 'batch-0000'
        with patch('recovery.skills.SKILLS', root):
            text = skill_catalog(prefix)
        entries = self.assert_snapshot(prefix)
        original = Path(entries[0]['path']).read_bytes()
        source = root / entries[0]['name'] / 'SKILL.md'
        source.write_text('changed after submission', encoding='utf-8')
        self.assertEqual(Path(entries[0]['path']).read_bytes(), original)
        self.assertIn('{{TASK}}', text)
        self.assert_snapshot(prefix)

    def test_invalid_skill_metadata_prevents_cli_launch(self):
        root = Path(self.temp.name) / 'broken-skills'
        shutil.copytree(SKILLS, root)
        path = root / 'lightre-recover-assembly/SKILL.md'
        path.write_text('---\nname: wrong-name\ndescription: test\n---\nbody', encoding='utf-8')
        state, batch = self.task('targeted')
        with patch('recovery.skills.SKILLS', root), patch('recovery.jobs.subprocess.Popen') as start:
            with self.assertRaisesRegex(ValueError, '名称或描述无效'):
                _start_batch(self.reader, state, 0, batch)
            start.assert_not_called()


@unittest.skipUnless(os.environ.get('LIGHTRE_TEST_LIVE_CODEX') == '1', '显式启用真实 Codex CLI 验证')
class LiveRecoverySkillTests(SkillFixture):
    def run_live(self, mode, input_mode='reference', comment=''):
        executable = shutil.which(os.environ.get('LIGHTRE_CODEX_BIN', 'codex'))
        self.assertIsNotNone(executable, '找不到 Codex CLI')
        state, batch = self.task(mode, input_mode, comment)
        state['codex'] = executable
        running = _start_batch(self.reader, state, 0, batch)
        try:
            running['process'].wait(timeout=180)
        except subprocess.TimeoutExpired:
            _terminate(running['process'])
            self.fail('真实 Codex CLI 超时，已终止该测试进程')
        prefix = running['prefix']
        self.assertEqual(running['process'].returncode, 0, prefix.with_suffix('.stderr.log').read_text())
        self.assert_snapshot(prefix)
        events = [json.loads(line) for line in prefix.with_suffix('.events.jsonl').read_text().splitlines()]
        commands = [event['item'].get('command', '') for event in events
                    if event.get('item', {}).get('type') == 'command_execution']
        self.assertTrue(any('.skills/' in command and 'SKILL.md' in command for command in commands),
                        '模型没有读取本批注入的 Skill 文档')
        _publish_batch(self.reader, state, running)
        self.assertEqual(state['done_rvas'], [4])
        self.assertFalse(state['unresolved'])
        return read_candidate(self.reader, 4)['display_code']

    def test_live_assembly(self):
        self.assertTrue(self.run_live('initial', 'assembly').strip())

    def test_live_targeted(self):
        self.assertTrue(self.run_live('targeted').strip())

    def test_live_feedback(self):
        save_candidate(self.reader, 4, '#include <stdint.h>\n\n'
                       'uint64_t fcn_00000004(uint64_t value) { return value + UINT64_C(7); }')
        candidate = self.run_live('comment', comment='将参数 value 重命名为 input，保持行为不变，并同步知识中的名称。')
        self.assertIn('input', candidate)
        self.assertNotIn('value', candidate)
        self.assertIn('input', read_knowledge(self.reader, 4))


if __name__ == '__main__':
    unittest.main()
