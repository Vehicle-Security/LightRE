"""Dependency provisioning tests without apt, network, or a real toolchain."""

import os
import tempfile
import io
import tarfile
import subprocess
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from lightre import deps


class DependencyTests(unittest.TestCase):
    def test_check_reports_missing_commands_and_python_modules(self):
        with patch.object(deps, '_command', return_value=None), \
             patch.object(deps.importlib.util, 'find_spec', return_value=None):
            report = deps.check('runtime')
        self.assertFalse(report['ok'])
        self.assertIn('java', report['missing_commands'])

    def test_install_system_uses_profile_packages(self):
        calls = []
        with patch.object(deps.shutil, 'which', return_value='/usr/bin/apt-get'), \
             patch.object(deps.os, 'geteuid', return_value=0), \
             patch.object(deps, '_run', side_effect=lambda command: calls.append(command)):
            result = deps.install_system('runtime')
        self.assertIn('maven', result['installed'])
        self.assertEqual(calls[0], ['apt-get', 'update'])
        self.assertIn('maven', calls[1])

    def test_ensure_can_install_then_recheck(self):
        reports = [
            {'ok': False, 'missing_commands': ['clang'], 'missing_python': [], 'tools': {}},
            {'ok': True, 'missing_commands': [], 'missing_python': [], 'tools': {}},
        ]
        with patch.object(deps, 'check', side_effect=reports), \
             patch.object(deps, 'install_system') as install:
            result = deps.ensure('runtime', install=True)
        install.assert_called_once_with('runtime')
        self.assertTrue(result['ok'])

    def test_install_ghidra_extracts_local_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'ghidra.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('ghidra-test/support/analyzeHeadless', '#!/bin/sh\n')
            destination = root / 'installed'
            cache = root / 'tools/cache'
            cache.mkdir(parents=True)
            (cache / archive.name).write_bytes(b'interrupted old download')
            with patch.object(deps, 'TOOLS_DIR', root / 'tools'):
                result = deps.install_ghidra(archive.as_uri(), destination=destination)
            self.assertEqual(Path(result), destination)
            self.assertTrue((destination / 'support/analyzeHeadless').is_file())

    def test_auto_install_can_be_disabled(self):
        report = {'ok': False, 'missing_commands': ['java'], 'missing_python': [], 'tools': {}}
        with patch.dict(os.environ, {'LIGHTRE_AUTO_INSTALL_DEPS': '0'}), \
             patch.object(deps, 'check', return_value=report), \
             patch.object(deps, 'install_system') as install:
            with self.assertRaises(RuntimeError):
                deps.ensure('runtime')
        install.assert_not_called()

    def test_ghidra_java_requirement_is_checked_and_installed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            properties = root / 'ghidra/Ghidra/application.properties'
            properties.parent.mkdir(parents=True)
            properties.write_text('application.java.min=25\n')
            with patch.object(deps, 'TOOLS_DIR', root), \
                    patch.object(deps, '_command', return_value='/tools/java'), \
                    patch.object(deps, '_java_major', return_value=21), \
                    patch.object(deps, '_install_packages') as install:
                report = deps.check('analysis')
                deps.install_system('analysis')
            self.assertIn('java>=25', report['missing_commands'])
            self.assertIn('javac>=25', report['missing_commands'])
            self.assertIn('openjdk-25-jdk-headless', install.call_args.args[0])

    def test_codex_installs_native_executable_without_node(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / 'cache'
            cache.mkdir()
            name = 'codex-aarch64-unknown-linux-musl'
            archive = cache / (name + '.tar.gz')
            binary = b'#!/bin/sh\necho codex-test\n'
            with tarfile.open(archive, 'w:gz') as bundle:
                member = tarfile.TarInfo(name)
                member.size = len(binary)
                bundle.addfile(member, io.BytesIO(binary))
            with patch.object(deps, 'TOOLS_DIR', root), \
                    patch.object(deps, '_command', return_value=None), \
                    patch.object(deps.platform, 'machine', return_value='aarch64'), \
                    patch.dict(os.environ, {'LIGHTRE_CODEX_BIN': ''}):
                installed = Path(deps.install_codex())
            self.assertEqual(installed.read_bytes(), binary)
            self.assertTrue(os.access(installed, os.X_OK))

    def test_ghidra_archive_preserves_launcher_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'ghidra.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                launcher = zipfile.ZipInfo('ghidra-test/support/analyzeHeadless')
                launcher.external_attr = 0o100755 << 16
                bundle.writestr(launcher, '#!/bin/sh\n')
            with patch.object(deps, 'TOOLS_DIR', root / 'tools'):
                installed = Path(deps.install_ghidra(archive.as_uri(), destination=root / 'installed'))
                self.assertTrue(os.access(installed / 'support/analyzeHeadless', os.X_OK))
                # An unpacked ARM64 installation can retry its native build without downloading again.
                self.assertEqual(Path(deps.install_ghidra(destination=installed)), installed)

    def test_failed_download_preserves_previous_cached_file(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / 'tool.tar.gz'
            destination.write_bytes(b'previous complete download')

            def fail(command):
                Path(command[command.index('--output') + 1]).write_bytes(b'partial download')
                raise subprocess.CalledProcessError(22, command)

            with patch.object(deps, '_run', side_effect=fail), \
                    self.assertRaises(subprocess.CalledProcessError):
                deps._download('https://example.invalid/tool.tar.gz', destination)
            self.assertEqual(destination.read_bytes(), b'previous complete download')


if __name__ == '__main__':
    unittest.main()
