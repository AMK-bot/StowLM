"""Tiny synthetic weights/manifests only; never touch a real model or sealed set."""
import json
from pathlib import Path

import pytest

from . import artifacts, score_final, train
from .gates import FREEZE_UTC, gate_report


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value)


@pytest.fixture
def scored(tmp_path, monkeypatch):
    package_names = [p.name for p in artifacts.HERE.glob('*.py') if not p.name.startswith('test_')]
    here = tmp_path / 'experiments/mvp/pipeline/intake_lora'
    for module in (artifacts, score_final):
        monkeypatch.setattr(module, 'ROOT', tmp_path)
        monkeypatch.setattr(module, 'HERE', here)
    adapter = here / 'adapter-intake-v1'
    results = here / 'results/sealed-final'
    model = tmp_path / artifacts.MODEL
    for name in package_names:
        put(here / name, f'# synthetic {name}\n')
    for name in artifacts.DEPENDENCIES:
        put(tmp_path / name, f'# synthetic {name}\n')
    put(here / 'config.json', {})
    put(here / 'GATE.md', 'synthetic fixed gate')
    put(here / 'tokenizer-preflight.json', {'sealed_read': False})
    put(here / 'data/manifest.json', {'files': {
        'sealed/sealed.jsonl': {'sha256': 'a' * 64, 'count': 200}}})
    for name in ('config.json', 'generation_config.json', 'tokenizer.json', 'tokenizer_config.json'):
        put(model / name, {})
    put(model / 'chat_template.jinja', 'synthetic chat template')
    put(model / 'model-00001-of-00001.safetensors', 'synthetic base weights')
    put(model / 'model.safetensors.index.json', {'weight_map': {'weight': 'model-00001-of-00001.safetensors'}})
    put(adapter / 'adapters.safetensors', 'synthetic adapter weights')
    put(adapter / 'adapter_config.json', {'lora_parameters': {'scale': 20}})
    put(adapter / 'STOPPED.json', {'source_adapter': None,
                                 'adapter_sha256': artifacts._digest(adapter / 'adapters.safetensors')})
    put(adapter / 'run.json', {'source_adapter': None, 'config_sha256': artifacts._digest(here / 'config.json')})
    # Exercise an imported training dependency registered only in the launch recipe.
    dependency = tmp_path / 'experiments/mvp/reader/train.py'
    put(dependency, '# synthetic imported trainer\n')
    recipe = {'source_adapter': None, 'files_sha256': {
        str(dependency.relative_to(tmp_path)): artifacts._digest(dependency)}}
    put(here / 'recipe-freeze.json', recipe)
    monkeypatch.setattr(train, 'check_recipe_freeze', lambda: recipe)
    frozen = score_final.freeze_final(adapter, results)
    freeze_sha = artifacts._digest(results / 'freeze.json')
    reports = {}
    for arm, exact in (('tuned', 190), ('untrained', 160), ('spark', 195)):
        report = {'arm': arm, 'count': 200, 'exact_count': exact, 'row_exact': exact / 200,
                  'dg_count': 20, 'silent_dg_errors': 0, 'malformed_count': 0,
                  'missing_count': 0, 'recorded_prediction_count': 200,
                  'data_sha256': 'a' * 64, 'schema_constrained': True,
                  'decoding_policy': 'synthetic-mask/1', 'max_output_tokens': 256,
                  'model': 'muse-spark-1.3' if arm == 'spark' else artifacts.MODEL,
                  'adapter_sha256': artifacts._digest(adapter / 'adapters.safetensors') if arm == 'tuned' else None,
                  'freeze_sha256': freeze_sha}
        for key, filename in (('decoder_sha256', 'decoder.py'), ('schema_sha256', 'schema.py'),
                              ('prompt_sha256', 'schema.py'), ('evaluator_sha256', 'evaluate.py')):
            report[key] = artifacts._digest(here / filename)
        put(results / f'{arm}.json', report)
        reports[arm] = report
    gates = gate_report(reports['tuned'], reports['untrained'], reports['spark'])
    gates.update(completed_utc='2026-11-02T00:00:00+00:00', freeze_utc=FREEZE_UTC, freeze_sha256=freeze_sha)
    put(results / 'gates.json', gates)
    artifacts.write_adoption(adapter, results)
    return {'here': here, 'adapter': adapter, 'results': results, 'model': model, 'frozen': frozen}


def test_complete_frozen_adoption_without_sealed_or_model_load(scored, monkeypatch):
    original = Path.open

    def no_sealed(path, *args, **kwargs):
        assert 'sealed' not in path.parts
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', no_sealed)
    bound = artifacts.verify_adoption(scored['adapter'], scored['results'])
    assert bound == {'adapter_sha256': artifacts._digest(scored['adapter'] / 'adapters.safetensors'),
                     'freeze_sha256': artifacts._digest(scored['results'] / 'freeze.json')}
    with pytest.raises(FileExistsError):
        artifacts.write_adoption(scored['adapter'], scored['results'])


@pytest.mark.parametrize('root,name', [
    ('adapter', 'adapter_config.json'), ('adapter', 'adapters.safetensors'),
    ('model', 'config.json'), ('model', 'generation_config.json'),
    ('model', 'model-00001-of-00001.safetensors'), ('model', 'model.safetensors.index.json'),
    ('model', 'tokenizer.json'), ('model', 'tokenizer_config.json'), ('model', 'chat_template.jinja'),
    ('here', 'decoder.py'), ('here', 'private_intake.py'),
    ('here', '../../reader/v3/decoder.py'), ('here', '../../reader/schema.py'),
    ('here', '../../reader/evaluate.py'), ('here', '../../reader/train.py'),
])
def test_mutation_of_any_inference_artifact_rejects(scored, root, name):
    path = scored[root] / name
    path.write_text(path.read_text() + "\nchanged")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        artifacts.verify_adoption(scored["adapter"], scored["results"])


def test_freeze_cannot_omit_a_required_artifact_even_with_rebound_reports(scored):
    frozen = scored['frozen']
    key = str((scored['adapter'] / 'adapter_config.json').relative_to(artifacts.ROOT))
    del frozen['files_sha256'][key]
    with pytest.raises(ValueError, match='missing required'):
        artifacts.verify_freeze(frozen, scored['adapter'])


def test_final_freeze_preserves_launch_dependency_set(scored):
    frozen = scored['frozen']
    key = 'experiments/mvp/reader/train.py'
    assert key in frozen['files_sha256']
    del frozen['files_sha256'][key]
    with pytest.raises(ValueError, match='missing required'):
        artifacts.verify_freeze(frozen, scored['adapter'])


@pytest.mark.parametrize('name', ['freeze.json', 'gates.json', 'tuned.json', 'untrained.json', 'spark.json'])
def test_adoption_binds_every_report_and_final_freeze(scored, name):
    path = scored['results'] / name
    put(path, json.loads(path.read_text()) | {'changed': True})
    with pytest.raises(ValueError, match='adopted result changed'):
        artifacts.verify_adoption(scored['adapter'], scored['results'])


def test_missing_adoption_never_adopts_eligible_report(scored):
    (scored['results'] / 'adoption.json').unlink()
    with pytest.raises(FileNotFoundError):
        artifacts.verify_adoption(scored['adapter'], scored['results'])


def test_arms_cannot_be_reused_from_another_freeze(scored):
    path = scored['results'] / 'tuned.json'
    report = json.loads(path.read_text())
    report['freeze_sha256'] = 'b' * 64
    put(path, report)
    with pytest.raises(ValueError, match='not bound'):
        artifacts._verified_results(scored['adapter'], scored['results'])


def test_adoption_pointer_cannot_name_another_freeze(scored):
    path = scored['results'] / 'adoption.json'
    adoption = json.loads(path.read_text())
    adoption['freeze_sha256'] = 'b' * 64
    put(path, adoption)
    with pytest.raises(ValueError, match='adoption differs'):
        artifacts.verify_adoption(scored['adapter'], scored['results'])


@pytest.mark.parametrize('which', ['adapter', 'model', 'results'])
def test_foreign_runtime_paths_refused(scored, which):
    foreign = artifacts.ROOT / 'foreign'
    arguments = {'adapter': scored['adapter'], 'results': scored['results'], 'model_path': scored['model']}
    arguments[{'adapter': 'adapter', 'results': 'results', 'model': 'model_path'}[which]] = foreign
    with pytest.raises(ValueError, match='only the'):
        artifacts.verify_adoption(**arguments)


def test_artifact_symlink_redirect_refused_even_for_identical_bytes(scored):
    path = scored['adapter'] / 'adapter_config.json'
    destination = artifacts.ROOT / 'identical.json'
    destination.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(destination)
    with pytest.raises(ValueError, match='symlink'):
        artifacts.verify_adoption(scored['adapter'], scored['results'])


def test_new_model_loader_input_requires_a_new_freeze(scored):
    put(scored['model'] / 'special_tokens_map.json', {})
    with pytest.raises(ValueError, match='missing required'):
        artifacts.verify_adoption(scored['adapter'], scored['results'])


def test_missing_model_shard_rejected(scored):
    (scored['model'] / 'model-00001-of-00001.safetensors').unlink()
    with pytest.raises(ValueError, match='weight index'):
        artifacts.verify_adoption(scored['adapter'], scored['results'])


def test_final_freeze_refuses_output_path_redirect(scored):
    with pytest.raises(ValueError, match='only the one-shot'):
        score_final.freeze_final(scored['adapter'], artifacts.ROOT / 'foreign-output')
