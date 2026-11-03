"""Run the shell launcher with isolated tool stubs; no apt or network access."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


LAUNCHER = Path(__file__).resolve().parents[1] / 'lightre'


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.user_bin = self.root / 'user-bin'
        self.project = self.root / 'project with spaces'
        self.project.mkdir()
        self.launcher = self.project / 'lightre'
        shutil.copy2(LAUNCHER, self.launcher)
        for command in ('dirname', 'mktemp', 'rm', 'sh', 'mkdir', 'cp'):
            (self.bin / command).symlink_to(shutil.which(command))
        self.script(self.bin / 'id', "printf '0\\n'\n")
        self.log = self.root / 'commands.log'
        self.arguments = self.root / 'uv-arguments'
        self.uv = self.root / 'fake-uv'
        self.script(self.uv, 'printf "%s\\n" "$@" > "$LIGHTRE_TEST_ARGUMENTS"\n')
        self.installer = self.root / 'uv-installer'
        self.installer.write_text(
            'mkdir -p "$UV_INSTALL_DIR"\n'
            'cp "$LIGHTRE_TEST_UV" "$UV_INSTALL_DIR/uv"\n')
        self.curl = self.root / 'fake-curl'
        self.script(self.curl,
                    'printf "curl\\n" >> "$LIGHTRE_TEST_LOG"\n'
                    'while [ "$1" != -o ]; do shift; done\n'
                    'cp "$LIGHTRE_TEST_INSTALLER" "$2"\n')
        self.script(self.bin / 'apt-get',
                    'printf "apt-get %s\\n" "$*" >> "$LIGHTRE_TEST_LOG"\n'
                    'cp "$LIGHTRE_TEST_CURL" "$LIGHTRE_TEST_BIN/curl"\n')
        self.environment = {
            **os.environ, 'PATH': str(self.bin), 'XDG_BIN_HOME': str(self.user_bin),
            'LIGHTRE_UV_BIN': '', 'LIGHTRE_AUTO_INSTALL_DEPS': '1',
            'LIGHTRE_TEST_LOG': str(self.log), 'LIGHTRE_TEST_ARGUMENTS': str(self.arguments),
            'LIGHTRE_TEST_UV': str(self.uv), 'LIGHTRE_TEST_INSTALLER': str(self.installer),
            'LIGHTRE_TEST_CURL': str(self.curl), 'LIGHTRE_TEST_BIN': str(self.bin),
        }

    def script(self, path, body):
        path.write_text('#!/bin/sh\nset -eu\n' + body)
        path.chmod(0o755)

    def launch(self, *arguments):
        return subprocess.run([str(self.launcher), *arguments], cwd=self.root,
                              env=self.environment, text=True, capture_output=True, timeout=10)

    def test_no_curl_or_uv_installs_prerequisites_then_forwards_arguments(self):
        result = self.launch('../target with spaces', '--port', '9000')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text().splitlines()
        self.assertEqual(calls[0], 'apt-get update')
        self.assertIn('ca-certificates curl tar gzip', calls[1])
        self.assertEqual(calls[2], 'curl')
        self.assertEqual(self.arguments.read_text().splitlines(), [
            'run', '--project', str(self.project), '--locked', '--python', '3.12',
            'lightre', '../target with spaces', '--port', '9000',
        ])
        self.assertTrue((self.user_bin / 'uv').is_file())

    def test_configured_uv_skips_bootstrap_installation(self):
        self.environment['LIGHTRE_UV_BIN'] = str(self.uv)
        result = self.launch('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        self.assertEqual(self.arguments.read_text().splitlines()[-1], '--help')

    def test_disabled_installation_does_not_run_package_manager_or_download(self):
        self.environment['LIGHTRE_AUTO_INSTALL_DEPS'] = '0'
        result = self.launch('--prepare')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('自动安装已禁用', result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.arguments.exists())

    def test_failed_download_does_not_execute_installer(self):
        self.script(self.bin / 'curl', 'exit 22\n')
        result = self.launch('--prepare')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.user_bin / 'uv').exists())
        self.assertFalse(self.arguments.exists())


if __name__ == '__main__':
    unittest.main()
