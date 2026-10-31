import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analyzers.rizin import _publish
from workspace import RESULT_SCHEMA, WorkspaceReader, prepare_workspace


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

    def test_export_replaces_stale_rows_and_paginates_nonzero_rva(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            _publish(conn, FakeRizin(), 0x1000, "test-version")
            self.assertEqual(conn.execute("SELECT count(*) FROM assembly WHERE function_rva=4").fetchone()[0], 0)
        reader = WorkspaceReader(self.directory)
        self.assertEqual([f["rva"] for f in reader.catalog()["functions"]], [8])
        first = reader.assembly(8, 0, 2)
        self.assertEqual([op["rva"] for op in first["instructions"]], [8, 12])
        self.assertEqual(first["next_offset"], 2)
        self.assertEqual(reader.assembly(8, 2)["instructions"][0]["rva"], 16)
        self.assertIsNone(reader.assembly(8, 2)["next_offset"])

    def test_failed_export_preserves_previous_complete_result(self):
        with sqlite3.connect(self.db) as conn:
            with self.assertRaisesRegex(RuntimeError, "decode failed"):
                _publish(conn, FakeRizin(fail=True), 0x1000, "test-version")
        reader = WorkspaceReader(self.directory)
        self.assertEqual(reader.catalog()["functions"][0]["name"], "old")
        self.assertEqual(reader.assembly(4)["instructions"][0]["opcode"], "ret")

    def test_invalid_arm64_slot_does_not_shift_following_instructions(self):
        class InvalidRizin(FakeRizin):
            def command(self, command):
                if command.startswith("pdj 3"):
                    return json.dumps([{"offset": 0x1008 + i, "bytes": "00000000", "opcode": "invalid"}
                                       for i in range(3)])
                if command.startswith("pdj 1"):
                    return json.dumps([{"offset": int(command.split()[-1]),
                                        "bytes": "00000000", "opcode": "invalid"}])
                return super().command(command)

        with sqlite3.connect(self.db) as conn:
            _publish(conn, InvalidRizin(), 0x1000, "test-version")
        ops = WorkspaceReader(self.directory).assembly(8)["instructions"]
        self.assertEqual([op["rva"] for op in ops], [8, 12, 16])

    def test_open_missing_result_does_not_create_workspace(self):
        missing = self.directory / "missing"
        with self.assertRaises(ValueError):
            WorkspaceReader(missing)
        self.assertFalse(missing.exists())

    def test_default_workspace_uses_working_directory_outside_sample_repo(self):
        repo = self.directory / "samples"
        source = repo / "target"
        source.mkdir(parents=True)
        (repo / ".git").mkdir()
        binary = source / "example.so"
        binary.write_bytes(b"test binary")
        workdir = self.directory / "work"
        with patch("workspace.Path.cwd", return_value=workdir):
            result = prepare_workspace(binary)
            self.assertEqual(prepare_workspace(binary), result)
        self.assertEqual(result.parent, workdir / "target-analysis")
        self.assertEqual(list(result.parent.iterdir()), [result])
        with self.assertRaisesRegex(ValueError, "样本 Git"):
            prepare_workspace(binary, repo / "results")

    def test_executable_default_workspace_uses_binary_name_and_reuses_result(self):
        source = self.directory / "samples/minilua/deliverable"
        source.mkdir(parents=True)
        binary = source / "minilua"
        binary.write_bytes(b"test executable")
        workdir = self.directory / "work"
        with patch("workspace.Path.cwd", return_value=workdir):
            result = prepare_workspace(binary)
            self.assertEqual(prepare_workspace(binary), result)
        self.assertEqual(result.parent, workdir / "minilua-analysis")
        self.assertEqual(list(result.parent.iterdir()), [result])
        self.assertEqual(list(source.iterdir()), [binary])


class FakeRizin:
    def __init__(self, fail=False):
        self.fail = fail

    def command(self, command):
        if command == "aflj":
            return json.dumps([{"offset": 0x1008, "name": "new"}])
        if command.startswith("afbj"):
            return json.dumps([{"addr": 0x1008, "size": 12}])
        if self.fail:
            raise RuntimeError("decode failed")
        return json.dumps([{"offset": addr, "bytes": "1f2003d5", "opcode": "nop"}
                           for addr in [0x1008, 0x100c, 0x1010]])


if __name__ == "__main__":
    unittest.main()
