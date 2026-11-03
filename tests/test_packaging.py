"""Installed entry points and runtime assets; no model or native analyzer."""

import subprocess
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from analyzers.ghidra import SCRIPT
from rehost.guide import TEMPLATE, ensure
from rehost.loop import PROMPT
from lightre.paths import CLANG_FORMAT
from lightre.paths import PACKAGE_DIR
from rehost.workflow import WORKFLOWS
import recovery
import webui


class PackagingTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('LIGHTRE_TEST_DIST'), '设置 LIGHTRE_TEST_DIST 检查实际发布包')
    def test_every_module_markdown_is_in_wheel_and_sdist(self):
        root = Path(__file__).resolve().parents[1]
        distribution = Path(os.environ['LIGHTRE_TEST_DIST'])
        expected = {str(path.relative_to(root / 'src')): path.read_bytes()
                    for path in (root / 'src').rglob('*.md')}
        self.assertTrue(expected)
        wheels = list(distribution.glob('*.whl'))
        sources = list(distribution.glob('*.tar.gz'))
        self.assertEqual(len(wheels), 1, '发布目录应包含当前版本的一个 wheel')
        self.assertEqual(len(sources), 1, '发布目录应包含当前版本的一个源码包')
        with zipfile.ZipFile(wheels[0]) as wheel:
            markdown = {name for name in wheel.namelist() if name.endswith('.md')}
            self.assertEqual(markdown, set(expected), 'wheel 存在缺失或过期 Markdown')
            for name, content in expected.items():
                self.assertEqual(wheel.read(name), content, name)
        with tarfile.open(sources[0]) as source:
            files = {entry.name.split('/', 1)[-1]: entry for entry in source.getmembers() if entry.isfile()}
            for name, content in expected.items():
                with self.subTest(path=name):
                    self.assertIn('src/' + name, files)
                    self.assertEqual(source.extractfile(files['src/' + name]).read(), content)

    def test_lightre_module_help_loads_from_an_external_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-I', '-m', 'lightre', '--help'], cwd=directory,
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('usage:', result.stdout)
            self.assertIn('workspace', result.stdout)

    def test_internal_skills_load_from_an_external_working_directory(self):
        script = '''
import json
from pathlib import Path
from recovery.skills import skill_catalog
prefix = Path.cwd() / 'batch-0000'
prompt = skill_catalog(prefix, feedback=True)
manifest = json.loads(prefix.with_suffix('.skills.json').read_text())
assert len(manifest['skills']) == 3
for skill in manifest['skills']:
    assert Path(skill['path']).is_file()
    assert json.dumps(skill['path'], ensure_ascii=False) in prompt
    for document in skill['files']:
        assert (prefix.with_suffix('.skills') / document['path']).is_file()
print('internal skills loaded')
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-I', '-c', script], cwd=directory,
                                    text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('internal skills loaded', result.stdout)

    def test_runtime_resources_are_readable_from_installed_modules(self):
        self.assertIn('BasedOnStyle:', CLANG_FORMAT.read_text())
        self.assertIn('DT_NEEDED', (PACKAGE_DIR / 'prompts/EXPLORE.md').read_text())
        self.assertIn('ExportReferences', SCRIPT.read_text())
        self.assertTrue(SCRIPT.with_name('DecompileFunction.java').is_file())
        self.assertIn('{{TARGET}}', TEMPLATE.read_text())
        self.assertIn('harness', PROMPT.read_text())
        self.assertIn('unidbg', (WORKFLOWS / 'pom.xml').read_text())
        self.assertTrue((WORKFLOWS / 'WORKFLOW.md').is_file())
        for name in ('INITIAL.md', 'FEEDBACK.md', 'ASSEMBLY.md'):
            self.assertTrue((Path(recovery.__file__).parent / 'prompts' / name).is_file())
        web = Path(webui.__file__).parent
        self.assertTrue((web / 'index.html').is_file())
        self.assertTrue((web / 'static/highlight-11.11.1.min.js').is_file())

    def test_generated_harness_instructions_use_the_current_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            body = ensure(Path(directory) / 'harness').read_text()
        self.assertIn('由 LightRE 主 workflow 自动执行', body)
        self.assertIn('Java、Maven', body)
        self.assertNotIn('{{', body)


if __name__ == '__main__':
    unittest.main()
