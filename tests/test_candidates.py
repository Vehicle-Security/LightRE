import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from candidates import read_candidate, save_candidate
from workspace import RESULT_SCHEMA, WorkspaceReader


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.reader = self.make_reader("one", "a" * 64)

    def make_reader(self, name, digest):
        directory = self.directory / name
        directory.mkdir()
        with sqlite3.connect(directory / "index.sqlite") as conn:
            conn.executescript("""
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE functions (rva INTEGER PRIMARY KEY, name TEXT,
                                        instructions INTEGER, ranges TEXT);
                INSERT INTO functions VALUES (4,'test',0,'[]');
            """)
            conn.executemany("INSERT INTO metadata VALUES (?,?)", {
                "name": name, "sha256": digest, "source_path": "/inputs/test.so",
                "state": "ready", "schema_version": RESULT_SCHEMA,
            }.items())
        return WorkspaceReader(directory)

    def test_persists_exact_file_and_reads_external_edits_without_tools_or_db_writes(self):
        database = self.reader.database.read_bytes()
        with patch("subprocess.Popen", side_effect=AssertionError("must not run tools")):
            self.assertEqual(read_candidate(self.reader, 4)["status"], "missing")
            self.assertFalse((self.reader.directory / "candidates").exists())
            path = save_candidate(self.reader, 4, "int test(void) { return 1; }\n")
            first = read_candidate(WorkspaceReader(self.reader.directory), 4)
            self.assertEqual(first["code"], path.read_text())
            self.assertIn("function_rva: 0x00000004", first["code"])
            self.assertIn("sha256: " + "a" * 64, first["code"])
            path.write_text(first["code"].replace("return 1", "return 2"))
            self.assertIn("return 2", read_candidate(self.reader, 4)["code"])
            save_candidate(self.reader, 4, path.read_text())
            self.assertEqual(path.read_text().count("/* LightRE analysis candidate"), 1)
            path.unlink()
            self.assertEqual(read_candidate(self.reader, 4)["status"], "missing")
        self.assertEqual(self.reader.database.read_bytes(), database)

    def test_same_rva_in_another_binary_is_independent_and_rejects_copied_candidate(self):
        other = self.make_reader("two", "b" * 64)
        path = save_candidate(self.reader, 4, "void test(void) {}\n")
        self.assertIsNone(read_candidate(other, 4)["code"])
        with self.assertRaisesRegex(ValueError, "来源注释"):
            save_candidate(other, 4, path.read_text())
        copied = other.directory / "candidates" / path.name
        copied.parent.mkdir()
        copied.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ValueError, "来源注释"):
            read_candidate(other, 4)

    def test_unknown_function_and_empty_code_do_not_create_candidate_files(self):
        with self.assertRaises(KeyError):
            save_candidate(self.reader, 8, "void missing(void) {}")
        with self.assertRaises(KeyError):
            read_candidate(self.reader, 8)
        with self.assertRaises(ValueError):
            save_candidate(self.reader, 4, " \n")
        self.assertFalse((self.reader.directory / "candidates").exists())

    def test_failed_replace_preserves_previous_file_and_cleans_temporary(self):
        path = save_candidate(self.reader, 4, "int test(void) { return 1; }\n")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                save_candidate(self.reader, 4, "int test(void) { return 2; }\n")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(list(path.parent.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
