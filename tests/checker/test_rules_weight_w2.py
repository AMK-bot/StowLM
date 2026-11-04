"""Synthetic C1/C2 acceptance examples under both catalogue regimes."""
import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.checker.rules_weight import (
    E6, e6_completion_possible, e6_departure_violations, weight_soft_metrics,
)
from stowage.model import Placement
from stowage.rules.catalogue import rule_freeze_v1
from tests.checker.test_rules_fixtures import build_instance

E1 = 'expert.e1.heavy_bottom'
E2 = 'expert.e2.weight_order'


def _case(masses, slots=None, lengths=None):
    instance = build_instance()
    slots = slots or ['0010102', '0010104', '0010106'][:len(masses)]
    lengths = lengths or [20] * len(masses)
    instance.rule_overrides = {}
    instance.containers = [box.model_copy(update={
        'gross_mass_kg': mass, 'tare_kg': 2200, 'length_ft': length,
        'iso_type': '22G1' if length == 20 else '42G1', 'height_m': 2.591,
        'reefer': False, 'reefer_setpoint_c': None, 'dg': None,
        'special_stowage': [], 'oog': None, 'pod': 'THBKK',
    }) for box, mass, length in zip(instance.containers, masses, lengths)]
    # Add a third synthetic physical tier when an all-lower-boxes example needs it.
    if any(slot.endswith('06') for slot in slots):
        cell = next(c for c in instance.vessel.cells if c.id == '0010104')
        instance.vessel.cells.append(cell.model_copy(update={'id': '0010106', 'vcg_base_m': 7.182}))
        stack = next(s for s in instance.vessel.stacks if s.id == cell.stack_id)
        stack.cell_ids.append('0010106')
        stack.max_height_m = 9.0
    ship = instance.onboard.model_copy(update={'placements': [
        Placement(container_id=box.id, slot=slot)
        for box, slot in zip(instance.containers, slots)
    ]})
    return instance, ship


def _violations(instance, ship, incremental=False):
    if incremental:
        upper = ship.placements[-1]
        return placement_violations(instance, ship.model_copy(update={'placements': ship.placements[:-1]}),
                                    instance.containers[-1], upper.slot, call_index=0)
    return check_static_rules(instance, ship, call_index=0).violations


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('incremental', [False, True])
@pytest.mark.parametrize('lower,upper', [(12000, 12000), (12000, 12001), (12001, 12000)])
def test_c1_gross_order_replaces_same_class_freedom(enabled, incremental, lower, upper):
    instance, ship = _case([lower, upper])
    # Yard labels and all legacy class parameters must have no ship effect on.
    instance.containers[0].weight_class = 'H'
    instance.containers[1].weight_class = 'L'
    with rule_freeze_v1(enabled=enabled):
        breaches = [v for v in _violations(instance, ship, incremental) if v.rule_id in {E1, E2}]
    assert {v.rule_id for v in breaches} == ({E1, E2} if enabled and upper > lower else set())
    assert all(v.severity == 'hard' and v.unit == 'kg' for v in breaches)


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('delta,expected', [(499, False), (500, False), (501, True)])
def test_c1_exact_tolerance_is_inclusive_and_shared(enabled, delta, expected):
    instance, ship = _case([12000, 12000 + delta])
    instance.rule_overrides = {E2: {'weight_order_tolerance_kg': {'value': 500}}}
    with rule_freeze_v1(enabled=enabled):
        breaches = [v for v in _violations(instance, ship) if v.rule_id in {E1, E2}]
    assert {v.rule_id for v in breaches} == ({E1, E2} if enabled and expected else set())
    assert all(v.limit == 12500 for v in breaches)


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('incremental', [False, True])
def test_c1_nonadjacent_inversion_cannot_accumulate_tolerance(enabled, incremental):
    instance, ship = _case([12000, 12500, 13000])
    instance.rule_overrides = {E2: {'weight_order_tolerance_kg': 500}}
    with rule_freeze_v1(enabled=enabled):
        breaches = [v for v in _violations(instance, ship, incremental) if v.rule_id in {E1, E2}]
    assert {v.rule_id for v in breaches} == ({E1, E2} if enabled else set())
    assert all(v.slots == ['0010102', '0010106'] for v in breaches)


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('incremental', [False, True])
@pytest.mark.parametrize('lighter_support', [0, 1])
def test_c1_long_box_compares_full_gross_with_each_twenty(enabled, incremental, lighter_support):
    lower = [16000, 16000]
    lower[lighter_support] = 14000
    instance, ship = _case([*lower, 15000], ['0010102', '0030102', '0020104'], [20, 20, 40])
    with rule_freeze_v1(enabled=enabled):
        breaches = [v for v in _violations(instance, ship, incremental) if v.rule_id in {E1, E2}]
    assert {v.rule_id for v in breaches} == ({E1, E2} if enabled else set())
    assert all(v.containers == [instance.containers[lighter_support].id, instance.containers[2].id]
               and v.measured == 15000 and v.limit == 14000 for v in breaches)


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('incremental', [False, True])
@pytest.mark.parametrize('upper', [14999, 15001])
def test_c1_twenty_above_long_uses_full_long_gross_mass(enabled, incremental, upper):
    instance, ship = _case([15000, upper], ['0020102', '0010104'], [40, 20])
    with rule_freeze_v1(enabled=enabled):
        breaches = [v for v in _violations(instance, ship, incremental) if v.rule_id in {E1, E2}]
    assert {v.rule_id for v in breaches} == ({E1, E2} if enabled and upper > 15000 else set())
    assert all(v.measured == upper and v.limit == 15000 for v in breaches)
    # The independent physical no-20-on-40 rule still applies to this example.


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('new_lower', [False, True])
def test_c1_inherited_breach_tolerated_but_new_lower_witness_is_hard(enabled, new_lower):
    instance, ship = _case([12000, 12100, 13000])
    instance.stage = 'B'
    instance.onboard = ship.model_copy(update={'placements': [
        p for i, p in enumerate(ship.placements) if not (new_lower and i == 1)
    ]})
    with rule_freeze_v1(enabled=enabled):
        breaches = [v for v in _violations(instance, ship) if v.rule_id in {E1, E2}]
    assert bool(breaches) is enabled
    if enabled:
        top = [v for v in breaches if v.containers[-1] == instance.containers[2].id]
        assert len(top) == 2
        assert all(v.inherited is (not new_lower) for v in top)
        assert all(v.severity == ('hard' if new_lower else 'tolerated') for v in top)


@pytest.mark.parametrize('invalid', [None, True, -1, float('nan'), '0'])
def test_c1_invalid_active_tolerance_fails_closed(invalid):
    instance, ship = _case([12000, 13000])
    instance.rule_overrides = {E2: {'weight_order_tolerance_kg': invalid}}
    with rule_freeze_v1(enabled=True):
        assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard'
                   for v in _violations(instance, ship))


def test_c1_legacy_class_parameters_have_no_ship_effect_after_activation():
    instance, ship = _case([12000, 12000])
    instance.rule_overrides = {E1: {'heavy_threshold': None}, E2: {
        'medium_min_kg': None, 'heavy_min_kg': None, 'tolerance': None,
        'order_mode': 'invalid', 'class_order': ['invalid'],
    }}
    with rule_freeze_v1(enabled=True):
        assert not any(v.rule_id in {E1, E2, 'checker.missing_vessel_data'}
                       for v in _violations(instance, ship))


@pytest.mark.parametrize('enabled', [False, True])
def test_c2_soft_never_rejects_and_completion_is_unrestricted(enabled):
    instance, ship = _case([20000], ['0010182'])
    with rule_freeze_v1(enabled=enabled):
        report = check_static_rules(instance, ship, call_index=0)
        assert any(v.rule_id == E6 for v in report.violations) is (not enabled)
        assert bool(e6_departure_violations(instance, ship, call_index=0)) is (not enabled)
        assert e6_completion_possible(instance, ship, []) is enabled
        assert ('heavy_on_deck_count' in report.metrics) is enabled
        if enabled:
            assert report.metrics['heavy_on_deck_count'] == 1.0


@pytest.mark.parametrize('enabled', [False, True])
def test_c2_metric_counts_boxes_inclusive_threshold_and_ignores_hold(enabled):
    instance, ship = _case([20000, 19999, 20000], ['0010182', '0030182', '0010102'])
    with rule_freeze_v1(enabled=enabled):
        assert weight_soft_metrics(instance, ship) == ({'heavy_on_deck_count': 1.0} if enabled else {})
    instance.rule_overrides[E6] = {'heavy_on_deck_threshold_kg': {'value': 19999}}
    with rule_freeze_v1(enabled=enabled):
        assert weight_soft_metrics(instance, ship) == ({'heavy_on_deck_count': 2.0} if enabled else {})


def test_c2_metric_counts_a_long_box_once_for_both_supports():
    instance, ship = _case([20000], ['0020182'], [40])
    with rule_freeze_v1(enabled=True):
        assert weight_soft_metrics(instance, ship) == {'heavy_on_deck_count': 1.0}


def test_c2_invalid_soft_policy_is_evidence_not_rejection():
    instance, ship = _case([22000], ['0010182'])
    instance.rule_overrides[E6] = {'heavy_on_deck_threshold_kg': None,
                                  'heavy_threshold': None,
                                  'allow_on_deck_if_no_compatible_hold_cell': False}
    with rule_freeze_v1(enabled=True):
        assert e6_departure_violations(instance, ship, call_index=0) == []
        assert e6_completion_possible(instance, ship, [])
        metrics = weight_soft_metrics(instance, ship)
        assert 'heavy_on_deck_count' not in metrics
        assert any(key.startswith('note: heavy_on_deck_count unavailable') for key in metrics)
