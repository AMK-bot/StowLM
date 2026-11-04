"""Synthetic run-folder evidence tests; these never load a language model."""
import json
from pathlib import Path
import shutil
import pytest
from app.inputs import load_inputs
from app.identity import instance_content_digest
from app.tests.report_helpers import bind_report
from stowage.model import CheckReport, PlannerResult

FIXTURE = Path(__file__).parent / 'fixtures/model_run'


def run_folder(tmp_path, replay_case):
    instance, plan = replay_case
    root = tmp_path / 'model-run'
    shutil.copytree(FIXTURE, root)
    record = {'result': PlannerResult(status='ok', plan=plan, planner='stowlm', planner_version='fixture').model_dump(mode='json'),
              'instance': instance.model_dump(mode='json'),
              'report': bind_report(CheckReport(passed=True), instance, plan).model_dump(mode='json')}
    path = root / 'instances/fixture_empty_ship/result.json'
    path.write_text(json.dumps(record))
    return root, path, record


def test_run_fixture_is_labelled_unverified_and_trace_retained(tmp_path, replay_case):
    root, _, _ = run_folder(tmp_path, replay_case)
    loaded = load_inputs(run_folder=root)
    assert loaded.metadata['label'] == 'Imported (unverified source)'
    model = loaded.metadata['model_run']
    assert model['fixture'] is True and model['verified'] is False
    assert model['trace'][0]['n_legal'] == 2
    assert model['trace'][0]['top_k'][0]['legal'] is False
    assert 'passed' not in model


def test_complete_adapter_binding_can_be_explicitly_verified_in_test(tmp_path, replay_case, monkeypatch):
    root, _, _ = run_folder(tmp_path, replay_case)
    monkeypatch.setattr('app.model_runs._allow_test_fixture_verification', lambda: True)
    loaded = load_inputs(run_folder=root)
    assert loaded.metadata['label'] == 'StowLM (Muse Glimmer 30B + LoRA, MLX)'
    assert loaded.metadata['model_run']['verified'] is True
    assert loaded.metadata['instance_content_sha256'] == instance_content_digest(loaded.instance)


def test_base_model_has_distinct_label(tmp_path, replay_case, monkeypatch):
    root, result, record = run_folder(tmp_path, replay_case)
    record['result']['planner'] = 'stowlm.base'  # what llm.stowlm.planner writes without an adapter
    result.write_text(json.dumps(record))
    path = root / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest.update(planner_kind='base_glimmer_mask', adapter_sha256=None, adapter_lineage=None,
                    adapter_clean=None, adapter_data_mode=None, adapter_stubs=None)
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr('app.model_runs._allow_test_fixture_verification', lambda: True)
    assert load_inputs(run_folder=root).metadata['label'] == 'Base Muse Glimmer 30B + feasibility mask'


@pytest.mark.parametrize('changes', [
    {'adapter_clean': False},
    {'adapter_clean': True, 'adapter_data_mode': 'pipeline_test'},
    {'adapter_clean': True, 'adapter_data_mode': 'diagnostic'},
    {'adapter_clean': True, 'adapter_stubs': {'training': 'stubbed dataset'}},
    {'adapter_clean': True, 'diagnostic': True},
])
def test_tainted_adapter_never_receives_clean_stowlm_label(tmp_path, replay_case, monkeypatch, changes):
    root, _, _ = run_folder(tmp_path, replay_case)
    path = root/'manifest.json'
    manifest = json.loads(path.read_text())
    manifest.update(changes)
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr('app.model_runs._allow_test_fixture_verification', lambda: True)
    assert load_inputs(run_folder=root).metadata['label'] == 'StowLM (diagnostic adapter, not evidence)'


def test_legacy_missing_adapter_clean_is_unverified(tmp_path, replay_case, monkeypatch):
    root, _, _ = run_folder(tmp_path, replay_case)
    path = root/'manifest.json'
    manifest = json.loads(path.read_text())
    for key in ('adapter_clean', 'adapter_data_mode', 'adapter_stubs', 'method', 'selection'):
        manifest.pop(key, None)
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr('app.model_runs._allow_test_fixture_verification', lambda: True)
    assert load_inputs(run_folder=root).metadata['label'] == 'Imported (unverified source)'


@pytest.mark.parametrize('mode,stubs', [('pipeline_test', {}), ('production_gold', {'training': 'diagnostic data'})])
def test_actual_lineage_taint_overrides_a_clean_manifest_claim(tmp_path, replay_case, monkeypatch, mode, stubs):
    import hashlib
    root, _, _ = run_folder(tmp_path, replay_case)
    path = root / 'manifest.json'
    manifest = json.loads(path.read_text())
    lineage = root / 'lineage.json'
    lineage.write_text(json.dumps({'weights_sha256': manifest['adapter_sha256'],
        'provenance': {'stubs': stubs}, 'training': {'data_identity': {'dataset_kind': mode}}}))
    manifest['adapter_lineage'] = {'path': 'lineage.json', 'sha256': hashlib.sha256(lineage.read_bytes()).hexdigest()}
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr('app.model_runs._allow_test_fixture_verification', lambda: True)
    loaded = load_inputs(run_folder=root)
    assert loaded.metadata['label'] == 'StowLM (diagnostic adapter, not evidence)'
    assert loaded.metadata['model_run']['adapter_clean'] is False


def test_diagnostic_adapter_taint_reaches_canonical_shipping_gate(tmp_path, replay_case, monkeypatch):
    from app.capture import checked_replay
    from app.tests.report_helpers import bind_report
    from stowage.model import CheckReport, PartReport
    from stowage.moves import apply_plan
    root, _, _ = run_folder(tmp_path, replay_case)
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest.update(adapter_clean=False, adapter_data_mode='pipeline_test')
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr('app.model_runs._allow_test_fixture_verification', lambda: True)
    loaded = load_inputs(run_folder=root)
    # Synthetic passing evidence isolates inherited adapter taint, not physics.
    report = CheckReport(passed=True, count=apply_plan(loaded.instance, loaded.plan).count,
        parts={name: PartReport(name=name) for name in
               ('moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints')})
    bind_report(report, loaded.instance, loaded.plan)
    monkeypatch.setattr('app.backend.build_checker_payload', lambda *a, **k: {
        'passed': True, 'bound': True, 'report': report.model_dump(mode='json')})
    payload = checked_replay(loaded)
    assert payload['metadata']['shippable'] is False
    assert payload['metadata']['stub_tainted'] is True


def test_legacy_diagnostic_manifest_can_retain_explicit_non_evidence_label(tmp_path, replay_case, monkeypatch):
    root, _, _ = run_folder(tmp_path, replay_case)
    path = root/'manifest.json'
    manifest = json.loads(path.read_text())
    for key in ('adapter_clean', 'adapter_data_mode', 'adapter_stubs', 'method', 'selection'):
        manifest.pop(key, None)
    manifest.update(diagnostic=True, epoch_id=None)
    manifest['decoding']['c2'] = 'off (stub; diagnostic)'
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr('app.model_runs._allow_test_fixture_verification', lambda: True)
    loaded = load_inputs(run_folder=root)
    assert loaded.metadata['label'] == 'StowLM (diagnostic adapter, not evidence)'
    assert loaded.metadata['model_run']['decoding']['c2'] is False
    assert loaded.metadata['model_run']['decoding']['c2_declaration'] == 'off (stub; diagnostic)'


@pytest.mark.parametrize('field,value', [('adapter_clean', 'true'), ('adapter_stubs', []),
                                       ('adapter_data_mode', 7), ('adapter_data_mode', {}),
                                       ('method', None), ('selection', [])])
def test_malformed_adapter_identity_fields_are_rejected(tmp_path, replay_case, field, value):
    root, _, _ = run_folder(tmp_path, replay_case)
    path = root/'manifest.json'
    manifest = json.loads(path.read_text())
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='manifest'):
        load_inputs(run_folder=root)


@pytest.mark.parametrize('mutation', ['missing', 'hash', 'decoding', 'revision', 'runtime', 'lineage', 'trace'])
def test_malformed_model_run_rejected(tmp_path, replay_case, mutation):
    root, _, _ = run_folder(tmp_path, replay_case)
    path = root / 'manifest.json'
    manifest = json.loads(path.read_text())
    if mutation == 'missing': manifest.pop('mask_version')
    if mutation == 'hash': manifest['adapter_sha256'] = 'bad'
    if mutation == 'decoding': manifest['decoding']['c2'] = 'yes'
    if mutation == 'revision': manifest['base_model_revision'] = None
    if mutation == 'runtime': manifest['runtime'] = 'pytorch'
    if mutation == 'lineage': manifest['adapter_lineage'] = {'path': 'x', 'sha256': 'bad'}
    if mutation == 'trace': (root / 'instances/fixture_empty_ship/trace.jsonl').write_text('{"step":false}')
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='(?i)(manifest|trace|model run)'):
        load_inputs(run_folder=root)


def test_self_claimed_stowlm_without_manifest_is_unverified(tmp_path, replay_case):
    root, _, _ = run_folder(tmp_path, replay_case)
    (root / 'manifest.json').unlink()
    assert load_inputs(run_folder=root).metadata['label'] == 'Imported (unverified source)'


def test_manifest_does_not_verify_missing_instance_binding(tmp_path, replay_case, monkeypatch):
    root, path, record = run_folder(tmp_path, replay_case)
    record.pop('report')
    path.write_text(json.dumps(record))
    monkeypatch.setattr('app.model_runs._allow_test_fixture_verification', lambda: True)
    loaded = load_inputs(run_folder=root)
    assert loaded.metadata['model_run']['verified'] is False
    assert loaded.metadata['label'] == 'Imported (unverified source)'


def test_raw_contract_result_with_report_and_hash_can_import(tmp_path, replay_case, monkeypatch):
    root, path, record = run_folder(tmp_path, replay_case)
    record = {**record['result'], 'instance': record['instance'], 'report': record['report']}
    path.write_text(json.dumps(record))
    monkeypatch.setattr('app.model_runs._allow_test_fixture_verification', lambda: True)
    assert load_inputs(run_folder=root).metadata['model_run']['verified'] is True


def test_catalogue_uses_verified_identity_not_self_claimed_name(tmp_path, replay_case, monkeypatch):
    from app.flow import DemoService
    root, _, _ = run_folder(tmp_path, replay_case)
    monkeypatch.setattr('app.model_runs._allow_test_fixture_verification', lambda: True)
    instance, plan = replay_case
    service = DemoService(instance, plan, run_root=root, instance_root=tmp_path/'absent')
    entry = next(p for p in service.catalog()['plans'] if p['id'].startswith('artifact-'))
    assert entry['label'] == 'StowLM (Muse Glimmer 30B + LoRA, MLX)'
    assert len(entry['source_files']) == 2


@pytest.mark.parametrize('source', ['manifest.json', 'instances/fixture_empty_ship/trace.jsonl'])
def test_model_source_changed_after_registration_rejects_replay(tmp_path, replay_case, source):
    from app.flow import DemoService
    root, _, _ = run_folder(tmp_path, replay_case)
    loaded = load_inputs(run_folder=root)
    # Cached fixture replay isolates source invalidation; this carries no safety verdict.
    service = DemoService(loaded.instance, loaded.plan, source_metadata=loaded.metadata,
        initial_replay={'metadata': {'fixture': True}}, discover=False)
    assert service.replay(service.active_plan_id)['metadata']['fixture'] is True
    path = root/source
    path.write_text(path.read_text()+'\n')
    with pytest.raises(ValueError, match='(?i)(source.*changed|changed.*source)'):
        service.replay(service.active_plan_id)
