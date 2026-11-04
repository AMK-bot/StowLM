"""Mutated reciprocal vessel geometry must fail closed at checker entry."""

import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.checker.rules_special import special_violations
from stowage.model import Placement
from tests.checker.test_rules_fixtures import build_instance


def malformed_instance(kind, slot):
    instance = build_instance()
    box = instance.containers[0]
    box.length_ft = 40 if slot.startswith('002') else 20
    box.iso_type = '42G1' if box.length_ft == 40 else '22G1'
    box.gross_mass_kg = 12000
    box.reefer, box.dg, box.oog = False, None, None
    box.special_stowage = []
    box.pod = 'THBKK'
    cell_id = '00101' + slot[-2:]
    cell = next(c for c in instance.vessel.cells if c.id == cell_id)
    stack = next(s for s in instance.vessel.stacks if s.id == cell.stack_id)
    if kind == 'missing_membership':
        stack.cell_ids.remove(cell_id)
    elif kind == 'wrong_owner':
        cell.stack_id = '00301' + ('D' if stack.on_deck else 'H')
    elif kind == 'duplicate_member':
        stack.cell_ids.insert(0, cell_id)
    elif kind == 'reversed_order':
        stack.cell_ids.reverse()
    elif kind == 'missing_sibling':
        instance.vessel.cells = [c for c in instance.vessel.cells if c.id != stack.cell_ids[-1]]
    elif kind == 'foreign_member':
        stack.cell_ids.append('00301' + slot[-2:])
    return instance, box


@pytest.mark.parametrize('kind', ['missing_membership', 'wrong_owner', 'duplicate_member',
                                  'reversed_order', 'missing_sibling', 'foreign_member'])
@pytest.mark.parametrize('slot', ['0010102', '0010182', '0020102', '0020182'])
@pytest.mark.parametrize('entry', ['full', 'placement', 'special'])
def test_inconsistent_reciprocal_geometry_is_hard_missing_data(kind, slot, entry):
    instance, box = malformed_instance(kind, slot)
    ship = instance.onboard.model_copy(update={'placements': [Placement(container_id=box.id, slot=slot)]})
    original = ship.model_dump()
    if entry == 'full':
        violations = check_static_rules(instance, ship, call_index=0).violations
    elif entry == 'placement':
        violations = placement_violations(instance, instance.onboard, box, slot, call_index=0)
    else:
        violations = special_violations(instance, ship, call_index=0)
    assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard' for v in violations)
    assert ship.model_dump() == original


def test_e6_candidate_search_does_not_crash_on_broken_hold_geometry():
    instance, box = malformed_instance('missing_membership', '0010102')
    box.gross_mass_kg = 22000
    ship = instance.onboard.model_copy(update={'placements': [Placement(container_id=box.id, slot='0010182')]})
    violations = check_static_rules(instance, ship, call_index=0).violations
    assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard' for v in violations)


@pytest.mark.parametrize('entry', ['full', 'placement', 'special'])
@pytest.mark.parametrize('record,field', [
    ('stack', 'max_mass_kg'), ('stack', 'max_height_m'), ('stack', 'base_z_m'),
    ('stack', 'max_mass_20_kg'), ('stack', 'max_mass_40_kg'),
    ('cell', 'lcg_m'), ('cell', 'tcg_m'), ('cell', 'vcg_base_m'),
])
@pytest.mark.parametrize('value', [None, float('nan'), float('inf'), True])
def test_nonfinite_or_boolean_geometry_is_hard_missing_data(entry, record, field, value):
    instance = build_instance()
    box = instance.containers[1]
    box.dg = None
    obj = (next(s for s in instance.vessel.stacks if s.id == '00101H') if record == 'stack'
           else next(c for c in instance.vessel.cells if c.id == '0010102'))
    setattr(obj, field, value)
    ship = instance.onboard.model_copy(update={'placements': [Placement(container_id=box.id, slot='0010102')]})
    if entry == 'full':
        violations = check_static_rules(instance, ship, call_index=0).violations
    elif entry == 'placement':
        violations = placement_violations(instance, instance.onboard, box, '0010102', call_index=0)
    else:
        violations = special_violations(instance, ship, call_index=0)
    missing = value is not None or field not in ('max_mass_20_kg', 'max_mass_40_kg')
    assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard' for v in violations) is missing


def test_e6_departure_stops_after_first_compatible_hold_witness(monkeypatch):
    from stowage.checker import rules
    from stowage.checker.rules_weight import e6_departure_violations

    instance = build_instance()
    box = instance.containers[0]
    box.gross_mass_kg = 22000
    box.reefer, box.dg = False, None
    box.special_stowage = []
    ship = instance.onboard.model_copy(update={'placements': [Placement(container_id=box.id, slot='0010182')]})
    checked_slots = []

    def accepted(*args, **kwargs):
        checked_slots.append(args[3])
        return []

    monkeypatch.setattr(rules, 'placement_violations', accepted)
    out = e6_departure_violations(instance, ship, call_index=0)
    assert any(v.rule_id == 'expert.e6.heavy_under_deck' for v in out)
    assert len(checked_slots) == 1


def test_compatible_hold_list_keeps_exhaustive_public_semantics(monkeypatch):
    from stowage.checker import rules
    from stowage.checker.rules_weight import compatible_empty_hold_slots

    instance = build_instance()
    box = instance.containers[0]
    box.reefer, box.dg = False, None
    box.special_stowage = []
    checked_slots = []

    def accepted(*args, **kwargs):
        checked_slots.append(args[3])
        return []

    monkeypatch.setattr(rules, 'placement_violations', accepted)
    result = compatible_empty_hold_slots(instance, instance.onboard, box)
    assert len(result) > 1
    assert result == checked_slots
