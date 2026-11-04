"""Development replay, implementation freeze, then one foreground held-out run."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import sys

from experiments.mvp.pipeline.contract.schema import sha256_file

from .admission import wait_for_slot
from .measure import score_case
from .record import record_case
from . import spark_reader

HERE = Path(__file__).resolve().parent
COHORT = HERE / 'fixtures' / 'heldout_v2'
PROJECT_ROOT = HERE.parents[3]


def extraction_prompt():
    return spark_reader.extraction_prompt()


def runtime_dependencies():
    return {'python': sys.version, 'pydantic': version('pydantic'),
            'pydantic_core': version('pydantic_core')}


def dependency_fingerprints():
    """Pin the project import closure plus the rule data read by that code."""
    roots = (PROJECT_ROOT, PROJECT_ROOT / 'src')

    def resolve(module):
        for root in roots:
            stem = root.joinpath(*module.split('.'))
            for path in (stem.with_suffix('.py'), stem / '__init__.py'):
                if path.is_file():
                    return path
        return None

    pending = [(path, 'experiments.mvp.pipeline.intake' + ('.' + path.stem if path.name != '__init__.py' else ''))
               for path in sorted(HERE.glob('*.py')) if not path.name.startswith('test_')]
    found = {}
    while pending:
        path, module = pending.pop()
        if path in found:
            continue
        found[path] = sha256_file(path)
        package = module if path.name == '__init__.py' else module.rpartition('.')[0]
        names = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ''
                if node.level:
                    parts = package.split('.')
                    prefix = '.'.join(parts[:len(parts) - node.level + 1] + ([prefix] if prefix else []))
                names.add(prefix)
                names.update(prefix + '.' + alias.name for alias in node.names if alias.name != '*')
        for name in names:
            for index in range(1, len(name.split('.')) + 1):
                candidate = '.'.join(name.split('.')[:index])
                dependency = resolve(candidate)
                if dependency is not None:
                    pending.append((dependency, candidate))
    data = PROJECT_ROOT / 'src/stowage/rules/imdg/data'
    for path in data.rglob('*'):
        if path.is_file():
            found[path] = sha256_file(path)
    for path in (PROJECT_ROOT / 'src/stowage/rules/catalogue.toml',
                 PROJECT_ROOT / 'pyproject.toml', PROJECT_ROOT / 'uv.lock'):
        found[path] = sha256_file(path)
    return {str(path.relative_to(PROJECT_ROOT)): digest for path, digest in sorted(found.items())}


def write(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def development():
    """Refresh a separate demo directory; legacy demo files remain unchanged."""
    original = HERE / 'fixtures' / 'dev'
    demo = HERE / 'fixtures' / 'demo_v2'
    demo.mkdir(exist_ok=True)
    for name in ('base_instance.json', 'expected.json', 'oracle_reading.json',
                 'booking_email.txt', 'gate_dump.csv', 'tally_sheet.png',
                 'spark_raw.json', 'spark_response.json'):
        shutil.copyfile(original / name, demo / name)
    documents = json.loads((original / 'documents.json').read_text())
    root = HERE.parents[3]
    for document in documents:
        if document.get('path'):
            document['path'] = str((demo / Path(document['path']).name).relative_to(root))
    write(demo / 'documents.json', documents)
    wait_for_slot()
    metrics = score_case(demo)
    assert metrics['status'] == 'complete', metrics
    assert metrics['assembled_sha256_equals_base'], metrics
    assert metrics['before_answers']['silent_wrong_values'] == 0, metrics
    assert metrics['traps_caught'] == metrics['traps_total'], metrics
    return metrics


def freeze():
    target = COHORT / 'SOURCE_FREEZE.json'
    if target.exists():
        raise ValueError('The v2 implementation is already frozen')
    manifest = COHORT / 'freeze.json'
    verify_inputs()
    value = {'schema_version': 'stowlm.intake-source-freeze/2',
             'frozen_utc': datetime.now(timezone.utc).isoformat(),
             'frozen_before_scoring': True, 'evaluation_split': 'heldout_v2',
             'input_freeze_sha256': sha256_file(manifest),
             'source_files': {path.name: sha256_file(path) for path in sorted(HERE.glob('*.py'))},
             'prompt_sha256': hashlib.sha256(extraction_prompt().encode()).hexdigest(),
             'dependency_files': dependency_fingerprints(),
             'runtime_dependencies': runtime_dependencies(),
             'development_metrics_sha256': sha256_file(HERE / 'fixtures/demo_v2/metrics.json'),
             'first_score_spent': {'metrics': 'fixtures/heldout/metrics.json',
                                   'sha256': sha256_file(HERE / 'fixtures/heldout/metrics.json')},
             'notes': 'Only development recordings and synthetic adversarial tests used for tuning. Fresh held-out inputs generated/frozen independently; not model-read or scored yet.'}
    write(target, value)
    return {'source_freeze_sha256': sha256_file(target), 'input_freeze_sha256': value['input_freeze_sha256']}


def verify_inputs(cohort=None):
    cohort = Path(cohort) if cohort is not None else COHORT
    path = cohort / 'freeze.json'
    if sha256_file(path) != (cohort / 'freeze.sha256').read_text().split()[0]:
        raise ValueError('The held-out input manifest changed')
    manifest = json.loads(path.read_text())
    for case in manifest['cases']:
        for name, digest in case['files'].items():
            if sha256_file(cohort / case['split'] / name) != digest:
                raise ValueError('A frozen held-out input changed')
    return manifest


def verify_frozen(cohort=None):
    cohort = Path(cohort) if cohort is not None else COHORT
    source = json.loads((cohort / 'SOURCE_FREEZE.json').read_text())
    if sha256_file(cohort / 'freeze.json') != source['input_freeze_sha256']:
        raise ValueError('The implementation names a different input freeze')
    for name, digest in source['source_files'].items():
        if sha256_file(HERE / name) != digest:
            raise ValueError('The frozen implementation changed')
    if hashlib.sha256(extraction_prompt().encode()).hexdigest() != source.get('prompt_sha256'):
        raise ValueError('The effective extraction prompt changed')
    if not source.get('dependency_files') or not source.get('runtime_dependencies'):
        raise ValueError('Historical freeze lacks dependency pins; cannot authorize a new score')
    if dependency_fingerprints() != source['dependency_files']:
        raise ValueError('The frozen validation dependencies changed')
    if runtime_dependencies() != source['runtime_dependencies']:
        raise ValueError('The frozen runtime dependencies changed')
    return verify_inputs(cohort)


def aggregate(cases):
    output = {'label': 'measured', 'evaluation_split': 'heldout_v2',
              'scored_utc': datetime.now(timezone.utc).isoformat(), 'cases': cases}
    for stage in ('before_answers', 'after_answers'):
        fields = {name: sum(case[stage][name] for case in cases)
                  for name in ('fields_correct', 'fields_total', 'fields_accepted',
                               'rows_exact', 'rows_total', 'silent_wrong_values')}
        fields['field_accuracy'] = fields['fields_correct'] / fields['fields_total']
        fields['row_exact_accuracy'] = fields['rows_exact'] / fields['rows_total']
        output[stage] = fields
    output.update(traps_caught=sum(c['traps_caught'] for c in cases),
                  traps_total=sum(c['traps_total'] for c in cases),
                  reading_cost_usd=sum(c['reading_cost_usd'] for c in cases),
                  reading_wall_s=sum(c['reading_wall_s'] for c in cases),
                  clean_controls={name: sum(c['clean_controls'][name] for c in cases)
                                  for name in ('rows_exact', 'rows_total')},
                  complete_cases=sum(c['status'] == 'complete' for c in cases),
                  assembled_hash_matches=sum(c['assembled_sha256_equals_base'] for c in cases))
    return output


def heldout():
    target = COHORT / 'metrics.json'
    if target.exists():
        raise ValueError('The v2 held-out score is frozen; read its metrics')
    manifest = verify_frozen()
    cases = []
    for item in manifest['cases']:
        verify_frozen()
        case = COHORT / item['split']
        if (case / 'metrics.json').exists():
            # A completed case may be resumed, never called or scored again.
            metrics = json.loads((case / 'metrics.json').read_text())
        else:
            if not (case / 'spark_raw.json').exists():
                metadata = record_case(case)
                print(json.dumps({'recorded': case.name, 'metadata': metadata}), flush=True)
            wait_for_slot()
            metrics = score_case(case)
        metrics = {'case': case.name, **metrics}
        cases.append(metrics)
        print(json.dumps({'scored': case.name, 'before_answers': metrics['before_answers'],
                          'traps_caught': metrics['traps_caught'], 'status': metrics['status']}), flush=True)
    output = aggregate(cases)
    write(target, output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('development', 'freeze', 'heldout'))
    args = parser.parse_args()
    print(json.dumps({'development': development, 'freeze': freeze, 'heldout': heldout}[args.action](),
                     indent=2, ensure_ascii=False), flush=True)
