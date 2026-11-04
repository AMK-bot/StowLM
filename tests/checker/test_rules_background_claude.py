"""Claude R1/R2: qualification absence cannot hide supplied physical overloads.

Synthetic 68 t columns are explicit fixture data, never inferred ISO limits.
Legacy aggregates are attributable only where their supplied coordinates identify
one physical column; genuinely unknown locations remain D38 assumption notes.
"""

import json

import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.checker.rules_evidence import background_capacity_violations
from stowage.model import Placement
from stowage.model.fixtures import build_instance
from stowage.model.vessel import FixedCargoBox, FixedLoad
from stowage.vessel.fleet_conditions import CONDITION_PREFIX, CONDITION_VERSION


CAP = 68000.0
SID = '00101H'
CID = '0010102'


def _case(mass=0.0):
    instance = build_instance()
    vessel = instance.vessel
    vessel.id, vessel.slice_of, vessel.sources = 'claude_capacity_case', None, []
    vessel.fixed_loads = []
    for stack in vessel.stacks:
        stack.max_mass_kg = stack.max_mass_20_kg = stack.max_mass_40_kg = CAP
    if mass:
        cell = next(cell for cell in vessel.cells if cell.id == CID)
        vessel.fixed_loads = [FixedLoad(
            x_m=cell.lcg_m, y_m=cell.tcg_m, z_m=cell.vcg_base_m + 2.591 / 2,
            mass_kg=float(mass), kind='cargo',
        )]
    candidate = instance.containers[1]
    _clean(candidate)
    instance.onboard = instance.onboard.model_copy(update={'vessel_id': vessel.id, 'placements': []})
    ship = instance.onboard.model_copy(update={'placements': [
        Placement(slot='0010202', container_id=candidate.id)]})
    return instance, ship


def _clean(box):
    box.length_ft, box.iso_type = 20, '22G1'
    box.height_m, box.gross_mass_kg, box.max_gross_mass_kg = 2.591, 12000.0, 100000.0
    box.reefer, box.dg, box.oog, box.special_stowage = False, None, None, []
    box.pod = 'THBKK'


def _audit(vessel, *, mass, limit=CAP, stack=SID, cells=True, qualification='missing_snapshot'):
    # Neither case supplies the original-parameter pin or foreign snapshot.
    vessel.slice_of = 'feeder_500' if qualification == 'missing_digest' else 'foreign_missing_parent'
    info = dict(version=CONDITION_VERSION, base_fixed_loads=[], background_stacks=[dict(
        stack_id=stack, mass_kg=mass, max_mass_20_kg=limit,
        max_cell_mass_kg=30480, on_deck=False)])
    if cells:
        info['background_cells'] = [dict(cell_id=CID, stack_id=stack, mass_kg=mass)]
    vessel.sources = [CONDITION_PREFIX + json.dumps(info)]


def _check(instance, ship, entry):
    if entry == 'full':
        return check_static_rules(instance, ship, call_index=0).violations
    placement = ship.placements[-1]
    box = next(box for box in instance.containers if box.id == placement.container_id)
    partial = ship.model_copy(update={'placements': ship.placements[:-1]})
    return placement_violations(instance, partial, box, placement.slot, call_index=0)


def _weights(out):
    return [v for v in out if v.rule_id == 'expert.physical.stack_weight']


def _assert_overload(out, mass, limit=CAP):
    assert any(v.severity == 'hard' and v.measured == pytest.approx(mass)
               and v.limit == pytest.approx(limit) for v in _weights(out))


@pytest.mark.parametrize('qualification', ['missing_digest', 'missing_snapshot'])
@pytest.mark.parametrize('cells', [False, True], ids=['stack_assertion', 'cell_and_stack'])
def test_direct_supplied_340t_on_68t_is_hard_before_missing_parent_return(qualification, cells):
    instance, _ = _case(340000)
    _audit(instance.vessel, mass=340000, qualification=qualification, cells=cells)
    emitted, notes = [], []
    background_capacity_violations(
        instance.vessel, lambda *args, **kwargs: emitted.append((args, kwargs)), note=notes.append)
    assert any(args[0] == 'expert.physical.stack_weight' and values.get('measured') == 340000
               and values.get('limit') == CAP for args, values in emitted)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('qualification', ['missing_digest', 'missing_snapshot'])
def test_supplied_340t_on_68t_missing_parent_qualification_stays_hard(entry, qualification):
    instance, ship = _case(340000)
    _audit(instance.vessel, mass=340000, qualification=qualification)
    _assert_overload(_check(instance, ship, entry), 340000)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('qualification', ['missing_digest', 'missing_snapshot'])
def test_missing_parent_cannot_hide_contradictory_audit_on_safe_actual_stack(entry, qualification):
    instance, ship = _case(CAP)
    _audit(instance.vessel, mass=340000, qualification=qualification)
    info = json.loads(instance.vessel.sources[0][len(CONDITION_PREFIX):])
    # Actual aggregate and cell attribution agree at the physical boundary.
    # Only the claimed stack total is inflated; missing parent qualification
    # cannot turn this supplied contradiction into a mere uncertainty note.
    info['background_cells'][0]['mass_kg'] = CAP
    instance.vessel.sources = [CONDITION_PREFIX + json.dumps(info)]
    out = _check(instance, ship, entry)
    assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard'
               and 'background' in v.message.lower() for v in out)
    assert not _weights(out)  # Actual mass is 68 t, not the audit's 340 t.


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_audit_only_stack_assertion_is_enforced_without_available_parent_geometry(entry):
    instance, ship = _case()
    _audit(instance.vessel, mass=340000, stack='00501H', cells=False)
    _assert_overload(_check(instance, ship, entry), 340000)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('unrelated_candidate', [False, True])
@pytest.mark.parametrize('mass,failing', [(56000.0, False), (56001.0, True)])
def test_audit_only_mass_combines_with_planned_column_without_fixed_load(
        entry, unrelated_candidate, mass, failing):
    instance, ship = _case()
    _audit(instance.vessel, mass=mass, cells=False)
    assert not instance.vessel.fixed_loads
    if unrelated_candidate:
        existing = instance.containers[2]
        _clean(existing)
        ship.placements.insert(0, Placement(slot='0010102', container_id=existing.id))
    else:
        ship.placements[0].slot = '0010102'
    out = _check(instance, ship, entry)
    assert bool(_weights(out)) is failing
    if failing:
        _assert_overload(out, mass + 12000)


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_physical_catalog_limit_beats_forged_audit_limit_without_parent(entry):
    instance, ship = _case(340000)
    _audit(instance.vessel, mass=340000, limit=1000000)
    _assert_overload(_check(instance, ship, entry), 340000, CAP)


@pytest.mark.parametrize('qualification', ['missing_digest', 'missing_snapshot'])
def test_direct_physical_limit_beats_forged_audit_limit_before_parent_return(qualification):
    instance, _ = _case(340000)
    _audit(instance.vessel, mass=340000, limit=1000000, qualification=qualification)
    emitted = []
    background_capacity_violations(
        instance.vessel, lambda *args, **kwargs: emitted.append((args, kwargs)))
    assert any(args[0] == 'expert.physical.stack_weight' and values.get('measured') == 340000
               and values.get('limit') == CAP for args, values in emitted)


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_known_column_legacy_500t_aggregate_is_hard_without_boxes_or_audit(entry):
    instance, ship = _case(500000)
    assert instance.vessel.fixed_loads[0].cargo_boxes is None
    assert not instance.vessel.sources
    _assert_overload(_check(instance, ship, entry), 500000)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('mass,failing', [(CAP, False), (CAP + 1, True)])
def test_attributable_legacy_aggregate_obeys_exact_physical_stack_boundary(entry, mass, failing):
    instance, ship = _case(mass)
    out = _check(instance, ship, entry)
    assert bool(_weights(out)) is failing
    if failing:
        _assert_overload(out, mass)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('upper_mass,failing', [(28000.0, False), (28001.0, True)])
def test_multiple_legacy_aggregates_combine_on_one_physical_stack(entry, upper_mass, failing):
    instance, ship = _case(40000)
    bottom = instance.vessel.fixed_loads[0]
    instance.vessel.fixed_loads.append(FixedLoad(
        x_m=bottom.x_m, y_m=bottom.y_m, z_m=bottom.z_m + 2.591,
        mass_kg=upper_mass, kind='cargo'))
    out = _check(instance, ship, entry)
    assert bool(_weights(out)) is failing
    if failing:
        _assert_overload(out, 40000 + upper_mass)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('unrelated_candidate', [False, True])
@pytest.mark.parametrize('mass,failing', [(56000.0, False), (56001.0, True)])
@pytest.mark.parametrize('qualification', [None, 'missing_snapshot', 'missing_digest'])
def test_legacy_background_and_planned_mass_share_limit_despite_missing_qualification(
        entry, unrelated_candidate, mass, failing, qualification):
    instance, ship = _case(mass)
    if qualification is not None:
        _audit(instance.vessel, mass=mass, qualification=qualification)
    if unrelated_candidate:
        existing = instance.containers[2]
        _clean(existing)
        ship.placements.insert(0, Placement(slot='0010104', container_id=existing.id))
    else:
        ship.placements[0].slot = '0010104'
    out = _check(instance, ship, entry)
    assert bool(_weights(out)) is failing
    if failing:
        _assert_overload(out, mass + 12000)


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_d42_aggregate_is_not_counted_again_as_legacy_cargo(entry):
    instance, ship = _case(CAP)
    vessel = instance.vessel
    load = vessel.fixed_loads[0]
    load.cargo_boxes = [FixedCargoBox(
        cell_ids=[CID], stack_id=SID, tier=2, length_ft=20, height_m=2.591,
        gross_mass_kg=CAP, x_m=load.x_m, y_m=load.y_m, z_m=load.z_m)]
    vessel.fixed_load_cells = [c for c in vessel.cells if c.stack_id == SID]
    vessel.fixed_load_stacks = [s for s in vessel.stacks if s.id == SID]
    vessel.cells = [c for c in vessel.cells if c.stack_id != SID]
    vessel.stacks = [s for s in vessel.stacks if s.id != SID]
    assert not _weights(_check(instance, ship, entry))


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_unattributable_legacy_aggregate_remains_note_without_invented_capacity(entry):
    # Larger than all supplied column capacities combined: a global cells×R or
    # summed-column sanity cap would invent an unsupported physical attribution.
    instance, ship = _case(50000000)
    load = instance.vessel.fixed_loads[0]
    load.x_m, load.y_m, load.z_m = -1000.0, 999.0, -1000.0
    assert not _weights(_check(instance, ship, entry))
    report = check_static_rules(instance, ship, call_index=0)
    assert not [v for v in report.violations if v.severity == 'hard']
    assert any(key.startswith('note: ') and 'background' in key.lower() for key in report.metrics)


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_legacy_coordinate_attribution_distinguishes_deck_from_hold(entry):
    instance, ship = _case(70000)
    vessel = instance.vessel
    deck = next(stack for stack in vessel.stacks if stack.id == '00101D')
    deck.max_mass_kg = deck.max_mass_20_kg = deck.max_mass_40_kg = 90000
    cell = next(cell for cell in vessel.cells if cell.id == '0010182')
    vessel.fixed_loads[0].z_m = cell.vcg_base_m + 2.591 / 2
    assert not _weights(_check(instance, ship, entry))


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('mass,failing', [(90000.0, False), (90001.0, True)])
def test_unknown_legacy_length_uses_supplied_upper_bound_and_discloses_uncertainty(entry, mass, failing):
    instance, ship = _case(mass)
    stack = next(stack for stack in instance.vessel.stacks if stack.id == SID)
    stack.max_mass_kg, stack.max_mass_20_kg, stack.max_mass_40_kg = 60000, CAP, 90000
    out = _check(instance, ship, entry)
    assert bool(_weights(out)) is failing
    if failing:
        _assert_overload(out, mass, 90000)
    report = check_static_rules(instance, ship, call_index=0)
    assert any('length unavailable' in key and 'upper-bound screen' in key for key in report.metrics)


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_known_planned_twenty_foot_box_activates_twenty_rating_for_legacy_mixed_column(entry):
    instance, ship = _case(70000)
    stack = next(stack for stack in instance.vessel.stacks if stack.id == SID)
    stack.max_mass_kg, stack.max_mass_20_kg, stack.max_mass_40_kg = 60000, CAP, 90000
    ship.placements[0].slot = '0010104'
    _assert_overload(_check(instance, ship, entry), 82000, CAP)


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_ambiguous_legacy_column_location_does_not_invent_mass_distribution(entry):
    instance, ship = _case(50000000)
    deck = next(stack for stack in instance.vessel.stacks if stack.id == '00101D')
    deck.base_z_m = 2.0  # Both supplied columns now contain the aggregate CG.
    assert not _weights(_check(instance, ship, entry))
    report = check_static_rules(instance, ship, call_index=0)
    assert any('no unique physical support-column attribution' in key for key in report.metrics)
