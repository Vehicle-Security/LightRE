"""Installed entry points and runtime assets; no model or native analyzer."""

import os
import subprocess
import sys
import tempfile
import unittest
from importlib.metadata import distribution
from pathlib import Path

from analyzers.ghidra import SCRIPT
from execution.guide import TEMPLATE, ensure
from execution.loop import PROMPT
from lightre.paths import CLANG_FORMAT
from rehost.workflow import WORKFLOWS
import recovery
import webui


class PackagingTests(unittest.TestCase):
    def test_commands_load_from_an_external_directory_without_pythonpath(self):
        environment = os.environ.copy()
        environment.pop('PYTHONPATH', None)
        with tempfile.TemporaryDirectory() as directory:
            for command in ('agent', 'analyze', 'harness', 'rehost', 'web'):
                with self.subTest(command=command):
                    result = subprocess.run([sys.executable, '-I', '-m', 'lightre', command, '--help'],
                                            cwd=directory, env=environment, text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('usage:', result.stdout)
            result = subprocess.run([sys.executable, '-I', '-m', 'lightre', '--version'], cwd=directory,
                                    env=environment, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), distribution('lightre').version)

    def test_runtime_resources_are_readable_from_installed_modules(self):
        self.assertIn('BasedOnStyle:', CLANG_FORMAT.read_text())
        self.assertIn('ExportReferences', SCRIPT.read_text())
        self.assertTrue(SCRIPT.with_name('DecompileFunction.java').is_file())
        self.assertIn('{{HARNESS_COMMAND}}', TEMPLATE.read_text())
        self.assertIn('harness', PROMPT.read_text())
        self.assertIn('unidbg', (WORKFLOWS / 'pom.xml').read_text())
        self.assertTrue((WORKFLOWS / 'WORKFLOW.md').is_file())
        self.assertTrue((Path(recovery.__file__).parent / 'prompts/RECOVER_AND_REFINE.md').is_file())
        web = Path(webui.__file__).parent
        self.assertTrue((web / 'index.html').is_file())
        self.assertTrue((web / 'static/highlight-11.11.1.min.js').is_file())

    def test_generated_harness_instructions_use_the_installed_interpreter(self):
        with tempfile.TemporaryDirectory() as directory:
            body = ensure(Path(directory) / 'harness').read_text()
        self.assertIn('lightre.cli.harness run', body)
        self.assertIn(sys.executable, body)
        self.assertNotIn('tools/venv', body)
        self.assertNotIn('{{', body)


if __name__ == '__main__':
    unittest.main()
