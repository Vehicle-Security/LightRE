"""Explicit cases and optional standalone checker; no target-name dispatch.

Checker files are trusted verification code, separate from the editable harness.
They use only the standard library and expose normalize(request, base),
expected(normalized, data, rows), and validate(observation, run_root).
Each Run saves the checker, cases, invocation and declared artifact paths.
"""

import hashlib
import shutil
import types
from pathlib import Path

from .storage import digest, read_json, write_json


def case_path(directory):
    directory = Path(directory)
    config = read_json(directory / 'harness.json')
    if 'cases' not in config:
        raise ValueError('Missing explicit cases in harness.json; configure this harness first')
    return (directory / config['cases']).resolve()


def cases(directory):
    rows = read_json(case_path(directory))
    validate_cases(rows)
    return rows


def validate_cases(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('No configured target cases')
    names = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not isinstance(row.get('request'), dict):
            raise ValueError('Each case requires a string id and request object')
        if not row['id'] or row['id'] in names:
            raise ValueError('Case ids must be nonempty and unique')
        names.add(row['id'])


def case_request(directory, name=None):
    for row in cases(directory):
        if name is None or row['id'] == name:
            request = dict(row['request'])
            if 'input_file' in request:
                request['input_file'] = str((case_path(directory).parent / request['input_file']).resolve())
            return request
    raise ValueError(f'Unknown case: {name}')


def normalize_request(request, base=None):
    """Opaque JSON parameters plus an optional binary input; ABI belongs to checker."""
    if not isinstance(request, dict):
        raise ValueError('Request must be a JSON object')
    request = dict(request)
    if 'input_hex' in request and 'input_file' in request:
        raise ValueError('Provide at most one of input_file or input_hex')
    origin = None
    if 'input_file' in request:
        path = (Path(base or Path.cwd()) / request.pop('input_file')).resolve()
        data = path.read_bytes()
        origin = {'path': str(path), 'sha256': digest(path)}
        request['input_file'] = 'input.bin'
    elif 'input_hex' in request:
        data = bytes.fromhex(request.pop('input_hex'))
        request['input_file'] = 'input.bin'
    else:
        data = b''
    return request, data, origin


def expected(request, data, rows):
    sha = hashlib.sha256(data).hexdigest()
    parameters = {k: v for k, v in request.items() if k != 'input_file'}
    for row in rows:
        saved = {k: v for k, v in row['request'].items() if k not in ('input_file', 'input_hex')}
        # Exact types matter: bool is not an integer observation/parameter.
        if type_equal(parameters, saved) and row.get('input_sha256') == sha:
            return row.get('expected')
    raise ValueError('No expected result for this input/parameter combination; configure a target case first')


def type_equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(type_equal(v, right[k]) for k, v in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(type_equal(a, b) for a, b in zip(left, right))
    return left == right


def artifact_path(root, name):
    if not isinstance(name, str) or not name:
        raise ValueError('Artifact paths must be nonempty strings')
    path = Path(name)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Artifact paths must be relative paths within the Run')
    reserved = {'harness', 'build', 'execute', 'versions', 'input.bin', 'request.json',
                'observation.json', 'expected.json', 'manifest.json', 'verification.json',
                'contract.json', 'checker.py'}
    if path.parts[0] in reserved:
        raise ValueError('Artifact path conflicts with a reserved Run file')
    full = Path(root) / path
    if any(part.is_symlink() for part in [full, *full.parents]):
        raise ValueError('Artifact paths cannot contain symlinks')
    return full


class Contract:
    def __init__(self, spec, rows, checker=None):
        self.spec, self.rows, self.checker = spec, rows, checker
        validate_cases(rows)
        self.observing = spec.get('verification', 'expected') == 'observe'
        if spec.get('verification', 'expected') not in ('observe', 'expected'):
            raise ValueError('verification must be expected or observe')
        if self.observing and checker is not None:
            raise ValueError('Observation-only runs cannot claim a fixed checker verdict')
        parameters = spec.get('parameters', {})
        if not isinstance(parameters, dict) or not isinstance(parameters.get('main_class'), str) or not parameters['main_class'].strip():
            raise ValueError('Configure an explicit parameters.main_class')
        artifacts = spec.get('artifacts', [])
        if not isinstance(artifacts, list) or any(not isinstance(name, str) for name in artifacts) or len(set(artifacts)) != len(artifacts):
            raise ValueError('artifacts must be a list of unique relative paths')
        for name in artifacts:
            artifact_path(Path('/tmp/lightre-artifact-validation'), name)
        self.module = None
        if checker is not None:
            self.module = types.ModuleType('lightre_saved_checker')
            self.module.__file__ = str(checker)
            # Read bytes directly: no stale bytecode or mutable import dependencies.
            exec(compile(Path(checker).read_bytes(), str(checker), 'exec'), self.module.__dict__)
            for name in ('normalize', 'expected', 'validate'):
                if not callable(getattr(self.module, name, None)):
                    raise ValueError(f'Checker must define {name}()')

    @classmethod
    def current(cls, directory):
        directory = Path(directory)
        config = read_json(directory / 'harness.json')
        checker = (directory / config['checker']).resolve() if config.get('checker') else None
        harness = (directory / config['path']).resolve()
        if checker and checker.is_relative_to(harness):
            raise ValueError('Fixed checker must be outside the editable harness')
        if case_path(directory).is_relative_to(harness):
            raise ValueError('Fixed cases must be outside the editable harness')
        spec = {'parameters': config.get('parameters', {}), 'artifacts': config.get('artifacts', []),
                'checker': 'checker.py' if checker else None}
        if 'verification' in config:
            spec['verification'] = config['verification']
        return cls(spec, cases(directory), checker)

    def save(self, root):
        if self.checker:
            shutil.copyfile(self.checker, Path(root) / 'checker.py')
        write_json(Path(root) / 'contract.json', {**self.spec, 'cases': self.rows})
        return self.load(root)

    @classmethod
    def load(cls, root):
        root = Path(root)
        spec = read_json(root / 'contract.json')
        if spec.get('checker') not in (None, 'checker.py'):
            raise ValueError('Invalid saved checker path')
        return cls(spec, spec['cases'], root / 'checker.py' if spec.get('checker') else None)

    def prepare(self, request, base=None):
        normalizer = self.module.normalize if self.module else normalize_request
        normalized, data, origin = normalizer(request, base)
        if self.observing:
            # Inputs stay fixed even when no independent oracle is available.
            sha = hashlib.sha256(data).hexdigest()
            parameters = {k: v for k, v in normalized.items() if k != 'input_file'}
            if not any(row.get('input_sha256') == sha and type_equal(parameters, {
                    k: v for k, v in row['request'].items() if k not in ('input_file', 'input_hex')
            }) for row in self.rows):
                raise ValueError('Observation input differs from the fixed task seeds')
            wanted = {}
        else:
            wanted = (self.module.expected if self.module else expected)(normalized, data, self.rows)
        if not isinstance(wanted, dict) or (not wanted and not self.observing):
            raise ValueError('Expected result must be a nonempty object')
        return normalized, data, origin, wanted

    def validate(self, observation, root):
        if self.observing:
            if not isinstance(observation, dict):
                return ['Observation must be a JSON object']
            differences = []
            if observation.get('status') != 'returned':
                differences.append('Entry has not reported a completed native call')
            if not isinstance(observation.get('entry'), str) or not observation['entry'].strip():
                differences.append('Missing investigated entry description')
            calls = observation.get('native_calls')
            if type(calls) is not int or calls < 1:
                differences.append('No native calls reported')
            if observation.get('missing_environment') != []:
                differences.append('Environment gaps remain; inspect runtime logs and HARNESS.md')
            return differences
        differences = self.module.validate(observation, root) if self.module else []
        if not isinstance(differences, list) or any(not isinstance(item, str) for item in differences):
            raise ValueError('Checker validate() must return a list of differences')
        return differences

    def hashes(self, root):
        files = ['contract.json'] + (['checker.py'] if self.checker else [])
        return {name: digest(Path(root) / name) for name in files}


def baseline_files(directory):
    directory = Path(directory)
    config = read_json(directory / 'harness.json')
    paths = [directory / 'harness.json', case_path(directory)]
    if config.get('checker'):
        paths.append((directory / config['checker']).resolve())
    return {str(path): digest(path) for path in paths}


def configure(target, *, harness, cases_file, main_class, checker=None, artifacts=(), workspace=None,
              instructions=None, verification='expected'):
    """Associate any Maven harness with an explicit verification contract."""
    from lightre.workspace import PROJECT_DIR, prepare_workspace
    from .knowledge import ensure_guide
    from .runner import target_info
    from .storage import exclusive
    target, harness = Path(target).resolve(), Path(harness).resolve()
    if target.is_file():
        if workspace is None:
            raise ValueError('Importing a new SO requires --workspace')
        target = prepare_workspace(target, Path(workspace), name=target.stem)
    if target.is_relative_to(PROJECT_DIR) or harness.is_relative_to(PROJECT_DIR):
        raise ValueError('Use target and editable harness paths outside the LightRE repository')
    target_info(target)
    config = {'path': str(harness), 'parameters': {'main_class': main_class},
              'cases': str(Path(cases_file).resolve()), 'artifacts': list(artifacts)}
    if verification != 'expected':
        config['verification'] = verification
    if checker:
        config['checker'] = str(Path(checker).resolve())
        if Path(config['checker']).is_relative_to(harness):
            raise ValueError('Fixed checker must be outside the editable harness')
    if Path(config['cases']).is_relative_to(harness):
        raise ValueError('Fixed cases must be outside the editable harness')
    harness.mkdir(parents=True, exist_ok=True)
    with exclusive(target, harness):
        ensure_guide(harness, target.name, instructions=instructions)
        Contract({'parameters': config['parameters'], 'artifacts': config['artifacts'],
                  'verification': verification},
                 read_json(config['cases']), Path(config['checker']) if checker else None)
        path = target / 'harness.json'
        if path.exists() and read_json(path) != config:
            raise ValueError('Existing association differs; review it explicitly before replacing it')
        write_json(path, config)
    return target
