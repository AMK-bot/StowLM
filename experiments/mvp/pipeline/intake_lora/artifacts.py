"""Frozen inference provenance, checked before any local model is loaded.

Only the one-shot scorer writes adoption.json. Runtime checks public artifacts;
sealed bytes are never among the files opened by this module.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path

from .gates import FREEZE_UTC, gate_report

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
MODEL = 'models/glimmer-30b-4bit'
FINAL_VERSION = 'stowlm.intake-final-freeze/2'
ADOPTION_VERSION = 'stowlm.intake-adoption/1'
# Imported by the inference mask/prompt path, including transitive local schemas.
DEPENDENCIES = (
    'experiments/mvp/reader/v3/decoder.py',
    'experiments/mvp/reader/v3/domain_words.py',
    'experiments/mvp/reader/schema.py',
    'experiments/mvp/reader/evaluate.py',
    'src/llm/instructions/schema.py',
    'src/stowage/conventions.py',
    'experiments/mvp/pipeline/contract/schema.py',
    'experiments/mvp/pipeline/intake/validation.py',
    'src/stowage/versions.py',
    'src/stowage/toml_cache.py',
    'src/stowage/rules/catalogue.py',
    'src/stowage/rules/catalogue.toml',
    'src/stowage/rules/imdg/dgl.py',
    'src/stowage/rules/imdg/data/dgl.json',
    'src/stowage/rules/imdg/data/checker_guards.json',
    'pyproject.toml', 'uv.lock',
) + tuple(f'src/stowage/model/{name}.py' for name in (
    '__init__', 'base', 'cargo', 'instance', 'moves', 'plan', 'port', 'provenance',
    'results', 'ship', 'vessel', 'violation', 'voyage', 'yard'))


def _safe(path):
    """Resolve lexical aliases but refuse symlink redirects, including parents."""
    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(ROOT))
    if not path.is_relative_to(root):
        raise ValueError('artifact must stay inside the repository')
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f'symlink artifact forbidden: {part}')
        if part == root:
            break
    if any(part.lower() == 'sealed' for part in path.relative_to(root).parts):
        raise ValueError('sealed bytes are not inference artifacts')
    return path


def _registered(name):
    if not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('invalid frozen artifact path')
    path = _safe(ROOT / name)
    if str(path.relative_to(ROOT)) != name:
        raise ValueError('noncanonical frozen artifact path')
    return path


def _digest(path):
    with _safe(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _json(path):
    return json.loads(_safe(path).read_text())


def _paths(adapter, results=None, model_path=None):
    adapter = _safe(adapter)
    if adapter != _safe(HERE / 'adapter-intake-v1'):
        raise ValueError('only the sealed-scored intake adapter is permitted')
    if model_path is not None and _safe(model_path) != _safe(ROOT / MODEL):
        raise ValueError('only the sealed-scored base model is permitted')
    if results is not None:
        results = _safe(results)
        if results != _safe(HERE / 'results/sealed-final'):
            raise ValueError('only the one-shot final results may be adopted')
    return adapter, results


def inference_files(adapter):
    """Required inventory is computed independently of freeze.json's entries."""
    adapter, _ = _paths(adapter)
    model = _safe(ROOT / MODEL)
    if (model / 'adapter_config.json').exists():
        raise ValueError('the base model must remain unadapted')
    files = {HERE / name for name in (
        'artifacts.py', 'private_intake.py', 'decoder.py', 'schema.py', 'evaluate.py',
        'score_final.py', 'gates.py', 'train.py', 'config.json', 'GATE.md',
        'data/manifest.json', 'recipe-freeze.json', 'tokenizer-preflight.json')}
    files.update(p for p in HERE.glob('*.py') if not p.name.startswith('test_'))
    files.update(ROOT / name for name in DEPENDENCIES)
    files.update(adapter / name for name in (
        'adapter_config.json', 'adapters.safetensors', 'STOPPED.json', 'run.json'))
    files.update(model / name for name in (
        'config.json', 'generation_config.json', 'tokenizer.json',
        'tokenizer_config.json', 'chat_template.jinja', 'model.safetensors.index.json'))
    # Include optional tokenizer/configuration files and every actual shard, so
    # additions, missing entries and changed loader-discovery inputs fail closed.
    files.update(p for p in model.rglob('*') if p.is_file() and p.name != 'README.md')
    weights = {p.name for p in model.glob('*.safetensors')}
    index = _json(model / 'model.safetensors.index.json').get('weight_map')
    if (not isinstance(index, dict) or not index or not weights
        or any(not isinstance(name, str) or name not in weights for name in index.values())):
        raise ValueError('base weight index requires complete local safetensors shards')
    return sorted({_safe(p) for p in files})


def frozen_files(adapter, recipe):
    """Union with the checked launch recipe, without reading any sealed file."""
    files = set(inference_files(adapter))
    registered = recipe.get('files_sha256')
    if not isinstance(registered, dict) or not registered:
        raise ValueError('recipe freeze has no registered artifacts')
    files.update(_registered(name) for name in registered)
    return sorted(files)


def verify_freeze(frozen, adapter):
    if frozen.get('version') != FINAL_VERSION:
        raise ValueError('complete final inference freeze required')
    registered = frozen.get('files_sha256')
    if not isinstance(registered, dict) or not registered:
        raise ValueError('final freeze has no registered artifacts')
    recipe = _json(HERE / 'recipe-freeze.json')
    required = {str(p.relative_to(ROOT)) for p in frozen_files(adapter, recipe)}
    if not required.issubset(registered):
        raise ValueError('final freeze missing required inference artifacts')
    for name, expected in registered.items():
        path = _registered(name)
        if not isinstance(expected, str) or _digest(path) != expected:
            raise ValueError(f'frozen artifact changed: {name}')
    if any(registered.get(name) != expected for name, expected in recipe['files_sha256'].items()):
        raise ValueError('final freeze differs from the launch recipe')


def _verified_results(adapter, results):
    frozen = _json(results / 'freeze.json')
    verify_freeze(frozen, adapter)
    freeze_sha = _digest(results / 'freeze.json')
    reports = {name: _json(results / f'{name}.json') for name in ('tuned', 'untrained', 'spark')}
    gates = _json(results / 'gates.json')
    if any(report.get('freeze_sha256') != freeze_sha for report in [gates, *reports.values()]):
        raise ValueError('results are not bound to this final freeze')
    sealed_sha = frozen.get('sealed_registration', {}).get('sha256')
    if (not sealed_sha or frozen['sealed_registration'].get('count') != 200
        or any(report.get('data_sha256') != sealed_sha for report in reports.values())):
        raise ValueError('results differ from the frozen sealed registration')
    expected = gate_report(reports['tuned'], reports['untrained'], reports['spark'])
    if (expected['all_pass'] is not True or gates.get('all_pass') is not True
        or gates.get('decision') != 'eligible'
        or any(gates.get(key) != expected[key] for key in ('I1', 'I2', 'I3', 'I4'))):
        raise ValueError('private adapter did not clear all four gates')
    try:
        completed = datetime.fromisoformat(gates['completed_utc'])
        if completed > datetime.fromisoformat(FREEZE_UTC) or gates.get('freeze_utc') != FREEZE_UTC:
            raise ValueError('private adapter has no complete score before the freeze')
    except (KeyError, TypeError) as error:
        raise ValueError('private adapter has no complete score before the freeze') from error
    adapter_sha = _digest(adapter / 'adapters.safetensors')
    if reports['tuned'].get('adapter_sha256') != adapter_sha:
        raise ValueError('adapter differs from the sealed-scored weights')
    for key, name in (('decoder_sha256', 'decoder.py'), ('schema_sha256', 'schema.py'),
                      ('prompt_sha256', 'schema.py'), ('evaluator_sha256', 'evaluate.py')):
        if any(report.get(key) != _digest(HERE / name) for report in reports.values()):
            raise ValueError('reported inference code differs from the final freeze')
    return {'version': ADOPTION_VERSION, 'freeze_sha256': freeze_sha,
            'adapter_sha256': adapter_sha,
            'files_sha256': {name: _digest(results / name) for name in
                             ('freeze.json', 'gates.json', 'tuned.json', 'untrained.json', 'spark.json')}}


def write_adoption(adapter, results):
    """Commit eligibility exclusively, after final score artifacts are complete."""
    adapter, results = _paths(adapter, results)
    adoption = _verified_results(adapter, results)
    with (results / 'adoption.json').open('x') as stream:
        stream.write(json.dumps(adoption, indent=2) + '\n')
    return adoption


def verify_adoption(adapter, results, model_path=None):
    adapter, results = _paths(adapter, results, model_path)
    adoption = _json(results / 'adoption.json')
    if adoption.get('version') != ADOPTION_VERSION:
        raise ValueError('final adoption manifest required')
    names = {'freeze.json', 'gates.json', 'tuned.json', 'untrained.json', 'spark.json'}
    if set(adoption.get('files_sha256', {})) != names:
        raise ValueError('complete adoption report hashes required')
    for name, expected in adoption['files_sha256'].items():
        if _digest(results / name) != expected:
            raise ValueError(f'adopted result changed: {name}')
    current = _verified_results(adapter, results)
    if adoption != current:
        raise ValueError('adoption differs from the final frozen result')
    return {'adapter_sha256': current['adapter_sha256'], 'freeze_sha256': current['freeze_sha256']}
