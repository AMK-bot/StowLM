import json

from PIL import Image
import pytest

from .metrics import aggregate_scores, score_yard
from .render import overlay_segments, render_yard


@pytest.fixture
def scene():
    yard = {'id': 'synthetic', 'blocks': [{'id': 'A', 'n_bays': 3, 'n_rows': 2,
             'max_tiers': 3, 'allowed_lengths_ft': [20, 40]}]}
    state = {'yard_id': 'synthetic', 'stacks': [
        {'key': 'A-001-01', 'length_ft': 40, 'boxes': ['TEST0000001', 'TEST0000002']},
        {'key': 'A-003-02', 'length_ft': 20, 'boxes': ['TEST0000003']}], 'quay_buffer': []}
    return state, yard


def test_render_and_evidence_overlay(scene, tmp_path):
    state, yard = scene
    original, overlay = tmp_path / 'yard.png', tmp_path / 'sam.png'
    metadata = render_yard(state, yard, original)
    json.dumps(metadata)
    assert metadata['box_count'] == 3
    assert metadata['stack_count'] == 2
    assert len(metadata['slots']) == 6
    assert metadata['quay_buffer']['boxes'] == []
    assert metadata['quay_buffer']['bbox'][3] < metadata['height']
    assert Image.open(original).size == (metadata['width'], metadata['height'])
    assert [b['tier'] for b in metadata['boxes']] == [1, 2, 1]
    # Real evidence comes from SAM. Only exercise overlay coordinates here.
    overlay_segments(original, [{'bbox': [30, 80, 70, 120]}], overlay)
    assert Image.open(overlay).getpixel((30, 90)) == (255, 237, 66)


def test_render_rejects_unseen_quay_and_unknown_location(scene, tmp_path):
    state, yard = scene
    with pytest.raises(ValueError, match='quay_buffer'):
        render_yard(dict(state, quay_buffer=['TEST0000004']), yard, tmp_path / 'bad.png')
    with pytest.raises(ValueError, match='unknown stack'):
        render_yard(dict(state, stacks=[{'key': 'B-001-01', 'length_ft': 20, 'boxes': ['X']}]), yard, tmp_path / 'bad.png')


def test_exact_and_silent_wrong_are_not_conflated(scene):
    state, _ = scene
    good = score_yard(state, state, accepted=True)
    assert good['id'] == {'correct': 3, 'total': 3, 'accuracy': 1.0}
    assert good['silent_wrong_stacks'] == 0
    bad = json.loads(json.dumps(state))
    bad['stacks'][0]['boxes'].reverse()
    score = score_yard(state, bad, accepted=True)
    assert score['box_count']['exact']
    assert score['stack_height']['accuracy'] == 1.0
    assert score['id']['correct'] == 1
    assert score['silent_wrong_stacks'] == 1
    assert score_yard(state, bad)['silent_wrong_stacks'] == 0


def test_unknown_is_abstention_and_extra_stack_penalized(scene):
    state, _ = scene
    read = {'stacks': [{'key': 'A-001-01', 'height': 2, 'boxes': [None, None], 'length_ft': 40},
                       {'key': 'A-002-02', 'height': 1, 'boxes': ['WRONG'], 'length_ft': 20}]}
    score = score_yard(state, read, accepted=True)
    assert score['stack_height'] == {'correct': 1, 'total': 3, 'accuracy': 1 / 3}
    assert score['id']['total'] == 4
    assert score['abstentions']['id'] == 3
    assert score['silent_wrong_stacks'] == 3
    aggregate = aggregate_scores([score, score])
    assert aggregate['id']['total'] == 8
    assert aggregate['silent_wrong_stacks'] == 6


def test_unknown_count_never_becomes_exact(scene):
    state, _ = scene
    score = score_yard(state, {'stacks': [{'key': 'A-001-01', 'height': None}]})
    assert score['box_count']['observed'] is None
    assert score['box_count']['accuracy'] == 0.0
    assert not score['box_count']['exact']


@pytest.mark.parametrize('change', [
    {'key': None}, {'key': ['unhashable']}, {'key': {'unhashable': True}},
    {'height': '2'}, {'height': True}, {'height': -1}, {'height': 0},
    {'boxes': 'not-a-list'}, {'boxes': [True, 'TEST0000002']},
    {'height': 3}, {'length_ft': '40'},
])
def test_malformed_observation_abstains_without_discarding_sam_count(scene, change):
    state, _ = scene
    read = json.loads(json.dumps(state))
    read['stacks'][0].update(change)
    score = score_yard(state, read, detected_box_count=3)
    assert score['malformed_evidence']
    assert score['malformed_reasons']
    assert score['box_count']['exact']
    assert score['box_count']['observed'] == 3
    assert score['stack_height'] == {'correct': 0, 'total': 2, 'accuracy': 0.0}
    assert score['id'] == {'correct': 0, 'total': 3, 'accuracy': 0.0}
    assert score['abstentions']['id'] == 3
    assert score['silent_wrong_stacks'] == 0


@pytest.mark.parametrize('observation', [None, 'bad', [], {'stacks': None}, {'stacks': [None]}])
def test_absent_or_malformed_observations_never_score_positive(scene, observation):
    state, _ = scene
    score = score_yard(state, observation)
    assert not score['box_count']['exact']
    assert score['stack_height']['accuracy'] == 0.0
    assert score['id']['accuracy'] == 0.0


def test_empty_truth_does_not_hide_malformed_observation():
    score = score_yard({'stacks': []}, {'state': []})
    assert score['malformed_evidence']
    assert not score['box_count']['exact']
    assert score['stack_height']['accuracy'] == 0.0
    assert score['id']['accuracy'] == 0.0
    assert aggregate_scores([score])['id']['accuracy'] == 0.0


def test_duplicate_stack_is_full_abstention_and_reported_in_aggregate(scene):
    state, _ = scene
    read = json.loads(json.dumps(state))
    read['stacks'].append(read['stacks'][0])
    score = score_yard(state, read)
    assert score['malformed_evidence']
    assert score['box_count']['observed'] is None
    assert not score['box_count']['exact']
    assert score['stack_height']['total'] == 2
    assert score['stack_height']['correct'] == 0
    assert score['id']['correct'] == 0
    assert aggregate_scores([score])['malformed_images'] == 1


@pytest.mark.parametrize('count', [True, '3', -1, float('nan')])
def test_invalid_sam_count_cannot_claim_exact_score(scene, count):
    state, _ = scene
    score = score_yard(state, state, detected_box_count=count)
    assert score['malformed_evidence']
    assert score['box_count']['observed'] is None
    assert score['id']['correct'] == 0
