"""Fast Ghidra import contracts; no real tools, models or Web server."""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analyzers.ghidra import analyze, investigate_function
from recovery import read_candidate, save_candidate, read_knowledge, save_knowledge
from workspace import WorkspaceReader


class GhidraTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary = self.root / 'sample.so'
        header = bytearray(64)
        header[:6] = b'\x7fELF\x02\x01'
        header[18:20] = (183).to_bytes(2, 'little')
        self.binary.write_bytes(header)
        self.calls = []
        self.functions = [
            {'rva': 8, 'name': 'fcn_00000008', 'instructions': 2, 'ranges': [[8, 16]],
             'status': 'ok', 'code': 'void fcn_00000008(void) {}', 'module': '.text', 'error': ''},
            {'rva': 16, 'name': 'fcn_00000010', 'instructions': 1, 'ranges': [[16, 20]],
             'status': 'error', 'code': None, 'module': '.text', 'error': 'timeout'},
        ]
        self.corrupt = False
        test = self

        class Headless:
            def __init__(process, command, **kwargs):
                test.calls.append(command)
                offset = command.index('ExportReferences.java')
                process.output = Path(command[offset + 1])
                process.assembly = Path(command[offset + 2])
                process.digest = command[offset + 3]

            def wait(process):
                process.output.write_text(json.dumps({'sha256': process.digest, 'image_base': 0x1000,
                    'export_schema': 1, 'language': 'AARCH64:LE:64:v8A', 'analysis_timed_out': False,
                    'functions': test.functions}))
                rows = [{'rva': fn['rva'], 'instructions': [
                    {'rva': address, 'bytes': '1f2003d5', 'opcode': 'nop'}
                    for start, end in fn['ranges'] for address in range(start, end, 4)]}
                    for fn in test.functions]
                if test.corrupt:
                    rows[-1]['instructions'] = []
                process.assembly.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                return 0

        patches = [patch('recovery.source.format_code', side_effect=lambda reader, code: code),
                   patch('analyzers.ghidra.find_headless', return_value=str(self.root / 'support/analyzeHeadless')),
                   patch('analyzers.ghidra.subprocess.Popen', Headless)]
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)

    def export(self, force=False):
        return WorkspaceReader(analyze(self.binary, self.root / 'workspace', force=force))

    def test_ghidra_owns_catalog_assembly_and_drafts_and_reuses_complete_export(self):
        reader = self.export()
        self.assertEqual([fn['rva'] for fn in reader.catalog()['functions']], [8, 16])
        self.assertEqual(reader.catalog()['sample']['analysis_profile'], 'ghidra-catalog-v1')
        self.assertEqual(reader.assembly(8, 1)['instructions'][0]['rva'], 12)
        self.assertEqual(reader.assembly(16)['total'], 1)
        self.assertEqual(read_candidate(reader, 8)['producer'], 'ghidra')
        self.assertIsNone(read_candidate(reader, 16)['code'])
        self.assertEqual(self.export().directory, reader.directory)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('-rvas', self.calls[0])

    def test_targeted_probe_is_read_only_and_preserves_current_catalog_and_source(self):
        reader = self.export()
        project = reader.directory / 'references/ghidra/project/LightRE.gpr'
        project.parent.mkdir(exist_ok=True)
        project.touch()
        before = read_candidate(reader, 8)
        calls = []

        class Probe:
            def __init__(process, command, **kwargs):
                calls.append(command)
                offset = command.index('DecompileFunction.java')
                process.plan = json.loads(Path(command[offset + 1]).read_text())
                process.output = Path(command[offset + 2])
            def wait(process, timeout):
                process.output.write_text(json.dumps({'rva': process.plan['rva'],
                    'completed': True, 'code': 'void probe(void) {}', 'error': ''}))
                return 0

        with patch('analyzers.ghidra.subprocess.Popen', Probe):
            result = investigate_function(reader, 8, [{'rva': 12, 'targets': [16]}], timeout=1)
        self.assertIn('-readOnly', calls[0])
        self.assertIn('-noanalysis', calls[0])
        self.assertNotIn('-import', calls[0])
        self.assertTrue(result['completed'])
        self.assertEqual(result['sha256'], reader.catalog()['sample']['sha256'])
        self.assertEqual(read_candidate(reader, 8)['sha256'], before['sha256'])
        self.assertEqual(reader.assembly(8)['total'], 2)
        self.assertTrue(Path(result['path']).is_file())

    def test_targeted_probe_rejects_unknown_function_and_invalid_mode(self):
        reader = self.export()
        with self.assertRaisesRegex(ValueError, '未识别'):
            investigate_function(reader, 999)
        with self.assertRaisesRegex(ValueError, '无效'):
            investigate_function(reader, 8, style='unsupported')

    def test_reexport_rebuilds_source_index_and_preserves_unchanged_source(self):
        reader = self.export()
        save_candidate(reader, 8, 'void fcn_00000008(void) { work(); }')
        before = read_candidate(reader, 8)
        self.export(force=True)
        self.assertEqual(read_candidate(reader, 8)['sha256'], before['sha256'])
        with reader._connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM source_index').fetchone()[0], 1)

    def test_failed_export_preserves_catalog_source_and_knowledge(self):
        reader = self.export()
        save_candidate(reader, 8, 'void fcn_00000008(void) { work(); }')
        before = read_candidate(reader, 8)
        save_knowledge(reader, 8, '保留知识。')
        self.functions[0]['ranges'] = [[8, 20]]
        self.functions[0]['instructions'] = 3
        self.corrupt = True
        with self.assertRaisesRegex(ValueError, '汇编不完整'):
            self.export(force=True)
        self.assertEqual(read_candidate(reader, 8)['sha256'], before['sha256'])
        self.assertEqual(reader.assembly(8)['total'], 2)
        self.assertEqual(read_knowledge(reader, 8), '保留知识。')

    def test_changed_boundaries_retire_stale_code_and_link_old_entry_to_owner(self):
        reader = self.export()
        save_candidate(reader, 8, 'void old(void) { work(); }')
        before = read_candidate(reader, 8)
        save_knowledge(reader, 8, '旧边界知识。')
        save_knowledge(reader, 16, '内部块知识。')
        self.functions = [{**self.functions[0], 'instructions': 3, 'ranges': [[8, 20]]}]
        self.export(force=True)
        self.assertEqual([fn['rva'] for fn in reader.catalog()['functions']], [8])
        self.assertEqual(read_candidate(reader, 8)['producer'], 'ghidra')
        self.assertIn('旧边界知识', read_knowledge(reader, 8))
        self.assertIn('0x10', read_knowledge(reader, 8))
        self.assertEqual(read_knowledge(reader, 16), '内部块知识。')
        mapping = json.loads((reader.directory / 'references/ghidra/boundaries.json').read_text())['changes']
        owner = next(item for item in mapping if item['old_rva'] == 16)
        self.assertEqual(owner['new_rva'], 8)
        archived = reader.directory / owner['archive']
        self.assertTrue(any(before['code'] in p.read_text() for p in (archived / 'source/src').glob('*.c')))
        with sqlite3.connect(reader.database) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM assembly').fetchone()[0], 3)
