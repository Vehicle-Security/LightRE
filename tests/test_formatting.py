"""Tiny formatter/storage fixtures; no analyzer, model, browser or Web server."""

import fcntl
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from recovery import format_sources, read_candidate, read_knowledge, save_candidate
from recovery.candidates import GHIDRA_MARKER, _identity, _blocks, _read_text
from recovery.formatting import FormattingError, format_code
from recovery.jobs import _publish_batch
from recovery.markers import regions
from recovery.source import prepare_source
from workspace import RESULT_SCHEMA, WorkspaceReader


class FormattingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        with sqlite3.connect(self.directory / 'index.sqlite') as conn:
            conn.executescript('''
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE functions (rva INTEGER PRIMARY KEY, name TEXT, instructions INTEGER, ranges TEXT);
                INSERT INTO functions VALUES (4,'first',0,'[]'),(8,'second',0,'[]');
            ''')
            conn.executemany('INSERT INTO metadata VALUES (?,?)', {
                'name': 'format-fixture', 'sha256': 'a' * 64, 'source_path': '/inputs/test.so',
                'state': 'ready', 'schema_version': RESULT_SCHEMA,
            }.items())
        self.reader = WorkspaceReader(self.directory)

    def publish(self, code):
        prefix = self.directory / 'batch-0000'
        prefix.with_suffix('.result.json').write_text(json.dumps({
            'results': [{'rva': '0x4', 'code': code, 'notes': '核对逻辑。'}]}))
        state = {'done_rvas': [], 'generated': 0, 'unresolved': {}}
        running = {'prefix': prefix, 'process': SimpleNamespace(returncode=0),
                   'before': {4: read_candidate(self.reader, 4)['sha256']},
                   'batch': [{'rva': 4, 'module': 'text'}]}
        _publish_batch(self.reader, state, running)
        return state

    @unittest.skipUnless(shutil.which('clang-format'), 'clang-format is not installed')
    def test_signature_spacing_utf8_literals_macros_and_include_order(self):
        code = '''#include "z.h"
#include "a.h"
// 中文说明
#define STRINGIFY(x) #x
const char *example = R"raw(keep )

{ inside literal)raw";
void first(long param_1,uint param_2)

{
    const char *s="a  b";
    if(param_2){consume(s);}
}
'''
        formatted = format_code(self.reader, code)
        self.assertIn('void first(long param_1, uint param_2) {\n', formatted)
        self.assertIn('    if (param_2) {\n        consume(s);', formatted)
        self.assertIn('// 中文说明', formatted)
        literals = lambda text: [value for kind, _, _, value in regions(text) if kind == 'literal']
        self.assertEqual(literals(code), literals(formatted))
        self.assertLess(formatted.index('z.h'), formatted.index('a.h'))
        self.assertIn('#define STRINGIFY(x) #x', formatted)
        self.assertEqual(formatted, format_code(self.reader, formatted))

    @unittest.skipUnless(shutil.which('clang-format'), 'clang-format is not installed')
    def test_existing_modules_keep_identity_shared_declarations_and_producer(self):
        path = save_candidate(self.reader, 4, GHIDRA_MARKER + 'void first(void)\n\n{\n  consume();\n}')
        save_candidate(self.reader, 8, 'int second(void)\n\n{\nreturn 42;\n}')
        path.write_text(path.read_text().replace('/* LightRE source module */\n\n',
                                               '/* LightRE source module */\n\ntypedef unsigned int word;\n'))
        meta, _ = _identity(self.reader)
        identities = [b.metadata for b in _blocks(_read_text(path), meta)]
        original = read_candidate(self.reader, 4)
        self.assertEqual(format_sources(self.reader), {'files': 1, 'functions': 2})
        self.assertEqual(identities, [b.metadata for b in _blocks(_read_text(path), meta)])
        current = read_candidate(self.reader, 4)
        self.assertEqual(current['producer'], 'ghidra')
        self.assertNotEqual(current['sha256'], original['sha256'])
        self.assertIn('void first(void) {', current['display_code'])
        self.assertIn('typedef unsigned int word;', path.read_text())
        self.assertTrue((self.directory / 'source/.clang-format').exists())
        self.assertEqual(format_sources(self.reader), {'files': 0, 'functions': 2})

    @unittest.skipUnless(shutil.which('clang-format'), 'clang-format is not installed')
    def test_ghidra_seed_and_agent_publication_are_formatted(self):
        prepare_source(self.reader, [{'rva': 4, 'status': 'ok', 'code': 'void first(void)\n\n{\nseed();\n}'}])
        self.assertIn('void first(void) {\n    seed();', read_candidate(self.reader, 4)['display_code'])
        state = self.publish('int renamed(int x)\n\n{\nreturn x+1;\n}')
        current = read_candidate(self.reader, 4)
        self.assertIn('int renamed(int x) {\n    return x + 1;', current['display_code'])
        self.assertEqual(current['producer'], 'llm')
        self.assertEqual(state['generated'], 1)

    def test_missing_formatter_preserves_generated_draft_and_records_the_failure(self):
        code = 'int first(void)\n\n{\nreturn 42;\n}'
        with patch('recovery.formatting.shutil.which', return_value=None):
            result = prepare_source(self.reader, [{'rva': 4, 'status': 'ok', 'code': code}])
            self.assertIn('未安装 clang-format', result['format_errors']['0x4'])
            self.assertEqual(read_candidate(self.reader, 4)['display_code'], code)
            self.publish(code)
            self.assertEqual(read_candidate(self.reader, 4)['display_code'], code)
            self.assertIn('格式化未完成', read_knowledge(self.reader, 4))

    def test_edit_during_formatting_is_not_overwritten(self):
        save_candidate(self.reader, 4, 'int first(void) { return 1; }')
        def external_edit(reader, code):
            save_candidate(reader, 4, 'int edited(void) { return 99; }')
            return code
        with patch('recovery.jobs.format_code', side_effect=external_edit):
            state = self.publish('int generated(void) { return 2; }')
        self.assertIn('return 99', read_candidate(self.reader, 4)['code'])
        self.assertEqual(read_knowledge(self.reader, 4), '')
        self.assertIn('保留当前文件', state['unresolved']['0x4'])

    def test_failed_or_incomplete_formatting_and_active_worker_preserve_files(self):
        path = save_candidate(self.reader, 4, 'void first(void)\n\n{}')
        original = path.read_bytes()
        results = [SimpleNamespace(returncode=1, stdout='', stderr='bad draft'),
                   SimpleNamespace(returncode=0, stdout='<replacements incomplete_format="true"/>', stderr='')]
        with patch('recovery.formatting.shutil.which', return_value='/fake/clang-format'):
            for result in results:
                with patch('recovery.formatting.subprocess.run', return_value=result):
                    with self.assertRaises(FormattingError):
                        format_sources(self.reader)
                self.assertEqual(path.read_bytes(), original)
        with (self.directory / 'tasks/.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, '后台任务结束'):
                format_sources(self.reader)
        self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
