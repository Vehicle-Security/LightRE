"""Fresh target projects, static inspection, and the shared execution feedback loop."""

import hashlib
import shutil
from contextlib import ExitStack
from pathlib import Path

from .contract import Contract
from .guide import ensure
from .loop import run_rounds
from .process import execute
from .runner import _target
from .storage import WriterBusy, digest, exclusive, identifier, read_json, write_json
from lightre.workspace import PROJECT_DIR, prepare_workspace
from lightre.prompt import render_prompt


WORKFLOWS = Path(__file__).parent / 'workflows/unidbg'
WORKFLOW = WORKFLOWS / 'WORKFLOW.md'
MAIN_CLASS = 'lightre.RehostHarness'


def start(target, *, description=None, workspace=None, inputs=(), materials=(), cases_file=None,
          checker=None, artifacts=(), max_rounds=None, timeout=600, cancelled=lambda: False):
    """Each invocation creates a fresh project; resume is explicit and keeps its files."""
    if description is not None and not description.strip():
        raise ValueError('Describe the entry/behavior to run, or request entry investigation explicitly')
    if (max_rounds is not None and max_rounds < 1) or timeout <= 0:
        raise ValueError('max_rounds and timeout must be positive')
    target = Path(target).resolve()
    if target.is_file():
        if workspace is None:
            raise ValueError('A new SO requires --workspace; existing analyses reuse their workspace')
        target = prepare_workspace(target, Path(workspace), name=target.stem)
    if target.is_relative_to(PROJECT_DIR):
        raise ValueError('Rehost outputs must use an external workspace')
    binary = _target(target)
    inputs = [Path(path).resolve(strict=True) for path in inputs]
    materials = [Path(path).resolve(strict=True) for path in materials]
    if any(not path.is_file() for path in inputs):
        raise ValueError('Each seed input must be a file')
    if cases_file is not None and inputs:
        raise ValueError('Use either fixed cases or seed inputs')
    if checker is not None and cases_file is None:
        raise ValueError('An independent checker requires explicit fixed cases')
    # Validate the fixed contract before creating projects or changing associations.
    mode = 'expected' if cases_file is not None else 'observe'
    if cases_file is not None:
        cases_file = Path(cases_file).resolve(strict=True)
        rows = read_json(cases_file)
        base = cases_file.parent
    else:
        rows = [{'id': f'seed-{index + 1}',
                 'request': {'input_file': str(path), 'input_name': path.name},
                 'input_sha256': digest(path)} for index, path in enumerate(inputs)]
        if not rows:
            rows = [{'id': 'entry', 'request': {}, 'input_sha256': hashlib.sha256(b'').hexdigest()}]
        base = target
    checker = Path(checker).resolve(strict=True) if checker else None
    spec = {'parameters': {'main_class': MAIN_CLASS}, 'artifacts': list(artifacts), 'verification': mode}
    contract = Contract(spec, rows, checker)
    for row in rows:
        contract.prepare(row['request'], base)

    key = identifier()
    harness = target.parent / 'harnesses' / f'{target.name}-rehost-{key}'
    harness.mkdir(parents=True)  # Never seed a new target from an existing Java project.
    with ExitStack() as stack:
        try:
            fds = stack.enter_context(exclusive(target, harness))
        except WriterBusy:
            shutil.rmtree(harness)
            raise
        setup = target / 'rehost' / key
        setup.mkdir(parents=True)
        ensure(harness, target.name, instructions=WORKFLOWS / 'PROJECT.md')
        shutil.copyfile(WORKFLOWS / 'pom.xml', harness / 'pom.xml')
        # There is deliberately no Java source, target ABI, JNI stub or RVA here.
        normalized_rows = []
        for row in rows:
            request = dict(row['request'])
            if 'input_file' in request:
                request['input_file'] = str((base / request['input_file']).resolve())
            normalized_rows.append({**row, 'request': request})
        write_json(setup / 'cases.json', normalized_rows)
        inspection = {'binary': binary, 'commands': []}
        for name, flags in [('headers', ['-h', '-l']), ('dependencies', ['-d']), ('symbols', ['-Ws'])]:
            inspection['commands'].append(execute(
                ['readelf', *flags, binary['path']], setup, setup / 'inspection' / name,
                timeout=30, cancelled=cancelled, pass_fds=fds))
        write_json(setup / 'inspection.json', inspection)
        body = render_prompt(WORKFLOWS / 'TASK.md', DESCRIPTION=description,
                             BINARY=binary['path'], SHA256=binary['sha256'],
                             INSPECTION=setup / 'inspection.json', INSPECTION_DIR=setup / 'inspection',
                             MODE=mode, MATERIALS=''.join(f'- {path}\n' for path in materials))
        (setup / 'task.md').write_text(body, encoding='utf-8')
        protected = [setup / 'task.md', setup / 'inspection.json',
                     *[path for path in (setup / 'inspection').rglob('*') if path.is_file()],
                     *[path for path in materials if path.is_file()],
                     *Path(__file__).parent.glob('*.py')]
        if cases_file:
            protected.append(cases_file)
        config = {'path': str(harness), **spec, 'cases': str(setup / 'cases.json'),
                  'workflow': str(WORKFLOW), 'task_description': str(setup / 'task.md'),
                  'protected_files': [str(path) for path in protected]}
        if checker:
            config['checker'] = str(checker)
        association = target / 'harness.json'
        if association.exists():
            shutil.copyfile(association, setup / 'previous-association.json')
        write_json(association, config)
    return run_rounds(target, description=body, workflow=WORKFLOW, bootstrap=True,
                  max_rounds=max_rounds, timeout=timeout, cancelled=cancelled)


def resume(directory, task, *, max_rounds=None, timeout=600, cancelled=lambda: False):
    """Continue the same generated project and full round history after rechecking."""
    return run_rounds(directory, task=task, workflow=WORKFLOW, bootstrap=True,
                  max_rounds=max_rounds, timeout=timeout, cancelled=cancelled)


def continue_analysis(directory, *, description=None, materials=(), max_rounds=None, timeout=600):
    """Reuse the associated workflow task, including a bootstrap interrupted before task creation."""
    directory = Path(directory)
    association = directory / 'harness.json'
    if association.exists():
        config = read_json(association)
        tasks = []
        for path in (directory / 'tasks').glob('*/state.json'):
            state = read_json(path)
            if state.get('mode') != 'harness' or state.get('workflow') != str(WORKFLOW):
                continue
            contract = read_json(path.parent / 'contract.json')
            if contract['harness'] == config['path']:
                tasks.append((path.parent, state))
        if tasks:
            task, state = max(tasks, key=lambda item: item[1]['created_at'])
            if state['status'] in ('complete', 'observed', 'cancelled'):
                return state
            return resume(directory, task, max_rounds=max_rounds, timeout=timeout)
        if config.get('workflow') == str(WORKFLOW):
            body = Path(config['task_description']).read_text(encoding='utf-8')
            return run_rounds(directory, description=body, workflow=WORKFLOW, bootstrap=True,
                          max_rounds=max_rounds, timeout=timeout)
    return start(directory, description=description, materials=materials, max_rounds=max_rounds,
                 timeout=timeout)
