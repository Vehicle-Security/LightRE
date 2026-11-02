"""Fresh target projects, static inspection, and the shared execution feedback loop."""

import hashlib
import shutil
from pathlib import Path

from execution.contract import Contract
from execution.guide import ensure
from execution.loop import repair
from execution.process import execute
from execution.runner import _target
from execution.storage import digest, exclusive, identifier, read_json, write_json
from workspace import PROJECT_DIR, prepare_workspace


WORKFLOWS = Path(__file__).parent / 'workflows/unidbg'
WORKFLOW = WORKFLOWS / 'WORKFLOW.md'
MAIN_CLASS = 'lightre.RehostHarness'


def start(target, *, description, workspace=None, inputs=(), materials=(), cases_file=None,
          checker=None, artifacts=(), max_rounds=None, timeout=600, cancelled=lambda: False):
    """Each invocation creates a fresh project; resume is explicit and keeps its files."""
    if not description.strip():
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
    with exclusive(target, harness) as fds:
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
        body = (f'{description}\n\n目标 SO：{binary["path"]}\nSHA256：{binary["sha256"]}\n'
                f'静态检查：{setup / "inspection.json"}\n'
                f'静态原始输出：{setup / "inspection"}\n'
                '每次从零实现目标 Java harness，不复制已有目标的 Java 工程、固定环境或地址。\n'
                '先调查实际入口与依赖，再通过真实 unidbg 运行逐项补环境。\n'
                f'检查模式：{mode}；observe 仅保存运行观察，不能宣称行为验证或语义等价。\n'
                '只读配套资料（含适用的 APK、DEX、资源或既有静态分析）：\n'
                + ''.join(f'- {path}\n' for path in materials))
        (setup / 'task.md').write_text(body, encoding='utf-8')
        protected = [setup / 'task.md', setup / 'inspection.json',
                     *[path for path in (setup / 'inspection').rglob('*') if path.is_file()],
                     *[path for path in materials if path.is_file()],
                     *Path(__file__).parent.glob('*.py')]
        if cases_file:
            protected.append(cases_file)
        config = {'path': str(harness), **spec, 'cases': str(setup / 'cases.json'),
                  'protected_files': [str(path) for path in protected]}
        if checker:
            config['checker'] = str(checker)
        association = target / 'harness.json'
        if association.exists():
            shutil.copyfile(association, setup / 'previous-association.json')
        write_json(association, config)
    return repair(target, description=body, workflow=WORKFLOW, bootstrap=True,
                  max_rounds=max_rounds, timeout=timeout, cancelled=cancelled)


def resume(directory, task, *, max_rounds=None, timeout=600, cancelled=lambda: False):
    """Continue the same generated project and full round history after rechecking."""
    return repair(directory, task=task, workflow=WORKFLOW, bootstrap=True,
                  max_rounds=max_rounds, timeout=timeout, cancelled=cancelled)
