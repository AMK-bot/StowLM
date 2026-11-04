"""Historical input bindings identify source content, never current safety evidence."""
import hashlib
import json

import pytest

from app.identity import instance_content_digest
from app.inputs import find_instance_path, load_inputs
from stowage.io import load_instance
from stowage.model import PlannerResult


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


@pytest.fixture
def legacy_source(replay_case, tmp_path):
    instance, plan = replay_case
    raw = instance.model_dump(mode='json')
    raw['schema_version'] = '1.8'
    raw['port'].pop('max_crane_height_tiers')
    raw['port'].pop('berthing_side')
    for block in raw['port']['yard']['blocks']:
        block.pop('truck_lane_side')
    for box in raw['containers']:
        box.pop('direct_delivery')
    source = tmp_path / 'instance-1.8.json'
    source.write_text(json.dumps(raw, indent=2))
    record = {'instance_id': instance.id,
        'result': PlannerResult(status='ok', plan=plan, planner='historical-test',
                                planner_version='1.8').model_dump(mode='json'),
        'report': {'passed': True, 'provenance': {'parents': ['checker.instance.sha256:' + _digest(raw)]}}}
    path = tmp_path / 'result.json'
    path.write_text(json.dumps(record))
    return source, path, raw, record


@pytest.mark.parametrize('checksum', [False, True])
def test_legacy_canonical_binding_accepts_explicit_original_file(legacy_source, checksum):
    source, record_path, raw, record = legacy_source
    if checksum:
        record['input_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        record_path.write_text(json.dumps(record))
    normalized = load_instance(source)
    assert instance_content_digest(normalized) != _digest(raw)
    loaded = load_inputs(instance_path=source, plan_path=record_path)
    assert loaded.instance == normalized
    assert loaded.metadata['instance_content_sha256'] == instance_content_digest(normalized)
    assert loaded.instance.port.max_crane_height_tiers is None
    assert loaded.instance.port.berthing_side == 'port'
    assert all(not box.direct_delivery for box in loaded.instance.containers)
    # Saved legacy verdicts and source hashes do not manufacture shipping evidence.
    assert PlannerResult.model_validate(loaded.metadata['planner_result']).shippable is False


def test_legacy_embedded_binding_accepts_normalized_explicit_world(legacy_source):
    source, record_path, raw, record = legacy_source
    record['instance'] = raw
    record_path.write_text(json.dumps(record))
    loaded = load_inputs(selected_instance=load_instance(source), plan_path=record_path)
    assert loaded.metadata['instance_content_sha256'] == instance_content_digest(loaded.instance)


@pytest.mark.parametrize('checksum', [False, True])
def test_legacy_bound_corpus_lookup_accepts_original_raw_content(legacy_source, monkeypatch, tmp_path, checksum):
    source, record_path, _, record = legacy_source
    if checksum:
        record['input_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        record_path.write_text(json.dumps(record))
    corpus = tmp_path / 'instances-v3'
    corpus.mkdir()
    target = corpus / source.name
    target.write_bytes(source.read_bytes())
    source.unlink()
    monkeypatch.setattr('app.inputs.INSTANCE_ROOT', tmp_path / 'instances-v2')
    loaded = load_inputs(plan_path=record_path)
    assert loaded.instance == load_instance(target)
    assert find_instance_path(loaded.instance.id, root=corpus,
                              record=json.loads(record_path.read_text())) == target


@pytest.mark.parametrize('change', ['seed', 'side'])
def test_legacy_binding_still_rejects_changed_world(legacy_source, change):
    source, record_path, _, _ = legacy_source
    world = load_instance(source)
    if change == 'seed':
        world.seed = (world.seed or 0) + 1
    else:
        world.port.berthing_side = 'starboard'
    with pytest.raises(ValueError, match='(?i)(bound|binding|conflict)'):
        load_inputs(selected_instance=world, plan_path=record_path)


def test_original_file_checksum_never_overrides_conflicting_report_hash(legacy_source):
    source, record_path, _, record = legacy_source
    record['input_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    record['report']['provenance']['parents'].append('checker.instance.sha256:' + 'f' * 64)
    record_path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='Conflicting'):
        load_inputs(instance_path=source, plan_path=record_path)


def test_legacy_local_companion_resolves_binding_and_file_checksum(legacy_source, tmp_path):
    source, record_path, _, record = legacy_source
    record['input_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    record_path.write_text(json.dumps(record))
    assert load_inputs(run_folder=tmp_path, entry=record_path.name).instance == load_instance(source)


def test_current_schema_cannot_claim_legacy_raw_binding_fallback(legacy_source):
    from app.identity import instance_binding_matches
    source, _, raw, _ = legacy_source
    raw['schema_version'] = '1.9'
    normalized = load_instance(source).model_copy(update={'schema_version': '1.9'})
    assert instance_binding_matches(normalized, _digest(raw), raw) is False


def test_legacy_source_binding_requires_valid_raw_model(legacy_source):
    from app.identity import instance_binding_matches
    source, _, raw, _ = legacy_source
    raw['unexpected_physics_field'] = 'cannot silently drop this'
    assert instance_binding_matches(load_instance(source), _digest(raw), raw) is False


@pytest.mark.parametrize('embedded', [False, True])
def test_service_discovers_and_replays_legacy_binding_as_normalized_world(
        legacy_source, tmp_path, monkeypatch, embedded):
    from app.flow import DemoService
    source, record_path, raw, record = legacy_source
    normalized = load_instance(source)
    if embedded:
        record['instance'] = raw
    # All evidence must agree even though canonical schema representations differ.
    record['input_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    record_path.write_text(json.dumps(record))
    replayed = []

    def fresh_replay(instance, plan, **kwargs):
        replayed.append((instance, plan))
        return {'metadata': {}, 'checker': {'bound': True}, 'frames': []}

    monkeypatch.setattr('app.flow.build_replay', fresh_replay)
    service = DemoService(normalized, PlannerResult.model_validate(record['result']).plan,
                          instance_root=tmp_path / 'absent', run_root=tmp_path)
    catalog = service.catalog()
    selected_world = next(world for world in catalog['instances'] if world['id'] == normalized.id)
    assert selected_world.get('conflict', False) is False
    artifact = next(plan for plan in catalog['plans'] if plan['id'].startswith('artifact-'))
    assert artifact['content_digest'] == instance_content_digest(normalized)
    service.replay(artifact['id'])
    assert replayed[0][0] == normalized
    assert len(replayed) == 1


def test_service_legacy_binding_does_not_hide_conflicting_file_checksum(legacy_source, tmp_path, monkeypatch):
    from app.flow import DemoService
    source, record_path, raw, record = legacy_source
    record['instance'] = raw
    record['input_sha256'] = 'f' * 64
    record_path.write_text(json.dumps(record))
    monkeypatch.setattr('app.inputs.INSTANCE_ROOT', tmp_path / 'instances-v2')
    service = DemoService(load_instance(source), PlannerResult.model_validate(record['result']).plan,
                          instance_root=tmp_path / 'absent', run_root=tmp_path)
    artifact = next(plan for plan in service.catalog()['plans'] if plan['id'].startswith('artifact-'))
    with pytest.raises(ValueError, match='(?i)(checksum|content|conflict|binding)'):
        service.replay(artifact['id'])
