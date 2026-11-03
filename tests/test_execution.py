"""Temporary files and external-process stand-ins; no Java, compiler or model."""

import os
import hashlib
import runpy
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rehost.workflow import run_rounds as repair
from rehost.guide import ensure
from rehost.storage import execute
from rehost.runner import run, check
from rehost.contract import configure
from lightre.workspace import PROJECT_DIR
DG_NATIVE_SIGNATURES = runpy.run_path(str(PROJECT_DIR / "tests/fixtures/execution/dg_checker.py"))["DG_NATIVE_SIGNATURES"]
from rehost.storage import digest, exclusive, read_json, write_json
from recovery.queue import snapshot


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.so = self.root / 'sample'
        (self.so / 'binary').mkdir(parents=True)
        (self.so / 'binary/original.so').write_bytes(b'\x7fELF\x02\x01' + b'\0'*10 + b'\x03\x00\xb7\x00')
        with sqlite3.connect(self.so / 'index.sqlite') as conn:
            conn.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)')
            conn.execute('INSERT INTO metadata VALUES (?,?)', ('sha256', digest(self.so / 'binary/original.so')))
        self.harness = self.root / 'harness'
        (self.harness / 'src').mkdir(parents=True)
        (self.harness / 'pom.xml').write_text('<project/>')
        self.java = self.harness / 'src/Main.java'
        self.java.write_text('FIXED')
        write_json(self.so / 'harness.json', {
            'path': str(self.harness), 'parameters': {'main_class': 'lightre.NativeHarness'},
            'cases': str(PROJECT_DIR / 'tests/fixtures/execution/native_smoke_cases.json'),
            'checker': str(PROJECT_DIR / 'tests/fixtures/execution/native_smoke_checker.py'), 'artifacts': []})
        ensure(self.harness, 'native-smoke', instructions=PROJECT_DIR / 'tests/fixtures/execution/adaptations/native_smoke.md')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.jar = self.root / 'dependency.jar'
        self.jar.write_bytes(b'fixed runtime dependency')
        self.tool('mvn', f'''
from pathlib import Path
if '-version' not in sys.argv:
    if 'COMPILE_ERROR' in Path('src/Main.java').read_text(): sys.exit(2)
    Path('classpath.txt').write_text({str(self.jar)!r})
''')
        self.tool('java', '''
import json
from pathlib import Path
if '-version' not in sys.argv:
    source=Path('src/Main.java').read_text()
    if 'MISSING' in source: raise RuntimeError('Missing export: smoke_arithmetic')
    request=Path(sys.argv[-2]); r=json.loads(request.read_text())
    data=(request.parent / r['input_file']).read_bytes()
    n=len(data) if r['capacity']>=len(data) else -1
    output=bytes(x ^ ((r['key']+i)&255) for i,x in enumerate(data)) if n>=0 else b''
    result={'integer':r['a']*3-r['b']+(1 if 'BROKEN' in source else 0),'length':n,'output_hex':output.hex()}
    Path(sys.argv[-1]).write_text(json.dumps(result))
''')
        self.tool('codex', '''
import os, time
from pathlib import Path
prompt=sys.stdin.read(); mode=os.environ.get('FAKE_REPAIR','fix')
assert '先读并适配说明：' in prompt
guide=Path('HARNESS.md'); instructions=guide.read_text()
assert 'smoke_arithmetic' in instructions
if mode=='slow': time.sleep(10)
if mode=='fail': sys.exit(2)
if mode=='empty': sys.exit(0)
if mode=='tamper':
    for line in prompt.splitlines():
        if line.startswith('只读契约与固定用例：'):
            Path(line.split('：',1)[1]).write_text('{}')
if mode=='mislead' and '上轮完整返回' not in prompt: pass
else:
    source=Path('src/Main.java')
    source.write_text(source.read_text().replace('BROKEN','FIXED'))
    guide.write_text(instructions + '\\n适配记录：已按固定 ABI 核对 Java 参数，等待外部重新检查。\\n')
Path(sys.argv[sys.argv.index('-o')+1]).write_text('完成。\\n完整原文，不能只信此声明。\\n')
print('{"type":"turn.completed"}')
''')
        self.enterContext(patch.dict(os.environ, {'PATH': str(self.bin)+os.pathsep+os.environ['PATH'],
                                                'LIGHTRE_CODEX_BIN': str(self.bin/'codex')}))
        os.environ.pop('LIGHTRE_HARNESS_SANDBOX', None)
        os.environ.pop('LIGHTRE_CODEX_SANDBOX', None)

    def tool(self, name, code):
        path = self.bin / name
        path.write_text(f'#!{sys.executable}\nimport sys\n' + code)
        path.chmod(0o755)

    def test_file_input_and_snapshot_replay_after_edit(self):
        original = self.root / 'seed.bin'
        original.write_bytes(b'\x00\xff\x82hello')
        report = run(self.so, {'a': -17, 'b': 9, 'key': 254, 'input_file': str(original)})
        self.assertTrue(report['passed'], report)
        saved = Path(report['run'])
        self.assertEqual((saved/'input.bin').read_bytes(), original.read_bytes())
        self.assertEqual(read_json(saved/'manifest.json')['input_origin']['sha256'], digest(original))
        guide_snapshot = (saved/'harness/HARNESS.md').read_text()
        self.assertIn('smoke_arithmetic', guide_snapshot)
        self.assertIn(str(saved/'verification.json'), (self.harness/'HARNESS.md').read_text())
        original.unlink()
        self.java.write_text('BROKEN')
        (self.harness/'HARNESS.md').write_text('当前已改动的说明，不适用于旧 Run。')
        replay = run(self.so, replay=saved)
        self.assertTrue(replay['passed'], replay)
        self.assertEqual(replay['observation'], report['observation'])
        self.assertEqual(self.java.read_text(), 'BROKEN')
        self.assertEqual((Path(replay['run'])/'harness/HARNESS.md').read_text(), guide_snapshot)
        self.assertEqual((self.harness/'HARNESS.md').read_text(), '当前已改动的说明，不适用于旧 Run。')

    def test_guide_profiles_preserve_adaptations_and_do_not_claim_target_support(self):
        for kind in ('native-smoke', 'dg', 'minilua'):
            guide = ensure(self.root/kind, kind, instructions=PROJECT_DIR / f'tests/fixtures/execution/adaptations/{kind.replace("native-smoke", "native_smoke")}.md')
            text = guide.read_text()
            self.assertIn('尚未检查', text)
            if kind == 'dg':
                self.assertIn('需要 JNI', text)
                self.assertIn('0x1c07c', text)
            elif kind == 'minilua':
                self.assertIn('不实现 JNI', text)
                self.assertIn('samples/minilua', text)
                self.assertIn('glibc', text)
            guide.write_text(text + '\n用户已经补充的接口和适配依据。\n')
            self.assertEqual(ensure(self.root/kind, kind).read_text(), text + '\n用户已经补充的接口和适配依据。\n')
        generic = ensure(self.root/'new-target', 'any-new-library')
        self.assertIn('any-new-library', generic.read_text())
        self.assertNotIn('smoke_arithmetic', generic.read_text())

    def test_legacy_run_without_guide_replays_without_modifying_saved_inputs(self):
        first = run(self.so)
        saved = Path(first['run'])
        # Construct the shape of a pre-HARNESS.md Run in this temporary fixture.
        (saved/'harness/HARNESS.md').unlink()
        manifest = read_json(saved/'manifest.json')
        del manifest['harness_files']['HARNESS.md']
        write_json(saved/'manifest.json', manifest)
        replay = run(self.so, replay=saved)
        self.assertTrue(replay['passed'], replay)
        self.assertFalse((saved/'harness/HARNESS.md').exists())
        self.assertFalse((Path(replay['run'])/'harness/HARNESS.md').exists())

    def test_changed_saved_guide_and_symlinks_are_rejected(self):
        first = run(self.so)
        saved = Path(first['run'])
        (saved/'harness/HARNESS.md').write_text('改动历史说明')
        self.assertIn('sources have changed', run(self.so, replay=saved)['error'])
        (self.harness/'HARNESS.md').unlink()
        (self.harness/'HARNESS.md').symlink_to(saved/'harness/HARNESS.md')
        self.assertIn('symlink', run(self.so)['error'])

    def test_missing_explicit_contract_is_rejected_without_sample_fallback(self):
        for kind in ('dg', 'minilua'):
            write_json(self.so/'harness.json', {'path': str(self.harness), 'kind': kind})
            self.assertIn('Missing explicit cases', run(self.so)['error'])
            with self.assertRaisesRegex(ValueError, 'Missing explicit cases'):
                repair(self.so)

    def minilua_contract(self):
        script = self.root/'case.lua'
        script.write_bytes(b'real source fixture')
        request = {'input_file': str(script), 'chunk_name': 'tests/case.lua'}
        write_json(self.so/'harness.json', {'path': str(self.harness), 'cases': 'harness-cases.json',
                                           'checker': str(PROJECT_DIR / 'tests/fixtures/execution/minilua_checker.py'),
                                           'artifacts': ['proto.txt'],
                                           'parameters': {'main_class': 'lightre.MiniluaHarness'}})
        write_json(self.so/'harness-cases.json', [
            {'id': 'fixture', 'input_sha256': digest(script), 'request': request,
             'expected': {'lua_status': 0, 'exit_code': 0, 'stdout': 'real source fixture', 'stderr': ''}}])
        self.tool('java', '''
import json
from pathlib import Path
if '-version' not in sys.argv:
    request=Path(sys.argv[-2]); r=json.loads(request.read_text())
    source=(request.parent/r['input_file']).read_text()
    output=Path(sys.argv[-1]); proto=output.parent/'proto.txt'
    proto.write_text('P 0 parent=-1 code=1 constants=0 children=0\\nI 0 0 00000000 line=1\\n')
    output.write_text(json.dumps({'lua_status':0,'exit_code':0,'stdout':source,'stderr':'',
                                 'vm_entries':1,'proto_bytes':proto.stat().st_size,'proto_file':'proto.txt'}))
''')
        return script

    def test_target_contract_and_proto_survive_missing_current_inputs_on_replay(self):
        script = self.minilua_contract()
        first = run(self.so)
        self.assertTrue(first['passed'], first)
        saved = Path(first['run'])
        manifest = read_json(saved/'manifest.json')
        self.assertEqual(manifest['parameters']['main_class'], 'lightre.MiniluaHarness')
        self.assertEqual(manifest['artifacts']['proto.txt'], digest(saved/'proto.txt'))
        self.assertTrue((saved/'contract.json').is_file())
        script.unlink()
        (self.so/'harness-cases.json').unlink()
        replay = run(self.so, replay=saved)
        self.assertTrue(replay['passed'], replay)
        self.assertEqual(replay['observation'], first['observation'])

    def test_target_expected_and_proto_tampering_block_replay(self):
        self.minilua_contract()
        for filename, message in [('expected.json', 'expected results'), ('proto.txt', 'artifacts')]:
            first = run(self.so)
            saved = Path(first['run'])
            (saved/filename).write_text('tampered')
            result = run(self.so, replay=saved)
            self.assertFalse(result['passed'], result)
            self.assertIn(message, result['error'])

    def test_target_check_uses_saved_contract_not_smoke_arithmetic(self):
        script = self.minilua_contract()
        report = check(self.so)
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['runs'][0]['case'], 'fixture')
        self.assertIn(str(Path(report['run'])/'verification.json'), (self.harness/'HARNESS.md').read_text())
        script.write_text('new input with no declared expected result')
        failed = run(self.so)
        self.assertFalse(failed['passed'], failed)
        self.assertIn('No expected result', failed['error'])

    def test_dg_binary_output_is_saved_and_changed_replay_is_rejected(self):
        seed = self.root/'dg.bin'
        seed.write_bytes(b'fixture bytecode')
        request = {'input_file': str(seed), 'stage': 'run', 'vm_entry_rva': '0x1c07c',
                   'environment_profile': 'headless-v1', 'runtime_gr': 0, 'runtime_i': 0,
                   'runtime_j': 0, 'runtime_h': '', 'runtime_m': '', 'runtime_k': '',
                   'random_seed': 0, 'monotonic_nanos': 1000000000, 'wall_time_millis': 1700000000000}
        write_json(self.so/'harness.json', {'path': str(self.harness), 'cases': 'harness-cases.json',
            'checker': str(PROJECT_DIR / 'tests/fixtures/execution/dg_checker.py'), 'artifacts': ['output.bin'],
            'parameters': {'main_class': 'lightre.DgHarness'}})
        write_json(self.so/'harness-cases.json', [
            {'id': 'dg', 'input_sha256': digest(seed), 'request': request,
             'expected': {'loaded': True, 'stage': 'run', 'handle_nonzero': True, 'closed': True,
                          'missing_jni': [], 'environment_profile': 'headless-v1'}}])
        tool = """
import json, os
from pathlib import Path
if '-version' not in sys.argv:
    output=Path(sys.argv[-1]); artifact=output.parent/'output.bin'
    data=os.environ.get('DG_OUTPUT','real fixture result').encode()
    artifact.write_bytes(data)
    output.write_text(json.dumps({'loaded':True,'stage':'run','handle_nonzero':True,'closed':True,
                                 'missing_jni':[],'environment_profile':'headless-v1',
                                 'vm_entries':3,'init_vm_entries':2,'ss_vm_entries':1,
                                 'registrations':REGISTRATIONS,'output_hex':data.hex(),'length':len(data)}))
"""
        self.tool('java', tool.replace('REGISTRATIONS', repr(dict.fromkeys(DG_NATIVE_SIGNATURES, '0x1000'))))
        first = run(self.so)
        self.assertTrue(first['passed'], first)
        saved = Path(first['run'])
        self.assertEqual(read_json(saved/'manifest.json')['artifacts']['output.bin'], digest(saved/'output.bin'))
        seed.unlink()
        self.assertTrue(run(self.so, replay=saved)['passed'])
        with patch.dict(os.environ, {'DG_OUTPUT': 'different but structurally valid result'}):
            changed = run(self.so, replay=saved)
        self.assertFalse(changed['passed'], changed)
        self.assertIn('Target artifacts differ from saved Run', changed['differences'])
        (saved/'output.bin').write_bytes(b'tampered')
        self.assertIn('artifacts have changed', run(self.so, replay=saved)['error'])

    def test_target_expected_contract_cannot_be_empty_and_saved_contract_is_checked(self):
        self.minilua_contract()
        first = run(self.so)
        saved = Path(first['run'])
        (saved/'contract.json').write_text('[]')
        self.assertIn('contract has changed', run(self.so, replay=saved)['error'])
        contract = read_json(self.so/'harness-cases.json')
        contract[0]['expected'] = {}
        write_json(self.so/'harness-cases.json', contract)
        self.assertIn('requires explicit Lua status', run(self.so)['error'])

    def test_suite_rejects_case_edits_during_execution(self):
        self.minilua_contract()
        java = self.bin/'java'
        java.write_text(java.read_text() + f"\nif '-version' not in sys.argv:\n    with open({str(self.so/'harness-cases.json')!r}, 'a') as contract: contract.write(' ')\n")
        result = check(self.so)
        self.assertFalse(result['passed'], result)
        self.assertIn('cases changed during verification', result['error'])

    def generic_contract(self):
        seed = self.root / 'generic.bin'
        seed.write_bytes(b'opaque input')
        rows = [{'id': 'custom', 'request': {'input_file': seed.name, 'mode': {'count': 5}},
                 'input_sha256': digest(seed), 'expected': {'answer': {'count': 5}}}]
        cases_file = self.root / 'custom-cases.json'
        write_json(cases_file, rows)
        (self.so / 'harness.json').unlink()
        configure(self.so, harness=self.harness, cases_file=cases_file,
                  main_class='arbitrary.CustomHarness', artifacts=['result.dat', 'nested/trace.json'])
        self.tool('java', '''
import json, os
from pathlib import Path
if '-version' not in sys.argv:
    assert 'arbitrary.CustomHarness' in sys.argv
    request=Path(sys.argv[-2]); r=json.loads(request.read_text())
    data=(request.parent/r['input_file']).read_bytes()
    assert data == b'opaque input'
    output=Path(sys.argv[-1]); root=output.parent
    (root/'result.dat').write_bytes(data[::-1])
    (root/'nested').mkdir()
    (root/'nested/trace.json').write_text('trace')
    output.write_text(json.dumps({'answer': r['mode']}))
''')
        return seed, cases_file

    def test_arbitrary_target_without_checker_runs_checks_repairs_and_replays(self):
        seed, cases_file = self.generic_contract()
        first = check(self.so)
        self.assertTrue(first['passed'], first)
        saved = Path(first['runs'][0]['run'])
        self.assertEqual(set(read_json(saved/'manifest.json')['artifacts']),
                         {'result.dat', 'nested/trace.json'})
        # The repair loop uses the same generic cases, with no target registry.
        repaired = repair(self.so)
        self.assertEqual(repaired['status'], 'complete', repaired)
        self.assertEqual(repaired['round'], 0)
        seed.unlink()
        cases_file.unlink()
        write_json(self.so/'harness.json', {'path': str(self.harness)})
        replay = run(self.so, replay=saved)
        self.assertTrue(replay['passed'], replay)
        self.assertEqual(replay['observation'], {'answer': {'count': 5}})
        self.assertFalse((Path(replay['run'])/'checker.py').exists())

    def test_generic_missing_artifact_and_nested_boolean_mismatch_fail(self):
        _, cases_file = self.generic_contract()
        java = self.bin/'java'
        content = java.read_text()
        java.write_text(content.replace("(root/'result.dat').write_bytes(data[::-1])", 'pass'))
        result = run(self.so)
        self.assertFalse(result['passed'], result)
        self.assertIn('Missing declared artifact: result.dat', result['differences'])
        rows = read_json(cases_file)
        rows[0]['expected'] = {'answer': {'count': 1}}
        write_json(cases_file, rows)
        java.write_text(content.replace("{'answer': r['mode']}", "{'answer': {'count': True}}"))
        result = run(self.so)
        self.assertFalse(result['passed'], result)
        self.assertIn('answer:', result['differences'][0])

    def test_generic_contract_rejects_unsafe_artifacts_and_empty_expectations(self):
        _, cases_file = self.generic_contract()
        config = read_json(self.so/'harness.json')
        for name in ('../escape', '/tmp/escape', 'input.bin', 'harness/other', '.'):
            write_json(self.so/'harness.json', {**config, 'artifacts': [name]})
            self.assertFalse(run(self.so)['passed'])
        write_json(self.so/'harness.json', config)
        rows = read_json(cases_file)
        rows[0]['expected'] = {}
        write_json(cases_file, rows)
        self.assertIn('nonempty object', run(self.so)['error'])

    def test_saved_checker_is_pinned_and_current_checker_is_not_used_for_replay(self):
        self.minilua_contract()
        checker = self.root / 'fixed-checker.py'
        checker.write_bytes((PROJECT_DIR/'tests/fixtures/execution/minilua_checker.py').read_bytes())
        config = read_json(self.so/'harness.json')
        write_json(self.so/'harness.json', {**config, 'checker': str(checker)})
        first = run(self.so)
        self.assertTrue(first['passed'], first)
        saved = Path(first['run'])
        checker.write_text('raise RuntimeError("new checker must not affect saved Run")')
        replay = run(self.so, replay=saved)
        self.assertTrue(replay['passed'], replay)
        (saved/'checker.py').write_text('raise RuntimeError("tampered")')
        self.assertIn('checker has changed', run(self.so, replay=saved)['error'])
        failed = run(self.so)
        self.assertEqual(failed['status'], 'error')
        self.assertIn('new checker', failed['error'])

    def test_checker_and_cases_cannot_be_edited_as_part_of_harness(self):
        self.minilua_contract()
        checker = self.harness/'checker.py'
        checker.write_bytes((PROJECT_DIR/'tests/fixtures/execution/minilua_checker.py').read_bytes())
        config = read_json(self.so/'harness.json')
        write_json(self.so/'harness.json', {**config, 'checker': str(checker)})
        self.assertIn('outside the editable harness', run(self.so)['error'])

    def test_generic_scalar_request_without_binary_input(self):
        _, cases_file = self.generic_contract()
        write_json(cases_file, [{'id': 'scalar', 'request': {'action': 'ping'},
                                'input_sha256': hashlib.sha256(b'').hexdigest(),
                                'expected': {'ok': True}}])
        config = read_json(self.so/'harness.json')
        write_json(self.so/'harness.json', {**config, 'artifacts': []})
        self.tool('java', '''
import json
from pathlib import Path
if '-version' not in sys.argv:
    request=json.loads(Path(sys.argv[-2]).read_text())
    assert request == {'action': 'ping'}
    Path(sys.argv[-1]).write_text(json.dumps({'ok': True}))
''')
        report = run(self.so)
        self.assertTrue(report['passed'], report)
        self.assertTrue(run(self.so, replay=report['run'])['passed'])
        rows = read_json(cases_file)
        rows[0]['expected']['optional'] = None
        write_json(cases_file, rows)
        missing = run(self.so)
        self.assertFalse(missing['passed'], missing)
        self.assertIn('optional:', missing['differences'][0])

    def test_configure_creates_instructions_for_a_new_arbitrary_harness(self):
        _, cases_file = self.generic_contract()
        harness = self.root / 'fresh-harness'
        (self.so / 'harness.json').unlink()
        configure(self.so, harness=harness, cases_file=cases_file, main_class='new.Target')
        self.assertTrue((harness / 'HARNESS.md').is_file())
        self.assertFalse((harness / 'src').exists())
        config = read_json(self.so / 'harness.json')
        self.assertEqual(config['parameters']['main_class'], 'new.Target')
        self.assertNotIn('checker', config)

    def test_changed_replay_or_binary_is_rejected(self):
        first = run(self.so)
        self.assertTrue(first['passed'], first)
        saved = Path(first['run'])
        (saved/'input.bin').write_bytes(b'tampered')
        self.assertIn('changed', run(self.so, replay=saved)['error'])
        (self.so/'binary/original.so').write_bytes(b'changed target')
        self.assertIn('identity', run(self.so)['error'])

    def test_changed_resolved_dependency_is_rejected_on_replay(self):
        first = run(self.so)
        self.jar.write_bytes(b'replaced dependency')
        report = run(self.so, replay=first['run'])
        self.assertFalse(report['passed'])
        self.assertIn('dependencies differ', report['error'])

    def test_build_export_and_missing_input_failures_are_saved(self):
        for content, status in [('COMPILE_ERROR', 'build_failed'), ('MISSING', 'execution_failed')]:
            self.java.write_text(content)
            report = run(self.so)
            self.assertFalse(report['passed'])
            self.assertEqual(report['status'], status)
            self.assertEqual(read_json(Path(report['run'])/'verification.json')['status'], status)
        report = run(self.so, {'a': 1, 'b': 0, 'key': 0, 'input_file': '/missing-seed.bin'})
        self.assertFalse(report['passed'])
        self.assertEqual(report['status'], 'error')

    def test_false_completion_continues_with_full_previous_record(self):
        self.java.write_text('BROKEN')
        with patch.dict(os.environ, {'FAKE_REPAIR': 'mislead'}):
            state = repair(self.so, max_rounds=2)
        self.assertEqual(state['status'], 'complete', state)
        task = Path(state['directory'])
        first = read_json(task/'round-0001/verification.json')
        self.assertFalse(first['passed'])
        self.assertEqual(first['status'], 'mismatch')
        prompt = (task/'round-0002/prompt.md').read_text()
        self.assertIn(str(self.harness/'HARNESS.md'), prompt)
        self.assertIn('先更新适配计划与依据再修代码', prompt)
        for filename in ('result.md', 'execution.json', 'verification.json'):
            self.assertIn(str(task/'round-0001'/filename), prompt)
        self.assertEqual((task/'round-0001/result.md').read_text(), '完成。\n完整原文，不能只信此声明。\n')
        self.assertTrue(read_json(task/'round-0002/verification.json')['passed'])
        verification = read_json(task/'round-0002/verification.json')
        self.assertEqual(verification['harness_files']['HARNESS.md'], digest(self.harness/'HARNESS.md'))
        self.assertIn(str(task/'round-0002/verification.json'), (self.harness/'HARNESS.md').read_text())
        for item in verification['runs']:
            self.assertIn('适配记录', (Path(item['run'])/'harness/HARNESS.md').read_text())
        self.assertTrue((task/'round-0001/events.jsonl').read_text().strip())
        command = read_json(task/'round-0001/execution.json')['command']
        self.assertEqual(command[command.index('--sandbox')+1], 'danger-full-access')
        self.assertEqual(snapshot(type('Reader', (), {'directory': self.so})()), [])

    def test_explicit_sandbox_override_is_preserved(self):
        self.java.write_text('BROKEN')
        with patch.dict(os.environ, {'LIGHTRE_HARNESS_SANDBOX': 'workspace-write'}):
            state = repair(self.so, max_rounds=1)
        self.assertEqual(state['status'], 'complete', state)
        command = read_json(Path(state['latest_round'])/'execution.json')['command']
        self.assertEqual(command[command.index('--sandbox')+1], 'workspace-write')

    def test_missing_final_reply_does_not_inherit_success_and_resume_rechecks(self):
        self.java.write_text('BROKEN')
        with patch.dict(os.environ, {'FAKE_REPAIR': 'empty'}):
            state = repair(self.so, max_rounds=3)
        self.assertEqual(state['status'], 'error')
        self.assertEqual(state['round'], 2)
        task = Path(state['directory'])
        self.assertFalse(read_json(task/'round-0001/execution.json')['result']['exists'])
        self.assertFalse(read_json(task/'round-0002/verification.json')['passed'])
        self.java.write_text('FIXED')
        resumed = repair(self.so, task=task)
        self.assertEqual(resumed['status'], 'complete', resumed)
        self.java.write_text('BROKEN')
        with patch.dict(os.environ, {'FAKE_REPAIR': 'fail'}):
            resumed = repair(self.so, task=task, max_rounds=1)
        self.assertEqual(resumed['status'], 'limit')
        self.assertFalse(read_json(resumed['latest_verification'])['passed'])

    def test_cancellation_timeout_and_writer_exclusion(self):
        record = execute([sys.executable, '-c', 'import time; time.sleep(10)'],
                         self.root, self.root/'deadline', timeout=0.1)
        self.assertEqual(record['status'], 'timeout')
        self.assertIsNotNone(record['exit_code'])
        with exclusive(self.so, self.harness):
            report = run(self.so)
            self.assertFalse(report['passed'])
            self.assertIn('Another writer', report['error'])
        self.java.write_text('BROKEN')
        with patch.dict(os.environ, {'FAKE_REPAIR': 'slow'}):
            state = repair(self.so, timeout=0.1, max_rounds=1)
        self.assertEqual(state['status'], 'limit')
        state = repair(self.so, task=state['directory'], cancelled=lambda: True)
        self.assertEqual(state['status'], 'cancelled')

    def test_inflight_cli_cancel_records_exit_and_resumes_same_task(self):
        self.java.write_text('BROKEN')
        stopped = False

        def cancel_inflight():
            nonlocal stopped
            for path in (self.so/'tasks').glob('*/round-*/execution.json'):
                if read_json(path)['status'] == 'running':
                    stopped = True
            return stopped

        with patch.dict(os.environ, {'FAKE_REPAIR': 'slow'}):
            state = repair(self.so, cancelled=cancel_inflight)
        self.assertEqual(state['status'], 'cancelled', state)
        task = Path(state['directory'])
        record = read_json(task/'round-0001/execution.json')
        self.assertEqual(record['status'], 'cancelled')
        self.assertIsNotNone(record['exit_code'])
        resumed = repair(self.so, task=task)
        self.assertEqual(resumed['status'], 'complete', resumed)
        self.assertTrue((task/'round-0002/prompt.md').is_file())

    def test_checker_rejects_model_changes_to_fixed_contract(self):
        self.java.write_text('BROKEN')
        with patch.dict(os.environ, {'FAKE_REPAIR': 'tamper'}):
            state = repair(self.so)
        self.assertEqual(state['status'], 'error', state)
        report = read_json(state['latest_verification'])
        self.assertFalse(report['passed'])
        self.assertEqual(report['status'], 'checker_error')
        self.assertIn('Fixed task/checker input changed', report['error'])


if __name__ == '__main__':
    unittest.main()
