import sqlite3
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lightre.workspace import RESULT_SCHEMA, WorkspaceReader, prepare_workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.db = self.directory / "index.sqlite"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE functions (rva INTEGER PRIMARY KEY, name TEXT,
                                        instructions INTEGER, ranges TEXT);
                CREATE TABLE assembly (function_rva INTEGER REFERENCES functions(rva),
                    ordinal INTEGER, rva INTEGER, bytes TEXT, opcode TEXT,
                    PRIMARY KEY(function_rva, ordinal));
            """)
            conn.executemany("INSERT INTO metadata VALUES (?,?)", {
                "name": "test", "state": "ready", "schema_version": RESULT_SCHEMA,
            }.items())
            conn.execute("INSERT INTO functions VALUES (4,'old',1,'[[4,8]]')")
            conn.execute("INSERT INTO assembly VALUES (4,0,4,'c0035fd6','ret')")

    def test_browser_reads_without_binary_or_subprocess_and_cannot_write(self):
        with patch("subprocess.Popen", side_effect=AssertionError("must not run a tool")):
            reader = WorkspaceReader(self.directory)
            self.assertEqual(reader.catalog()["sample"]["function_count"], 1)
            self.assertEqual(reader.assembly(4)["instructions"][0]["opcode"], "ret")
            self.assertEqual(reader.assembly(4, 1)["instructions"], [])
            with reader._connect() as conn:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("DELETE FROM functions")

    def test_open_missing_result_does_not_create_workspace(self):
        missing = self.directory / "missing"
        with self.assertRaises(ValueError):
            WorkspaceReader(missing)
        self.assertFalse(missing.exists())

    def test_default_workspace_reuses_so_and_executable_results_outside_sample_repo(self):
        repo = self.directory / "samples"
        repo.mkdir()
        (repo / '.git').mkdir()
        workdir = self.directory / 'work'
        for name in ('target/example.so', 'minilua/deliverable/minilua'):
            with self.subTest(input=name):
                binary = repo / name
                binary.parent.mkdir(parents=True)
                binary.write_bytes(name.encode())
                with patch('lightre.workspace.Path.cwd', return_value=workdir):
                    result = prepare_workspace(binary)
                    self.assertEqual(prepare_workspace(binary), result)
                self.assertEqual(result.parent, workdir / 'workspace')
                self.assertEqual((result / 'binary/original.so').read_bytes(), binary.read_bytes())
                self.assertEqual(list(binary.parent.iterdir()), [binary])
        self.assertEqual(len(list((workdir / 'workspace').glob('*/index.sqlite'))), 2)
        with self.assertRaisesRegex(ValueError, "样本 Git"):
            prepare_workspace(binary, repo / "results")

    def test_hash_prefix_collision_extends_name_and_full_hash_reuses_the_result(self):
        source = self.directory / 'inputs'
        source.mkdir()
        binary = source / 'target.so'
        binary.write_bytes(b'target content')
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        workspace = self.directory / 'results'
        occupied = workspace / f'target-{digest[:4]}'
        occupied.mkdir(parents=True)
        result = prepare_workspace(binary, workspace, name='target')
        self.assertEqual(result.name, f'target-{digest[:5]}')
        self.assertEqual(prepare_workspace(binary, workspace, name='target'), result)
        self.assertTrue(occupied.is_dir())


if __name__ == "__main__":
    unittest.main()
