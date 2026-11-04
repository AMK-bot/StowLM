"""Synthetic renderer inputs isolate labels; they do not certify physical plans."""
from copy import deepcopy
import json
from pathlib import Path
from app.tests.test_renderer import render_page


def seven_moves():
    record = json.loads(Path('app/tests/fixtures/model_run/instances/fixture_empty_ship/result.json').read_text())
    instance, plan = record['instance'], record['result']['plan']
    plan['moves'].extend([{'kind': 'hatch_cover_off', 'call_index': 0, 'cover_id': 'HC-02-1'},
                          {'kind': 'hatch_cover_on', 'call_index': 0, 'cover_id': 'HC-02-1'}])
    # Synthetic count observations, explicitly separate from a checker verdict.
    frames = []
    for index in range(8):
        frames.append({'state': {'yard': instance['yard'], 'ship': instance['onboard']},
            'move': plan['moves'][index - 1] if index else None, 'highlight': {},
            'counts': {'loads': 0, 'discharges': int(index >= 3), 'relocations': 0,
                'restow_lifts': max(0, min(index, 3) - 1), 'hatch_cover_lifts': 0,
                'total_lifts_now': index, 'gate_moves': 0, 'future': [], 'weighted_nonproductive': index}})
    return {'instance': instance, 'plan': plan, 'frames': frames, 'checker': {},
            'metadata': {'physical_replay_legal': True, 'shippable': False, 'checker_status': 'failed'}}


def test_initial_zeros_are_so_far_with_seven_move_plan_totals():
    replay = seven_moves()
    assert len(replay['frames']) == 8
    page = render_page(replay)
    assert page['metrics-progress']['text'] == 'So far (move 0 / 7)'
    assert page['total-lifts']['text'] == '0'
    assert page['total-lifts-plan-total']['text'] == 'plan total: 7'
    assert page['discharges']['text'] == '0'
    assert page['discharges-plan-total']['text'] == 'plan total: 1'


def test_plan_totals_stay_fixed_when_playback_advances():
    replay = seven_moves()
    page = render_page(replay, index=3)
    assert page['metrics-progress']['text'] == 'So far (move 3 / 7)'
    assert page['total-lifts']['text'] == '3'
    assert page['total-lifts-plan-total']['text'] == 'plan total: 7'
    assert page['future-plan-total']['text'] == 'plan total: ' + str(sum(
        count['restow_lifts'] for count in replay['frames'][-1]['counts']['future']))


def test_illegal_tail_never_labels_reached_prefix_as_plan_totals():
    replay = deepcopy(seven_moves())
    replay['plan']['moves'].append({'kind': 'hatch_cover_on', 'call_index': 0, 'cover_id': 'HC-02-1'})
    replay['metadata']['physical_replay_legal'] = False
    page = render_page(replay)
    assert page['metrics-progress']['text'] == 'So far (move 0 / 8)'
    assert page['total-lifts-plan-total']['text'] == 'executed-prefix total: 7'
    assert 'Plan totals unavailable' in page['metrics-totals-note']['text']
