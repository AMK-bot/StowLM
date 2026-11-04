"""Synthetic placement comparisons; no checker, planner, or model invocation."""
import json
from types import SimpleNamespace as NS

from llm.instructions.schema import CloseBay, DgKeepAway
from stowage.model import MovePlan
from stowage.conventions import make_container_id

from experiments.mvp.reader.hero.visual_delta import build_visual_delta, final_positions

A, B, C, X = [make_container_id('ABCU', serial) for serial in range(4)]


def fixture(*, long=False, subsidiary=False, retained=False, discharge=False):
    dg = NS(imdg_class='3' if subsidiary else '8', subsidiary_risks=['8'] if subsidiary else [], additional=[])
    boxes = [NS(id=A, length_ft=40 if long else 20, dg=dg), NS(id=B, length_ft=20, dg=None),
             NS(id=C, length_ft=20, dg=NS(imdg_class='8', subsidiary_risks=[], additional=[]))]
    return NS(id='synthetic', call_index=0,
              vessel=NS(cells=[NS(id=f'{bay:03d}{row:02d}{tier:02d}')
                                for bay in (1, 3, 5, 7) for row in (1, 2) for tier in (2, 4, 82)]),
              containers=boxes, load_list=[A, B], discharge_list=[C] if discharge else [],
              onboard=NS(placements=[NS(container_id=C, slot='0010104')] if retained or discharge else []))


def move(kind, bid, *, src=None, dst=None):
    value = dict(kind=kind, container_id=bid, call_index=0)
    if src is not None:
        value['src'] = src
    if dst is not None:
        value['dst'] = dst
    return value


def plan(*moves, ballast=None):
    return MovePlan.model_validate(dict(id='p', instance_id='synthetic', call_index=0,
                                       moves=list(moves), ballast=ballast))


def dg():
    return DgKeepAway(type='dg_keep_away', imdg_class='8', min_bays=1,
                      away_from={'kind': 'bay', 'bay': 1})


def test_matched_load_changes_physical_bay_with_slot_and_displacement_evidence():
    instance = fixture()
    before = plan(move('load', A, src='truck', dst='0010182'), move('load', B, src='truck', dst='0030182'))
    after = plan(move('load', A, src='truck', dst='0030182'), move('load', B, src='truck', dst='0010182'))
    delta = build_visual_delta(instance, before, after, dg())
    assert delta['move_signature_changed'] and delta['container_move_signature_changed']
    assert delta['changed_load_placements_count'] == 2
    assert delta['load_before_distinct_bay_count'] == delta['load_after_distinct_bay_count'] == 2
    assert delta['load_total_bay_displacement_20ft'] == 2
    assert delta['matched_dg_changed_load_placements_count'] == 1
    assert delta['matched_dg_changed_physical_bay_count'] == 1
    assert delta['matched_dg_before_physical_bays'] == [1]
    assert delta['matched_dg_after_physical_bays'] == [3]
    assert delta['matched_dg_total_bay_displacement_20ft'] == 1
    assert delta['visible_binding_candidate']
    assert not delta['checker_called'] and not delta['instruction_compliance_verified']
    assert delta['matched_dg_load_boxes'][0]['before_slot'] == '0010182'
    json.dumps(delta, allow_nan=False)


def test_ballast_only_change_never_counts_as_visible():
    instance = fixture()
    before = plan(move('load', A, src='truck', dst='0010102'), move('load', B, src='truck', dst='0030102'),
                  ballast={'tank_masses_kg': {'tank': 1}, 'source': 'finisher'})
    after = before.model_copy(update={'ballast': before.ballast.model_copy(update={'tank_masses_kg': {'tank': 2}})})
    delta = build_visual_delta(instance, before, after, dg())
    assert not delta['move_signature_changed']
    assert delta['changed_load_placements_count'] == 0
    assert not delta['visible_binding_candidate']


def test_order_and_cover_changes_do_not_fake_final_placement_visibility():
    instance = fixture()
    first = move('load', A, src='truck', dst='0010102')
    second = move('load', B, src='truck', dst='0030102')
    before = plan(first, second)
    after = plan(second, first)
    delta = build_visual_delta(instance, before, after, dg())
    assert delta['container_move_signature_changed']
    assert not delta['visible_binding_candidate']
    cover = dict(kind='hatch_cover_off', call_index=0, cover_id='HC-1', dst='quay')
    delta = build_visual_delta(instance, before, plan(first, second, cover), dg())
    assert delta['move_signature_changed'] and not delta['container_move_signature_changed']
    assert not delta['visible_binding_candidate']


def test_row_or_tier_change_is_visible_without_claiming_bay_displacement():
    instance = fixture()
    b = move('load', B, src='truck', dst='0030102')
    before, after = plan(move('load', A, src='truck', dst='0010102'), b), plan(move('load', A, src='truck', dst='0010204'), b)
    delta = build_visual_delta(instance, before, after, dg())
    assert delta['matched_dg_changed_load_placements_count'] == 1
    assert delta['matched_dg_changed_physical_bay_count'] == 0
    assert delta['matched_dg_total_bay_displacement_20ft'] == 0


def test_even_slot_records_both_physical_bays_and_centre_displacement():
    instance = fixture(long=True)
    b = move('load', B, src='truck', dst='0070102')
    before, after = plan(move('load', A, src='truck', dst='0020102'), b), plan(move('load', A, src='truck', dst='0040102'), b)
    row = build_visual_delta(instance, before, after, dg())['matched_dg_load_boxes'][0]
    assert row['before_physical_bays'] == [1, 3]
    assert row['after_physical_bays'] == [3, 5]
    assert row['absolute_bay_displacement_20ft'] == 1


def test_dg_subsidiary_risk_is_matched_and_undg_not_counted():
    instance = fixture(subsidiary=True)
    baseline = plan(move('load', A, src='truck', dst='0010102'), move('load', B, src='truck', dst='0030102'))
    delta = build_visual_delta(instance, baseline, baseline, dg())
    assert len(delta['matched_dg_load_boxes']) == 1
    assert delta['matched_dg_load_boxes'][0]['dg_classes'] == ['3', '8']
    assert delta['load_placements'][1]['matches_intended_dg'] is False


def test_retained_and_discharge_then_restow_follow_move_order():
    instance = fixture(retained=True)
    moves = [move('load', A, src='truck', dst='0010102'), move('load', B, src='truck', dst='0030102'),
             move('restow_off', C, src='0010104'), move('restow_on', C, dst='0050104'),
             move('restow_shift', C, src='0050104', dst='0070104')]
    positions, errors = final_positions(instance, plan(*moves))
    assert not errors and positions[C] == '0070104'
    delta = build_visual_delta(instance, plan(*moves[:2]), plan(*moves), dg())
    assert delta['matched_dg_active_count'] == 2
    assert delta['matched_dg_retained_boxes'][0]['physical_bay_changed']
    assert delta['changed_load_placements_count'] == 0
    assert not delta['visible_binding_candidate']  # criteria demand a loaded box changes
    positions, errors = final_positions(instance, plan(*moves, move('discharge', C, src='0070104', dst='truck')))
    assert C not in positions and not errors


def test_missing_unknown_or_removed_load_is_reported_without_visible_credit():
    instance = fixture()
    before = plan(move('load', A, src='truck', dst='0010102'), move('load', B, src='truck', dst='0030102'))
    after = plan(move('load', A, src='truck', dst='0050102'), move('discharge', B, src='0030102', dst='truck'),
                 move('load', X, src='truck', dst='0070102'))
    delta = build_visual_delta(instance, before, after, dg())
    assert delta['missing_load_after'] == [B]
    assert any('unknown container' in issue for issue in delta['diagnostics']['after'])
    assert not delta['visible_binding_candidate']


def test_non_dg_constraint_does_not_invent_matched_class():
    instance = fixture()
    before = plan(move('load', A, src='truck', dst='0010102'), move('load', B, src='truck', dst='0030102'))
    delta = build_visual_delta(instance, before, before, CloseBay(type='close_bay', bay=1))
    assert delta['intended_dg_class'] is None and not delta['matched_dg_load_boxes']
