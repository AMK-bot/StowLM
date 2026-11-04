"""Rereview regressions; synthetic fixtures test admission, never physical gold."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def gold():
    path = Path(__file__).resolve().parents[2] / 'experiments/gold-plans/campaign/gold.py'
    spec = importlib.util.spec_from_file_location('gold_rereview_integrity', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_g1_replacement_during_validation_cannot_become_admitted_pin(gold, tmp_path, monkeypatch):
    path = tmp_path / 'g1.json'
    original = json.dumps(dict(epoch_id='epoch',g1_met=True,evidence_eligible=True,min_yield={'small':.5},solve_yield={'small':.8})).encode()
    path.write_bytes(original)
    # Isolate the byte-read race from the separately tested strict migration gate.
    monkeypatch.setattr(gold.rule_policy(),'validate_migration_approval',lambda *args:None)
    require_evidence = gold.machinery().require_evidence
    def validate_then_replace(value):
        require_evidence(value)
        path.write_text(json.dumps(dict(epoch_id='other', g1_met=False,
            diagnostic=True, evidence_eligible=False)))
    monkeypatch.setattr(gold.machinery(), 'require_evidence', validate_then_replace)
    args = SimpleNamespace(diagnostic=False, rungs=['small'], g1_summary=path)
    admitted = gold.require_g1(args, 'epoch')
    assert admitted['sha256'] == hashlib.sha256(original).hexdigest()
    assert admitted['sha256'] != gold.sha(path)


def test_main_carries_validated_g1_digest_without_rehash(gold, tmp_path, monkeypatch):
    report = tmp_path / 'g1.json'
    report.write_text(json.dumps(dict(epoch_id='epoch',g1_met=True,min_yield={'small':.5},solve_yield={'small':.8})))
    validated_digest = hashlib.sha256(report.read_bytes()).hexdigest()
    manifest = tmp_path / 'instances/manifest.json'
    captured = {}
    migration=gold.migration_evidence()
    components=migration.current_component_epoch()
    monkeypatch.setattr(gold,'migration_evidence',lambda:migration)
    policy=gold.rule_policy()
    monkeypatch.setattr(policy,'validate_migration_approval',lambda *args:None)
    monkeypatch.setattr(policy,'enforce_rule_policy',lambda root,pin:pin)
    monkeypatch.setattr(gold,'rule_policy',lambda:policy)
    monkeypatch.setattr(gold, 'HERE', tmp_path)
    monkeypatch.setattr(gold, 'ROOT', Path('/'))
    monkeypatch.setattr(gold, 'freeze_check', lambda _: dict(epoch_id='epoch', source_sha256='source',
        _manifest_sha256='validated-freeze',required_rules={'synthetic_policy':True},component_epoch=components))
    def select(args, frozen):
        args._validated_instance_manifest_sha256 = 'validated-manifest'
        args._validated_split_manifest_sha256 = 'validated-split'
        return [dict(rung='small')], manifest, SimpleNamespace(entries_sha256='v2')
    monkeypatch.setattr(gold, 'select_inputs', select)
    monkeypatch.setattr(gold, 'tiny', lambda: SimpleNamespace(source_fingerprint=lambda: {'sha256': 'source'}))
    def hash_nonvalidated(path):
        if Path(path) == report:
            pytest.fail('validated G1 report was independently reread for provenance')
        return 'other-pin'
    monkeypatch.setattr(gold, 'sha', hash_nonvalidated)
    monkeypatch.setattr(gold.machinery(), 'ensure_identity', lambda _, identity: captured.update(identity))
    assert gold.main(['--freeze-manifest', str(tmp_path/'freeze.json'), '--instance-root', str(manifest.parent),
        '--g1-summary', str(report), '--output', str(tmp_path/'output'), '--rungs', 'small', '--estimate-only']) == 0
    assert captured['g1_summary']['sha256'] == validated_digest
    assert captured['provenance_sha256'][str(report.resolve())] == validated_digest
    assert captured['instance_manifest_sha256'] == 'validated-manifest'
    assert captured['provenance_sha256'][str(manifest.resolve())] == 'validated-manifest'


@pytest.mark.parametrize('state_boundary', ['missing', 'stale'])
def test_calibration_resume_restores_checkpoint_for_actual_writer(gold, tmp_path, monkeypatch, state_boundary):
    helper = gold.module_at('_gold_rereview_synthetic_fixture', Path(__file__).with_name('test_gold_admission.py'))
    driver = gold.tiny()
    instance, result, _ = helper.case.__wrapped__(driver, tmp_path, monkeypatch)
    plan = driver._structured_plan(instance, result.plan)
    report = helper.bound_report(instance, plan)
    member = next(row for row in driver._inventory().manifest['entries']
                  if row['split'] == 'train' and row['spec']['rung'] == 'tiny')
    from eval.splits import spec_key
    key = spec_key(member['spec'])
    row = dict(rung='tiny', shard=dict(spec_key=key, split='train'))
    identity = dict(inputs=[row])
    candidate = dict(accepted=True, shippable=True, mask_verified=True, global_screen='on',
        plan=plan.model_dump(mode='json'), report=report.model_dump(mode='json'))
    envelope = dict(format='tiny-gold@1.0', accepted=True, split='train', split_spec=member['spec'],
        spec_key=key, input_sha256='a'*64, instance_id=instance.id, group='simulated_yard/tiny',
        gold_source='scalable', scalable=candidate, plan=candidate['plan'], check_report=candidate['report'],
        shippable=True, mask_verified=True, c2='on/default', component_versions=driver.component_versions(),
        relevant_source_sha256='epoch', objective=0, gold_status='feasible', **driver.inventory_pin())
    artifact = tmp_path/'plans'/f'{key}.json'
    result_path = tmp_path/'shards'/key/'result.json'
    state_path = tmp_path/'state'/f'{key}.json'
    gold.atomic_json(artifact, envelope)
    outcome = dict(accepted=True, status='feasible', rung='tiny', attempt_id='checked-attempt',
        gold_file=str(artifact), gold_sha256=gold.sha(artifact))
    gold.atomic_json(result_path, outcome)
    gold.machinery().write_done(result_path.parent/'done.json', dict(campaign=identity, input=row),
        result_path, [artifact])
    gold.atomic_json(state_path, outcome)
    assert driver.write_manifest(tmp_path)['accepted'] == 1
    if state_boundary == 'missing':
        state_path.unlink()
    else:
        gold.atomic_json(state_path, dict(accepted=False, status='stale'))
    assert driver.write_manifest(tmp_path)['accepted'] == 0
    pending, completed = gold.calibration_selection(SimpleNamespace(calibrate_first_shard=True,
        calibration_rung=None), identity, [row], tmp_path)
    assert pending == [] and completed[0]['resumed']
    gold.publication_checkpoint_check(tmp_path, identity)
    assert driver.write_manifest(tmp_path)['accepted'] == 1
    assert json.loads(state_path.read_text()) == outcome


def test_gold_worker_uses_verified_pin_file_transport(gold, tmp_path, monkeypatch):
    helper = gold.module_at('_gold_rereview_retry_fixture', Path(__file__).with_name('test_gold_campaign_integrity.py'))
    row, identity, args = helper.setup_case(tmp_path)
    pin = dict(sources={}, files={'cache': 'a'*64}, sha256='b'*64)
    monkeypatch.setattr(gold, 'freeze_check', lambda _: {'numerical_caches': pin})
    monkeypatch.setattr(gold.machinery(), 'runner_check', lambda *_: None)
    transport = gold.module_at('_gold_rereview_transport', gold.ROOT/'experiments/campaign-g1/pin_transport.py')
    class Child:
        pid = 12345
        def __init__(self, environment):
            assert transport.LEGACY not in environment
            assert transport.read_pin(environment) == pin
            assert Path(environment[transport.PIN_PATH]).is_relative_to(tmp_path)
        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(gold.subprocess, 'Popen', lambda *_, **kwargs: Child(kwargs['env']))
    result = gold.run_shard(args, identity, row, tmp_path)
    assert result['status'] == 'worker_error'  # no synthetic solver result was supplied


@pytest.mark.parametrize('resume_mode', ['ordinary', 'calibration'])
def test_gold_resume_uses_verified_result_without_reopening(gold, tmp_path, monkeypatch, resume_mode):
    row = dict(rung='tiny', shard=dict(spec_key='key'))
    identity = dict(inputs=[row])
    result_path = tmp_path/'shards/key/result.json'
    original = dict(accepted=False, status='timeout', rung='tiny', attempt_id='bound-attempt')
    gold.atomic_json(result_path, original)
    marker = result_path.parent/'done.json'
    gold.machinery().write_done(marker, dict(campaign=identity, input=row), result_path)
    valid_done = gold.machinery().valid_done
    def validate_then_replace(path, expected):
        valid = valid_done(path, expected)
        gold.atomic_json(result_path, {**original, 'accepted': True, 'evidence_eligible': False,
            'status': 'unverified replacement'})
        return valid
    monkeypatch.setattr(gold.machinery(), 'valid_done', validate_then_replace)
    monkeypatch.setattr(gold, 'freeze_check', lambda _: {})
    monkeypatch.setattr(gold.machinery(), 'runner_check', lambda *_: None)
    if resume_mode == 'ordinary':
        outcome = gold.run_shard(SimpleNamespace(), identity, row, tmp_path)
    else:
        pending, outcomes = gold.calibration_selection(SimpleNamespace(calibrate_first_shard=True,
            calibration_rung=None), identity, [row], tmp_path)
        assert pending == []
        outcome = outcomes[0]
    assert outcome == {**original, 'resumed': True}
    assert json.loads((tmp_path/'state/key.json').read_text()) == original


def test_writer_never_publishes_replacement_bytes_under_passing_claims(gold, tmp_path, monkeypatch):
    helper = gold.module_at('_gold_round3_writer_fixture', Path(__file__).with_name('test_gold_admission.py'))
    driver = gold.tiny()
    instance, result, _ = helper.case.__wrapped__(driver, tmp_path, monkeypatch)
    plan = driver._structured_plan(instance, result.plan)
    report = helper.bound_report(instance, plan)
    member = next(row for row in driver._inventory().manifest['entries']
                  if row['split'] == 'train' and row['spec']['rung'] == 'tiny')
    from eval.splits import spec_key
    key = spec_key(member['spec'])
    candidate = dict(accepted=True, shippable=True, mask_verified=True, global_screen='on')
    envelope = dict(format='tiny-gold@1.0', accepted=True, split='train', split_spec=member['spec'],
        spec_key=key, input_sha256='a'*64, instance_id=instance.id, group='simulated_yard/tiny',
        gold_source='scalable', scalable=candidate, plan=plan.model_dump(mode='json'),
        check_report=report.model_dump(mode='json'), shippable=True, mask_verified=True,
        c2='on/default', component_versions=driver.component_versions(), relevant_source_sha256='epoch',
        objective=0, gold_status='feasible', **driver.inventory_pin())
    artifact = tmp_path/'plans'/f'{key}.json'
    gold.atomic_json(artifact, envelope)
    original = gold.sha(artifact)
    gold.atomic_json(tmp_path/'state/key.json', dict(accepted=True, gold_file=str(artifact), gold_sha256=original))
    clean = driver._require_clean
    replaced = []
    def replace_during_validation(value):
        clean(value)
        if not replaced:
            gold.atomic_json(artifact, dict(envelope, accepted=False, diagnostic=True, evidence_eligible=False))
            replaced.append(gold.sha(artifact))
    monkeypatch.setattr(driver, '_require_clean', replace_during_validation)
    published = driver.write_manifest(tmp_path)
    assert replaced
    assert published['accepted'] == 0, 'concurrently replaced envelope must fail the publication freshness gate'
    assert all(row['sha256'] != replaced[0] for row in published['shards'])
