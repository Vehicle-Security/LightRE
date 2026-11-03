"""Directory exploration against a local Codex substitute; no model or analyzer."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lightre.workspace import explore_directory


class TargetTests(unittest.TestCase):
    def test_multi_library_exploration_uses_packaged_prompt_and_collects_structured_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'input with spaces'
            source.mkdir()
            workspace = root / 'output'
            header = bytearray(64)
            header[:6] = b'\x7fELF\x02\x01'
            header[18:20] = (183).to_bytes(2, 'little')
            for name in ('libmain.so', 'libsupport.so'):
                (source / name).write_bytes(header)
            executable = root / 'fake-codex'
            executable.write_text(f'#!{sys.executable}\n' + '''
import json, sys
from pathlib import Path
args = sys.argv[1:]
assert args[args.index('--sandbox') + 1] == 'read-only'
schema = json.loads(Path(args[args.index('--output-schema') + 1]).read_text())
assert set(schema['properties']['binary']['enum']) == {'libmain.so', 'libsupport.so'}
prompt = sys.stdin.read()
assert 'DT_NEEDED' in prompt and 'libsupport.so' in prompt
Path(args[args.index('-o') + 1]).write_text(json.dumps({
    'binary': 'libmain.so', 'dependencies': ['libsupport.so'], 'reason': 'test dependency evidence'}))
print('{"type":"turn.completed"}', flush=True)
''')
            executable.chmod(0o755)
            with patch.dict(os.environ, {'LIGHTRE_CODEX_BIN': str(executable)}):
                selected = explore_directory(source, workspace)
            self.assertEqual(selected['binary'], 'libmain.so')
            self.assertEqual(selected['dependencies'], ['libsupport.so'])
            output = next((workspace / 'exploration').iterdir())
            execution = json.loads((output / 'execution.json').read_text())
            self.assertEqual(execution['status'], 'exited', execution)
            self.assertTrue((output / 'result.md').is_file())
            self.assertEqual({path.name for path in source.iterdir()}, {'libmain.so', 'libsupport.so'})


if __name__ == '__main__':
    unittest.main()
