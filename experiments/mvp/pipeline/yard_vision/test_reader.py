"""Offline safety tests; evidence stubs exercise validation, not model accuracy."""
import copy
import json

import pytest

from experiments.mvp.pipeline.contract.schema import Mode, StageRecord, YardRead, sha256_file
from experiments.mvp.pipeline.yard_vision import (
    RecordedClient, context_sha256, read_yard_picture, reading_context,
)
from experiments.mvp.pipeline.yard_vision import reader
from stowage.conventions import make_container_id
from stowage.model.yard import YardState

IDS = [make_container_id('QZXU', n) for n in range(5)]


@pytest.fixture
def base():
    return {'port': {'yard': {'id': 'synthetic', 'blocks': [
        {'id': 'A', 'n_bays': 6, 'n_rows': 2, 'max_tiers': 4, 'equipment_id': 'rtg',
         'allowed_lengths_ft': [20, 40]}]}, 'yard_max_tiers': 3},
        'containers': [{'id': box, 'length_ft': 20} for box in IDS[:2]],
        'load_list': IDS[:2], 'onboard': {'placements': []},
        'yard': {'yard_id': 'synthetic', 'stacks': [
            {'key': 'A-001-01', 'length_ft': 20, 'boxes': IDS[:2]}]}}


@pytest.fixture
def evidence():
    return {'complete_view': True, 'quay_clear': True, 'segments': 2,
            'stacks': [{'key': 'A-001-01', 'length_ft': 20, 'height': 2, 'boxes': IDS[:2]}]}


@pytest.fixture
def image(tmp_path):
    # Reader hashes bytes; image decoding and model semantics belong to the client.
    path = tmp_path / 'generated.png'
    path.write_bytes(b'generated-picture-stub')
    return path


class Client:
    mode = Mode.live

    def __init__(self, evidence):
        self.evidence = evidence
        self.records = []

    def read(self, image_path, *, layout, inventory):
        self.context = {'layout': layout, 'inventory': inventory}
        return self.evidence


def run(image, base, evidence):
    return read_yard_picture(image, base_instance=base, client=Client(evidence))


def reasons(result):
    assert result.status == 'needs_answers'
    assert result.state is None
    assert result.questions
    assert result.records
    YardRead.model_validate_json(result.model_dump_json())
    return {question.reason for question in result.questions}


def test_complete_is_shared_state_and_jsonable(image, base, evidence):
    result = run(image, base, evidence)
    assert isinstance(result, YardRead)
    assert result.status == 'complete'
    assert not result.questions
    state = YardState.model_validate(result.state)
    assert state.stacks[0].boxes == IDS[:2]
    assert state.time_s == 0
    assert result.records[-1].mode == Mode.live
    YardRead.model_validate_json(result.model_dump_json())


def test_positions_never_enter_prompt_or_hash(image, base, evidence):
    first = context_sha256(base)
    base['yard']['stacks'][0]['boxes'].reverse()
    base['yard']['stacks'][0]['key'] = 'A-005-02'
    assert context_sha256(base) == first
    client = Client(evidence)
    result = read_yard_picture(image, base_instance=base, client=client)
    assert result.status == 'complete'
    assert client.context['inventory'] == [{'id': box, 'length_ft': 20} for box in IDS[:2]]
    assert 'stacks' not in client.context['layout']
    assert client.context['layout']['blocks'][0]['max_tiers'] == 3
    assert result.state['stacks'][0]['boxes'] == IDS[:2]


def test_does_not_fill_hidden_id_from_ground_truth(image, base, evidence):
    evidence['stacks'][0]['boxes'][0] = None
    assert 'unreadable_id' in reasons(run(image, base, evidence))


@pytest.mark.parametrize('field,value,reason', [
    ('complete_view', False, 'incomplete_view'),
    ('complete_view', 'true', 'incomplete_view'),
    ('quay_clear', None, 'quay_not_confirmed_clear'),
    ('segments', None, 'missing_segmentation'),
    ('segments', True, 'missing_segmentation'),
    ('segments', -1, 'missing_segmentation'),
    ('segments', 1, 'segment_count_mismatch'),
    ('stacks', None, 'missing_stacks'),
    ('questions', ['hidden lower tier'], 'reader_uncertainty'),
])
def test_document_uncertainty(image, base, evidence, field, value, reason):
    evidence[field] = value
    assert reason in reasons(run(image, base, evidence))


@pytest.mark.parametrize('field,value,reason', [
    ('key', None, 'invalid_geometry'),
    ('key', 'A-000-01', 'invalid_geometry'),
    ('key', 'B-001-01', 'invalid_geometry'),
    ('key', 'A-007-01', 'outside_block'),
    ('key', 'A-001-03', 'outside_block'),
    ('length_ft', None, 'unreadable_length'),
    ('length_ft', '20', 'unreadable_length'),
    ('length_ft', 45, 'disallowed_length'),
    ('height', None, 'unreadable_height'),
    ('height', True, 'unreadable_height'),
    ('height', 4, 'tier_limit'),
    ('height', 1, 'height_id_count_mismatch'),
    ('boxes', None, 'missing_ids'),
    ('boxes', [IDS[0], None], 'unreadable_id'),
    ('boxes', [IDS[0], 'UNKNOWN'], 'unknown_id'),
    ('boxes', [IDS[0], IDS[0]], 'duplicate_id'),
    ('boxes', [IDS[0]], 'height_id_count_mismatch'),
    ('order_readable', False, 'unreadable_order_or_ids'),
    ('ids_readable', False, 'unreadable_order_or_ids'),
])
def test_stack_validation(image, base, evidence, field, value, reason):
    evidence['stacks'][0][field] = value
    assert reason in reasons(run(image, base, evidence))


def test_duplicate_and_overlapping_stacks(image, base, evidence):
    evidence['stacks'].append(copy.deepcopy(evidence['stacks'][0]))
    result = reasons(run(image, base, evidence))
    assert {'duplicate_stack', 'overlapping_stacks', 'duplicate_id'} <= result


def test_long_stack_overhang_and_length_mismatch(image, base, evidence):
    evidence['stacks'][0].update(key='A-006-01', length_ft=40)
    assert {'outside_block', 'container_length_mismatch'} <= reasons(run(image, base, evidence))


def test_long_stack_overlap(image, base, evidence):
    base['containers'][0]['length_ft'] = 40
    evidence['stacks'] = [dict(key='A-001-01', length_ft=40, height=1, boxes=[IDS[0]]),
                          dict(key='A-002-01', length_ft=20, height=1, boxes=[IDS[1]])]
    assert 'overlapping_stacks' in reasons(run(image, base, evidence))


def test_missing_required_id(image, base, evidence):
    evidence.update(segments=1)
    evidence['stacks'][0].update(boxes=[IDS[0]], height=1)
    assert 'missing_required_ids' in reasons(run(image, base, evidence))


@pytest.mark.parametrize('kind', ['onboard', 'direct_delivery'])
def test_registry_ids_cannot_be_in_yard_when_elsewhere(image, base, evidence, kind):
    if kind == 'onboard':
        base['onboard']['placements'] = [{'container_id': IDS[0]}]
    else:
        base['containers'][0]['direct_delivery'] = True
    assert 'container_not_in_yard' in reasons(run(image, base, evidence))


def test_segmentation_shapes(image, base, evidence):
    evidence['segments'] = [{'box': [0, 0, 10, 10]}, {'box': [10, 10, 20, 20]}]
    assert run(image, base, evidence).status == 'complete'
    evidence['segments'] = {'count': 2}
    assert run(image, base, evidence).status == 'complete'


@pytest.mark.parametrize('segment', [
    None, 'box', {}, {'box': [0, 0, 1]}, {'box': [0, 0, 1, 1, 2]},
    {'box': [0, 0, float('nan'), 1]}, {'box': [0, 0, float('inf'), 1]},
    {'box': [0, 0, True, 1]}, {'box': [0, 0, '1', 1]},
    {'box': [0, 0, 0, 1]}, {'box': [1, 0, 0, 1]}, {'box': [-1, 0, 1, 1]},
    {'box': [0, -1, 1, 1]}, {'box': [0, 0, 1, 0]},
    {'box': [0, 0, 11, 1], 'image_size': [10, 10]},
    {'box': [0, 0, 1, 11], 'image_size': [10, 10]},
    {'box': [0, 0, 1, 1], 'image_size': None},
    {'box': [0, 0, 1, 1], 'image_size': [0, 10]},
    {'box': [0, 0, 1, 1], 'image_size': [10]},
    {'box': [0, 0, 1, 1], 'image_size': [True, 10]},
    {'box': [0, 0, 1, 1], 'image_size': [float('inf'), 10]},
])
def test_invalid_sam_segment_abstains(image, base, evidence, segment):
    evidence['segments'] = [segment, {'box': [1, 1, 2, 2]}]
    assert 'invalid_segment' in reasons(run(image, base, evidence))


def test_duplicate_segment_abstains(image, base, evidence):
    evidence['segments'] = [{'box': [0, 0, 1, 1]}, {'box': [0.0, 0.0, 1.0, 1.0]}]
    assert 'duplicate_segment' in reasons(run(image, base, evidence))


def test_bbox_alias_and_bounds(image, base, evidence):
    evidence['segments'] = [{'bbox': [0, 0, 1, 1], 'image_size': [10, 10]},
                            {'box': [1, 1, 10, 10], 'image_size': [10, 10]}]
    assert run(image, base, evidence).status == 'complete'
    evidence['segments'][0]['image_size'] = [11, 10]
    assert 'invalid_segment' in reasons(run(image, base, evidence))


def test_clean_empty_yard_requires_explicit_scope(image, base, evidence):
    base['load_list'] = []
    base['containers'] = []
    evidence.update(stacks=[], segments=0)
    assert run(image, base, evidence).status == 'complete'
    evidence.pop('complete_view')
    assert 'incomplete_view' in reasons(run(image, base, evidence))


def test_nonload_yard_obstructions_cannot_be_omitted(image, base, evidence):
    base['containers'].append({'id': IDS[2], 'length_ft': 20})
    assert IDS[2] not in base['load_list']
    assert 'missing_required_ids' in reasons(run(image, base, evidence))
    evidence.update(stacks=[], segments=0)
    assert 'missing_required_ids' in reasons(run(image, base, evidence))


def test_actual_state_validates_iso_id(image, base, evidence):
    bad = 'QZXU0000000'
    base['containers'][0]['id'] = bad
    base['load_list'][0] = bad
    evidence['stacks'][0]['boxes'][0] = bad
    assert 'invalid_yard_state' in reasons(run(image, base, evidence))


def test_file_base_and_real_instance(image, evidence, base, tmp_path):
    path = tmp_path / 'base.json'
    path.write_text(json.dumps(base))
    assert run(image, path, evidence).status == 'complete'
    from stowage.model.fixtures import build_instance
    instance = build_instance()
    context, inventory = reading_context(instance)
    assert context['id'] == instance.port.yard.id
    assert len(inventory) == len(instance.containers)


def test_client_cannot_mutate_validator_context(image, base, evidence):
    class Mutating(Client):
        def read(self, image_path, *, layout, inventory):
            layout['blocks'][0]['max_tiers'] = 20
            inventory[0]['id'] = 'UNKNOWN'
            return self.evidence
    evidence['stacks'][0]['height'] = 4
    result = read_yard_picture(image, base_instance=base, client=Mutating(evidence))
    assert 'tier_limit' in reasons(result)


def test_default_is_offline_and_missing_is_question(image, base, monkeypatch, tmp_path):
    monkeypatch.setattr(reader, 'FIXTURE_PATH', tmp_path / 'absent.json')
    result = read_yard_picture(image, base_instance=base)
    assert 'reading_unavailable' in reasons(result)
    assert result.records[-1].mode == Mode.recorded


def test_hash_bound_recorded_replay(image, base, evidence, tmp_path):
    path = tmp_path / 'recorded.json'
    entry = {'image_sha256': sha256_file(image), 'context_sha256': context_sha256(base),
             'mode': 'recorded', 'evidence': evidence, 'records': []}
    path.write_text(json.dumps([entry]))
    client = RecordedClient(path)
    result = read_yard_picture(image, base_instance=base, client=client)
    assert result.status == 'complete'
    assert result.records[-1].mode == Mode.recorded
    image.write_bytes(b'changed image')
    assert 'reading_unavailable' in reasons(read_yard_picture(image, base_instance=base, client=client))


def test_recorded_context_mismatch_is_question(image, base, evidence, tmp_path):
    path = tmp_path / 'recorded.json'
    path.write_text(json.dumps([{'image_sha256': sha256_file(image), 'context_sha256': context_sha256(base),
                                'mode': 'recorded', 'evidence': evidence}]))
    base['port']['yard']['blocks'][0]['n_bays'] = 5
    result = read_yard_picture(image, base_instance=base, client=RecordedClient(path))
    assert 'reading_unavailable' in reasons(result)


def test_reused_recorded_client_clears_previous_receipts(image, base, evidence, tmp_path):
    path = tmp_path / 'recorded.json'
    record = StageRecord(stage='yard_vision.sam', mode=Mode.live,
                         input_sha256='a' * 64, output_sha256='b' * 64, cost_usd=.0025, wall_s=1)
    path.write_text(json.dumps([{'image_sha256': sha256_file(image), 'context_sha256': context_sha256(base),
                                'mode': 'recorded', 'evidence': evidence,
                                'records': [record.model_dump(mode='json')]}]))
    client = RecordedClient(path)
    result = read_yard_picture(image, base_instance=base, client=client)
    assert result.status == 'complete'
    assert result.records[0].stage == 'yard_vision.sam'
    image.write_bytes(b'unmatched-picture')
    result = read_yard_picture(image, base_instance=base, client=client)
    assert 'reading_unavailable' in reasons(result)
    assert client.records == []
    assert [record.stage for record in result.records] == ['yard_vision.validate']


def test_recorded_mode_and_cost_cannot_claim_live_spend(image, base, evidence, tmp_path):
    path = tmp_path / 'recorded.json'
    historical = StageRecord(stage='yard_vision.models', mode=Mode.live,
                             input_sha256='a' * 64, output_sha256='b' * 64, wall_s=9, cost_usd=.1)
    path.write_text(json.dumps([{'image_sha256': sha256_file(image), 'context_sha256': context_sha256(base),
                                'mode': 'recorded', 'evidence': evidence,
                                'records': [historical.model_dump(mode='json')]}]))
    result = read_yard_picture(image, base_instance=base, client=RecordedClient(path))
    assert result.status == 'complete'
    assert all(record.mode == Mode.recorded for record in result.records)
    assert sum(record.cost_usd for record in result.records) == 0
    assert result.records[0].wall_s == 0


def test_client_failure_becomes_question(image, base):
    class Failing(Client):
        def read(self, *args, **kwargs):
            raise RuntimeError('service unavailable')
    assert 'reading_unavailable' in reasons(read_yard_picture(image, base_instance=base, client=Failing(None)))


def test_paid_receipt_survives_client_failure(image, base):
    class Failing(Client):
        def read(self, *args, **kwargs):
            self.records.append(StageRecord(stage='yard_vision.sam', mode=Mode.live,
                input_sha256='a' * 64, output_sha256='b' * 64, cost_usd=.0025, wall_s=1))
            raise RuntimeError('Spark JSON parse failed')
    result = read_yard_picture(image, base_instance=base, client=Failing(None))
    assert 'reading_unavailable' in reasons(result)
    assert result.records[0].stage == 'yard_vision.sam'
    assert result.records[0].cost_usd == .0025


def test_live_model_measurements_carried(image, base, evidence):
    evidence.update(model='sam-3.1+muse-spark-1.3', wall_s=2.5, cost_usd=.03,
                    prompt_sha256='c' * 64)
    result = run(image, base, evidence)
    assert result.status == 'complete'
    assert result.records[0].model == 'sam-3.1+muse-spark-1.3'
    assert result.records[0].cost_usd == .03
    assert result.records[0].wall_s == 2.5


@pytest.mark.parametrize('cost', [-1, float('nan'), float('inf')])
def test_invalid_measurements_abstain(image, base, evidence, cost):
    evidence.update(model='sam-3.1+muse-spark-1.3', cost_usd=cost)
    assert 'reading_unavailable' in reasons(run(image, base, evidence))


@pytest.mark.parametrize('payload', [None, [], 'bad', {'stacks': [None]}])
def test_malformed_evidence_abstains(image, base, payload):
    reasons(run(image, base, payload))
