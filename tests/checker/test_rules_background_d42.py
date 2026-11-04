"""D42 physical stack capacity uses actual boxes, independently of C3 audits.

All inputs are synthetic. The fixture carries only in-memory geometry and never
builds a production hull or loading condition. Mutations represent public
checker inputs that bypass model validators.
"""

import json

import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.model import Placement
from stowage.model.fixtures import build_instance
from stowage.model.vessel import FixedCargoBox, FixedLoad
from stowage.vessel.fleet_conditions import CONDITION_PREFIX, CONDITION_VERSION


def _case(specs, *, cap=24000.0):
    """Specs are (support cell ids, actual box gross mass) pairs."""
    instance = build_instance()
    vessel = instance.vessel
    vessel.id, vessel.slice_of, vessel.sources = 'd42_capacity_test', None, []
    vessel.fixed_loads = []
    parent = vessel.model_copy(deep=True)
    cells = {cell.id: cell for cell in vessel.cells}
    support_sids = {cells[cid].stack_id for ids, _ in specs for cid in ids}
    vessel.fixed_load_stacks = [stack.model_copy(deep=True) for stack in vessel.stacks
                                if stack.id in support_sids]
    vessel.fixed_load_cells = [cell.model_copy(deep=True) for cell in vessel.cells
                               if cell.stack_id in support_sids]
    for stack in vessel.fixed_load_stacks:
        stack.max_mass_20_kg = cap
        stack.max_mass_40_kg = cap
    # Keep unrelated decision geometry so incremental checks add a real box.
    vessel.stacks = [stack for stack in vessel.stacks if stack.id not in support_sids]
    vessel.cells = [cell for cell in vessel.cells if cell.stack_id not in support_sids]
    for ids, mass in specs:
        support = [cells[cid] for cid in ids]
        box = FixedCargoBox(
            cell_ids=ids, length_ft=20 if len(ids) == 1 else 40,
            height_m=2.591, gross_mass_kg=float(mass),
            x_m=sum(cell.lcg_m for cell in support) / len(support),
            y_m=sum(cell.tcg_m for cell in support) / len(support),
            z_m=support[0].vcg_base_m + 2.591 / 2,
            stack_id=support[0].stack_id, tier=int(ids[0][-2:]),
        )
        vessel.fixed_loads.append(FixedLoad(
            x_m=box.x_m, y_m=box.y_m, z_m=box.z_m, mass_kg=float(mass),
            kind='cargo', cargo_boxes=[box],
        ))
    candidate = instance.containers[1]
    candidate.length_ft, candidate.iso_type = 20, '22G1'
    candidate.gross_mass_kg, candidate.max_gross_mass_kg = 12000.0, 100000.0
    candidate.height_m, candidate.pod = 2.591, 'THBKK'
    candidate.reefer, candidate.dg, candidate.oog, candidate.special_stowage = False, None, None, []
    instance.onboard = instance.onboard.model_copy(update={'vessel_id': vessel.id, 'placements': []})
    ship = instance.onboard.model_copy(update={'placements': [
        Placement(slot='0010202', container_id=candidate.id)]})
    return instance, ship, parent


def _check(instance, ship, entry):
    if entry == 'full':
        return check_static_rules(instance, ship, call_index=0).violations
    placement = ship.placements[-1]
    candidate = next(box for box in instance.containers if box.id == placement.container_id)
    partial = ship.model_copy(update={'placements': ship.placements[:-1]})
    return placement_violations(instance, partial, candidate, placement.slot, call_index=0)


def _overloads(violations):
    return [v for v in violations if v.rule_id == 'expert.physical.stack_weight']


def _assert_overload(violations, *, mass, limit):
    assert any(v.severity == 'hard' and v.measured == pytest.approx(mass)
               and v.limit == pytest.approx(limit) for v in _overloads(violations))


def _use_supports_as_decision_geometry(instance):
    # Public mutable inputs may combine background and decision boxes in one
    # physical stack even though the constructor keeps the two catalogs apart.
    vessel = instance.vessel
    vessel.cells.extend(vessel.fixed_load_cells)
    vessel.stacks.extend(vessel.fixed_load_stacks)
    vessel.fixed_load_cells, vessel.fixed_load_stacks = [], []


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('cap,failing', [(24000.0, False), (23999.0, True)])
def test_no_audit_background_uses_actual_box_masses_per_stack(entry, cap, failing):
    instance, ship, _ = _case([(['0010102'], 12000), (['0010104'], 12000)], cap=cap)
    assert not any(source.startswith(CONDITION_PREFIX) for source in instance.vessel.sources)
    out = _check(instance, ship, entry)
    assert bool(_overloads(out)) is failing
    if failing:
        _assert_overload(out, mass=24000, limit=23999)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('cap,failing', [(24000.0, False), (23999.0, True)])
def test_planned_and_background_mass_share_one_physical_stack(entry, cap, failing):
    instance, ship, _ = _case([(['0010102'], 12000)], cap=cap)
    _use_supports_as_decision_geometry(instance)
    ship.placements[0].slot = '0010104'
    out = _check(instance, ship, entry)
    assert bool(_overloads(out)) is failing
    if failing:
        _assert_overload(out, mass=24000, limit=23999)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('cap,failing', [(24000.0, False), (23999.0, True)])
def test_incremental_unrelated_candidate_still_sees_existing_planned_background_mass(entry, cap, failing):
    instance, ship, _ = _case([(['0010102'], 12000)], cap=cap)
    _use_supports_as_decision_geometry(instance)
    existing = instance.containers[2]
    existing.length_ft, existing.iso_type = 20, '22G1'
    existing.gross_mass_kg, existing.max_gross_mass_kg = 12000.0, 100000.0
    existing.height_m, existing.pod = 2.591, 'THBKK'
    existing.reefer, existing.dg, existing.oog, existing.special_stowage = False, None, None, []
    ship.placements.insert(0, Placement(slot='0010104', container_id=existing.id))
    out = _check(instance, ship, entry)
    assert bool(_overloads(out)) is failing
    if failing:
        _assert_overload(out, mass=24000, limit=23999)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('length', [40, 45])
@pytest.mark.parametrize('cap,failing', [(18000.0, False), (17999.0, True)])
def test_background_long_box_splits_mass_between_physical_supports(entry, length, cap, failing):
    instance, ship, _ = _case([(['0010102', '0030102'], 36000)], cap=50000)
    instance.vessel.fixed_loads[0].cargo_boxes[0].length_ft = length
    for stack in instance.vessel.fixed_load_stacks:
        stack.max_mass_20_kg = 1000  # Must use the long-box rating for this column.
        stack.max_mass_40_kg = cap
    out = _check(instance, ship, entry)
    assert bool(_overloads(out)) is failing
    if failing:
        assert len(_overloads(out)) == 2
        _assert_overload(out, mass=18000, limit=17999)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('length', [40, 45])
def test_planned_long_box_splits_mass_and_combines_with_each_background_column(entry, length):
    instance, ship, _ = _case([(['0010102'], 12000), (['0030102'], 12000)], cap=29999)
    _use_supports_as_decision_geometry(instance)
    candidate = instance.containers[1]
    candidate.length_ft, candidate.gross_mass_kg = length, 36000.0
    ship.placements[0].slot = '0020104'
    out = _check(instance, ship, entry)
    _assert_overload(out, mass=30000, limit=29999)
    assert len(_overloads(out)) == 2


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_forged_audit_limit_cannot_replace_supplied_physical_support_limit(entry):
    instance, ship, _ = _case([(['0010102'], 12001)], cap=12000)
    info = dict(version=CONDITION_VERSION, background_stacks=[dict(
        stack_id='00101H', mass_kg=12001, max_mass_20_kg=1e9,
        max_cell_mass_kg=1e9, on_deck=False)])
    instance.vessel.sources.append(CONDITION_PREFIX + json.dumps(info))
    _assert_overload(_check(instance, ship, entry), mass=12001, limit=12000)


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_recovered_parent_limit_cannot_be_replaced_by_forged_support_catalog(entry):
    from stowage.vessel.fleet_slices import _parent_record

    instance, ship, parent = _case([(['0010102'], 12001)], cap=1e9)
    parent_stack = next(stack for stack in parent.stacks if stack.id == '00101H')
    parent_stack.max_mass_20_kg = 12000
    source = _parent_record(parent)
    instance.vessel.slice_of = parent.id
    instance.vessel.id = parent.id + '.slice'
    ship.vessel_id = instance.onboard.vessel_id = instance.vessel.id
    instance.vessel.sources = [source, CONDITION_PREFIX + json.dumps({'version': CONDITION_VERSION})]
    _assert_overload(_check(instance, ship, entry), mass=12001, limit=12000)


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_malformed_aggregate_does_not_suppress_actual_background_overload(entry):
    instance, ship, _ = _case([(['0010102'], 12001)], cap=12000)
    instance.vessel.fixed_loads[0].mass_kg = 1  # Retain the independently supplied actual box mass.
    out = _check(instance, ship, entry)
    _assert_overload(out, mass=12001, limit=12000)
    assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard' for v in out)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('mutation', ['missing_cell', 'missing_stack', 'nonreciprocal', 'invalid_limit'])
def test_missing_or_invalid_d42_physical_support_is_hard_data_failure(entry, mutation):
    instance, ship, _ = _case([(['0010102'], 12000)])
    vessel = instance.vessel
    if mutation == 'missing_cell':
        vessel.fixed_load_cells = []
    elif mutation == 'missing_stack':
        vessel.fixed_load_stacks = []
    elif mutation == 'nonreciprocal':
        vessel.fixed_load_stacks[0].cell_ids.remove('0010102')
    else:
        vessel.fixed_load_stacks[0].max_mass_20_kg = float('nan')
    out = _check(instance, ship, entry)
    assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard' for v in out)


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_explicit_non_cargo_mass_is_not_background_stack_cargo(entry):
    instance, ship, _ = _case([(['0010102'], 12000)], cap=12000)
    instance.vessel.fixed_loads.append(FixedLoad(
        x_m=46.9, y_m=-1.3, z_m=3.2955, mass_kg=1e9, kind='non_cargo'))
    assert not _overloads(_check(instance, ship, entry))
