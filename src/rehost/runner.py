"""Build a saved harness, execute a configured Java entry point, and preserve each Run."""

import os
import shutil
import sqlite3
import time
from pathlib import Path

from lightre.workspace import PROJECT_DIR
from .knowledge import ensure_guide, record_check
from .storage import execute, succeeded
from .storage import association, digest, exclusive, identifier, inventory, read_json, write_json
from .contract import Contract, artifact_path, baseline_files, case_path, case_request, cases, type_equal


def source_files(harness):
    """Instructions and Maven inputs, never target/, caches or observations."""
    files = {'pom.xml': digest(harness / 'pom.xml')}
    guide = harness / 'HARNESS.md'
    if guide.is_symlink():
        raise ValueError('HARNESS.md cannot be a symlink')
    if guide.exists():  # Old Run snapshots without a guide remain replayable.
        files['HARNESS.md'] = digest(guide)
    files.update({f'src/{name}': sha for name, sha in inventory(harness / 'src').items()})
    if (harness / 'pom.xml').is_symlink() or (harness / 'src').is_symlink():
        raise ValueError('Harness source roots cannot be symlinks')
    if not any(name.endswith('.java') for name in files):
        raise ValueError('No Java harness source found')
    return files


def compare(observation, wanted):
    if not isinstance(observation, dict):
        return ['Observation must be a JSON object']
    differences = []
    for key, value in wanted.items():
        actual = observation.get(key)
        if key not in observation or not type_equal(actual, value):
            differences.append(f'{key}: expected {value!r}, observed {actual!r}')
    return differences


def target_info(directory):
    """分析目录的固定 SO：校验工作区身份与 ARM64 ELF 头，返回路径和 SHA256。"""
    binary = directory / 'binary/original.so'
    with sqlite3.connect((directory / 'index.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key='sha256'").fetchone()
    sha = digest(binary)
    if row is None or sha != row[0]:
        raise ValueError('Original SO differs from its workspace identity')
    header = binary.read_bytes()[:20]
    if header[:6] != b'\x7fELF\x02\x01' or header[16:20] != b'\x03\x00\xb7\x00':
        raise ValueError('unidbg requires a little-endian ARM64 shared library')
    return {'path': str(binary), 'sha256': sha}


def run(directory, request=None, *, request_base=None, replay=None, timeout=120,
        cancelled=lambda: False, _lock_fds=None, _update_guide=True):
    """Every attempt has an independent record, including preflight failures."""
    directory = Path(directory).resolve()
    if directory.is_relative_to(PROJECT_DIR):
        raise ValueError('Run outputs must be outside the LightRE repository')
    root = directory / 'runs' / identifier()
    root.mkdir(parents=True)
    report = {'run': str(root), 'status': 'running', 'passed': False, 'started_at': time.time()}
    write_json(root / 'verification.json', report)
    try:
        harness, parameters = association(directory)
        if _lock_fds is None:
            with exclusive(directory, harness) as fds:
                _run(directory, harness, parameters, root, request, request_base, replay,
                     timeout, cancelled, fds, report)
                if replay is None and _update_guide:
                    record_check(harness, report, root / 'verification.json')
        else:
            _run(directory, harness, parameters, root, request, request_base, replay,
                 timeout, cancelled, _lock_fds, report)
    except Exception as error:  # Checker/import failures must also leave a failed Run.
        report.update(status='error', passed=False, error=str(error))
    except BaseException:
        report['status'] = 'interrupted'
        raise
    finally:
        report['finished_at'] = time.time()
        write_json(root / 'verification.json', report)
    return report


def _run(directory, harness, parameters, root, request, request_base, replay,
         timeout, cancelled, fds, report):
    binary = target_info(directory)
    source = root / 'harness'
    manifest = {'binary': binary, 'parameters': parameters, 'replay_of': None, 'contract_version': 1}
    old = None
    current_baseline = None
    if replay is not None:
        replay = Path(replay).resolve()
        old = read_json(replay / 'manifest.json')
        if old.get('contract_version') != 1:
            raise ValueError('Legacy Run lacks a saved explicit checker contract; create a new Run after configuration')
        if binary != old['binary']:
            raise ValueError('Replay SO path or content differs from saved Run')
        if source_files(replay / 'harness') != old['harness_files']:
            raise ValueError('Saved harness sources have changed')
        if digest(replay / 'input.bin') != old['input_sha256'] or digest(replay / 'request.json') != old['request_sha256']:
            raise ValueError('Saved request or input has changed')
        if digest(replay / 'expected.json') != old['expected_sha256']:
            raise ValueError('Saved expected results have changed')
        for name, sha in old['verification_files'].items():
            if name not in ('contract.json', 'checker.py') or digest(replay / name) != sha:
                raise ValueError(f'Saved {"target contract" if name == "contract.json" else "checker"} has changed')
        if 'contract.json' not in old['verification_files']:
            raise ValueError('Missing saved verification contract')
        spec = read_json(replay / 'contract.json')
        if bool(spec.get('checker')) != ('checker.py' in old['verification_files']):
            raise ValueError('Saved checker declaration differs from its manifest')
        contract = Contract.load(replay).save(root)
        if contract.observing and digest(replay / 'observation.json') != old.get('observation_sha256'):
            raise ValueError('Saved observation has changed')
        if contract.spec['parameters'] != old['parameters']:
            raise ValueError('Saved invocation differs from its contract')
        if set(old.get('artifacts', {})) != set(contract.spec['artifacts']):
            raise ValueError('Saved artifact manifest differs from its contract')
        for name, sha in old.get('artifacts', {}).items():
            if digest(artifact_path(replay, name)) != sha:
                raise ValueError('Saved target artifacts have changed')
        harness = replay / 'harness'
        normalized, data, _, recomputed = contract.prepare(read_json(replay / 'request.json'), replay)
        wanted = read_json(replay / 'expected.json')
        if not type_equal(wanted, recomputed):
            raise ValueError('Saved expected results differ from checker contract')
        manifest.update(replay_of=str(replay), input_origin=old.get('input_origin'))
    else:
        ensure_guide(harness, directory.name)
        current_baseline = baseline_files(directory)
        contract = Contract.current(directory).save(root)
        if request is None:
            request = case_request(directory)
        normalized, data, origin, wanted = contract.prepare(request, request_base)
        manifest['input_origin'] = origin
        if baseline_files(directory) != current_baseline:
            raise RuntimeError('Target configuration, cases or checker changed while saving the contract')
    parameters = contract.spec['parameters']
    manifest.update(parameters=parameters, verification_files=contract.hashes(root))
    files = source_files(harness)
    for name in files:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(harness / name, target)
    if source_files(harness) != files or source_files(source) != files:
        raise RuntimeError('Harness changed while copying its snapshot')
    (root / 'input.bin').write_bytes(data)
    write_json(root / 'request.json', normalized)
    manifest.update(harness_files=files, input_sha256=digest(root / 'input.bin'),
                    request_sha256=digest(root / 'request.json'))
    write_json(root / 'manifest.json', manifest)
    write_json(root / 'expected.json', wanted)
    manifest['expected_sha256'] = digest(root / 'expected.json')
    write_json(root / 'manifest.json', manifest)
    report['expected'] = wanted
    for tool in ('java', 'mvn'):
        record = execute([tool, '-version'], source, root / f'versions/{tool}', timeout=10,
                         cancelled=cancelled, pass_fds=fds)
        if not succeeded(record):
            report.update(status=record['status'], error=f'{tool} unavailable; see versions/{tool}')
            return
    build = execute(['mvn', '-B', '-ntp', 'compile', 'dependency:build-classpath',
                     '-Dmdep.outputFile=classpath.txt'], source, root / 'build', timeout=timeout,
                    cancelled=cancelled, pass_fds=fds)
    report['build'] = build
    if not succeeded(build):
        report.update(status='cancelled' if build['status'] == 'cancelled' else 'build_failed',
                      error=f'Harness build failed; see {root / "build"}')
        return
    jars = [Path(item) for item in (source / 'classpath.txt').read_text().strip().split(os.pathsep)]
    dependencies = [{'path': str(path), 'sha256': digest(path)} for path in jars]
    manifest['dependencies'] = dependencies
    write_json(root / 'manifest.json', manifest)
    if old is not None and dependencies != old.get('dependencies'):
        raise ValueError('Resolved runtime dependencies differ from saved Run')
    command = ['java', '-Dorg.slf4j.simpleLogger.defaultLogLevel=warn', '-cp',
               os.pathsep.join([str(source / 'target/classes'), *(str(path) for path in jars)]),
               parameters['main_class'], binary['path'],
               str(root / 'request.json'), str(root / 'observation.json')]
    execution = execute(command, source, root / 'execute', timeout=timeout,
                        cancelled=cancelled, pass_fds=fds)
    report['execution'] = execution
    if not succeeded(execution):
        report.update(status='cancelled' if execution['status'] == 'cancelled' else 'execution_failed',
                      error=f'Harness execution failed; see {root / "execute"}')
        return
    try:
        observation = read_json(root / 'observation.json')
    except (OSError, ValueError) as error:
        report.update(status='observation_failed', error=str(error))
        return
    differences = compare(observation, wanted)
    differences.extend(contract.validate(observation, root))
    manifest['observation_sha256'] = digest(root / 'observation.json')
    if old is not None and contract.observing and not type_equal(observation, read_json(replay / 'observation.json')):
        differences.append('Observed results differ from saved Run')
    artifacts = {}
    for name in contract.spec['artifacts']:
        path = artifact_path(root, name)
        if not path.is_file():
            differences.append(f'Missing declared artifact: {name}')
        else:
            artifacts[name] = digest(path)
    manifest['artifacts'] = artifacts
    if old is not None and artifacts != old.get('artifacts', {}):
        differences.append('Target artifacts differ from saved Run')
    write_json(root / 'manifest.json', manifest)
    # Build plugins and the harness must not modify the inputs being verified.
    if source_files(source) != files or digest(binary['path']) != binary['sha256']:
        differences.append('Harness source or target SO changed during execution')
    if (digest(root / 'request.json') != manifest['request_sha256']
            or digest(root / 'input.bin') != manifest['input_sha256']
            or digest(root / 'expected.json') != manifest['expected_sha256']
            or contract.hashes(root) != manifest['verification_files']
            or (current_baseline is not None and baseline_files(directory) != current_baseline)
            or any(digest(item['path']) != item['sha256'] for item in dependencies)):
        differences.append('Request, input or runtime dependencies changed during execution')
    observed = contract.observing and not differences
    report.update(observation=observation, differences=differences,
                  status='mismatch' if differences else ('observed' if observed else 'passed'),
                  passed=not differences and not contract.observing, observed=observed)


def check(directory, *, timeout=120, cancelled=lambda: False):
    """Run the declared target cases serially under the same SO/harness locks."""
    directory = Path(directory).resolve()
    harness, _ = association(directory)
    root = directory / 'runs' / ('suite-' + identifier())
    root.mkdir(parents=True)
    report = {'run': str(root), 'status': 'checking', 'passed': False,
              'runs': [], 'started_at': time.time()}
    try:
        with exclusive(directory, harness) as fds:
            ensure_guide(harness, directory.name)
            before = source_files(harness)
            baseline = baseline_files(directory)
            report['baseline'] = baseline
            rows = cases(directory)
            for row in rows:
                if cancelled():
                    report['status'] = 'cancelled'
                    break
                result = run(directory, row['request'], request_base=case_path(directory).parent,
                             timeout=timeout, cancelled=cancelled, _lock_fds=fds)
                report['runs'].append({'case': row['id'], **result})
                write_json(root / 'verification.json', report)
            else:
                report['passed'] = all(item['passed'] for item in report['runs'])
                report['observed'] = all(item.get('observed') for item in report['runs'])
                report['status'] = 'passed' if report['passed'] else ('observed' if report['observed'] else 'failed')
            if baseline_files(directory) != baseline:
                raise RuntimeError('Target configuration or cases changed during verification')
            if source_files(harness) != before:
                raise RuntimeError('Editable harness changed during target verification')
            record_check(harness, report, root / 'verification.json')
            report['harness_files'] = source_files(harness)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        report.update(status='error', passed=False, error=str(error))
    finally:
        report['finished_at'] = time.time()
        write_json(root / 'verification.json', report)
    return report
