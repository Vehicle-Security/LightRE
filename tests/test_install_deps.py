"""Exercise installer decisions with a private PATH and an apt boundary stub."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/install_deps.sh'


class InstallDepsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for tool in ('readlink', 'dirname', 'sed', 'head'):
            (self.bin/tool).symlink_to(shutil.which(tool))
        for tool in ('curl', 'git', 'python3', 'unzip', 'make', 'gcc', 'g++', 'bison',
                     'flex', 'clang-format', 'mvn', 'clang', 'ld.lld'):
            (self.bin/tool).symlink_to(shutil.which('true'))
        self.program('java', 'print(\'openjdk version "25.0.1"\')')
        self.program('javac', "print('javac 25.0.1')")
        self.program('apt-get', '''
import json, os, sys
with open(os.environ['TEST_APT_LOG'], 'a') as log:
    log.write(json.dumps(sys.argv[1:])+'\\n')
''')
        self.env = {**os.environ, 'PATH': str(self.bin), 'TEST_APT_LOG': str(self.root/'apt.log')}

    def program(self, name, body):
        path = self.bin/name
        path.write_text(f'#!{sys.executable}\n{body}\n')
        path.chmod(0o755)

    def shell(self, body):
        return subprocess.run(['/bin/bash', '-c', 'source "$1"; INSTALL_GHIDRA=false; '+body,
                               'test', str(SCRIPT)], env=self.env, capture_output=True, text=True)

    def test_missing_runtime_tools_defeat_installer_early_exit(self):
        for tool in ('mvn', 'clang', 'ld.lld', 'javac'):
            with self.subTest(tool=tool):
                current = self.bin/tool
                current.rename(self.bin/(tool+'.hidden'))
                result = self.shell('if has_build_dependencies; then exit 0; else exit 3; fi')
                (self.bin/(tool+'.hidden')).rename(current)
                self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(self.shell('has_build_dependencies').returncode, 0)

    def test_old_java_is_rejected_even_with_a_new_compiler(self):
        self.program('java', 'print(\'openjdk version "11.0.1"\')')
        result = self.shell('if has_java; then exit 0; else exit 3; fi')
        self.assertEqual(result.returncode, 3, result.stderr)

    def test_runtime_install_contains_all_new_packages(self):
        result = self.shell('as_root() { "$@"; }; install_system_packages')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in (self.root/'apt.log').read_text().splitlines()]
        self.assertEqual(calls[0], ['update'])
        for package in ('default-jdk', 'maven', 'clang', 'lld'):
            self.assertIn(package, calls[1])

    def test_android_tools_are_checked_and_included_in_the_unified_installer(self):
        android_tools = ('clang-14', 'llvm-config-14', 'opt-14', 'llc-14', 'cmake', 'ninja', 'patch', 'readelf')
        for tool in android_tools:
            (self.bin/tool).symlink_to(shutil.which('true'))
        self.assertEqual(self.shell('INSTALL_ANDROID=true; has_build_dependencies').returncode, 0)
        for tool in android_tools:
            current = self.bin/tool
            current.rename(self.bin/(tool+'.hidden'))
            result = self.shell('INSTALL_ANDROID=true; has_build_dependencies')
            (self.bin/(tool+'.hidden')).rename(current)
            self.assertNotEqual(result.returncode, 0, tool)
        result = self.shell('INSTALL_ANDROID=true; as_root() { "$@"; }; install_system_packages')
        self.assertEqual(result.returncode, 0, result.stderr)
        packages = json.loads((self.root/'apt.log').read_text().splitlines()[1])
        for package in ('default-jdk', 'maven', 'clang', 'lld', 'clang-14', 'llvm-14-dev',
                        'cmake', 'ninja-build', 'patch', 'binutils'):
            self.assertIn(package, packages)
