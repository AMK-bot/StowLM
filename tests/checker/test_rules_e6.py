"""D25 departure fallback and a sound, deliberately optimistic completion bound."""
import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.checker.rules_weight import (
    E6, compatible_empty_hold_slots, e6_completion_possible, e6_departure_violations,
)
from stowage.model import Placement
from tests.checker.test_rules_fixtures import build_instance


def instance_with_hold(*hold_cells):
    instance = build_instance()
    instance.rule_overrides = {}
    instance.containers = [box.model_copy(update={
        'iso_type': '22G1', 'length_ft': 20, 'height_m': 2.591,
        'tare_kg': 2200, 'gross_mass_kg': 12000, 'reefer': False,
        'reefer_setpoint_c': None, 'dg': None, 'special_stowage': [],
        'oog': None, 'pod': 'THBKK',
    }) for box in instance.containers]
    instance.containers[0].gross_mass_kg = 22000
    instance.vessel.cells = [cell for cell in instance.vessel.cells
                             if cell.id in hold_cells or int(cell.id[-2:]) >= 80]
    for stack in instance.vessel.stacks:
        stack.cell_ids = [cid for cid in stack.cell_ids
                          if cid in {cell.id for cell in instance.vessel.cells}]
    return instance


def ship_with(instance, *slots):
    return instance.onboard.model_copy(update={'placements': [
        Placement(container_id=box.id, slot=slot)
        for box, slot in zip(instance.containers, slots)
    ]})


@pytest.mark.parametrize('occupied', [False, True], ids=['empty_hold_fails', 'full_hold_passes'])
def test_e6_departure_fallback(occupied):
    instance = instance_with_hold('0010102')
    ship = ship_with(instance, '0010182', *(['0010102'] if occupied else []))
    assert bool(e6_departure_violations(instance, ship, call_index=0)) is (not occupied)
    assert any(v.rule_id == E6 for v in check_static_rules(instance, ship, call_index=0).violations) is (not occupied)


def test_e6_incremental_deck_heavy_is_completion_time_condition():
    instance = instance_with_hold('0010102')
    empty = ship_with(instance)
    assert not any(v.rule_id == E6 for v in placement_violations(
        instance, empty, instance.containers[0], '0010182', call_index=0))
    partial = ship_with(instance, '0010182')
    assert e6_completion_possible(instance, partial, instance.containers[1:])
    assert not e6_completion_possible(instance, partial, [])


@pytest.mark.parametrize('fallback', [False, True])
def test_e6_fallback_override_has_priority(fallback):
    instance = instance_with_hold('0010102')
    instance.rule_overrides[E6] = {'allow_on_deck_if_no_compatible_hold_cell': fallback}
    ship = ship_with(instance, '0010182', '0010102')
    assert bool(e6_departure_violations(instance, ship, call_index=0)) is (not fallback)
    assert e6_completion_possible(instance, ship, instance.containers[2:]) is fallback


def test_e6_threshold_override_has_priority():
    instance = instance_with_hold('0010102')
    instance.rule_overrides[E6] = {'heavy_threshold': {'value': 23000}}
    ship = ship_with(instance, '0010182')
    assert not e6_departure_violations(instance, ship, call_index=0)


@pytest.mark.parametrize('reason', ['length', 'reefer', 'stack_mass', 'stack_height', 'weight_order'])
def test_e6_only_compatible_hold_opportunities_count(reason):
    instance = instance_with_hold('0010102', '0010104')
    box = instance.containers[0]
    slots = ['0010182']
    if reason == 'length':
        box.length_ft, box.iso_type = 40, '42G1'
        slots[0] = '0020182'
    elif reason == 'reefer':
        box.reefer = True
    elif reason == 'stack_mass':
        next(s for s in instance.vessel.stacks if s.id == '00101H').max_mass_kg = 21000
    elif reason == 'stack_height':
        next(s for s in instance.vessel.stacks if s.id == '00101H').max_height_m = 2.5
    else:
        slots.append('0010102')  # M below H would violate E1/E2.
    ship = ship_with(instance, *slots)
    assert not compatible_empty_hold_slots(instance, ship, box)
    assert not e6_departure_violations(instance, ship, call_index=0)


def test_e6_one_occupied_support_blocks_forty_foot_opportunity():
    instance = instance_with_hold('0010102', '0030102')
    box = instance.containers[0]
    box.length_ft, box.iso_type = 40, '42G1'
    empty_hold = ship_with(instance, '0020182')
    assert compatible_empty_hold_slots(instance, empty_hold, box) == ['0020102']
    blocked = ship_with(instance, '0020182', '0010102')
    assert not compatible_empty_hold_slots(instance, blocked, box)
    assert e6_completion_possible(instance, blocked, [])


def test_e6_remaining_cargo_can_remove_compatibility_without_filling_all_cells():
    instance = instance_with_hold('0010102', '0010104')
    next(s for s in instance.vessel.stacks if s.id == '00101H').max_mass_kg = 22000
    instance.containers[1].gross_mass_kg = 22000
    before = ship_with(instance, '0010182')
    assert compatible_empty_hold_slots(instance, before, instance.containers[0]) == ['0010102']
    assert e6_completion_possible(instance, before, [instance.containers[1]])
    after = ship_with(instance, '0010182', '0010102')
    assert not e6_departure_violations(instance, after, call_index=0)
    assert e6_completion_possible(instance, after, [])


def test_e6_invalid_fallback_fails_closed_in_departure_report():
    instance = instance_with_hold('0010102')
    instance.rule_overrides[E6] = {'allow_on_deck_if_no_compatible_hold_cell': 'yes'}
    violations = e6_departure_violations(instance, ship_with(instance, '0010182'), call_index=0)
    assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard' for v in violations)
