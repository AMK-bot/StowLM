"""Public API regressions for review findings 2–4 (synthetic fixed cargo)."""
import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.model import Instance, Placement
from stowage.model.vessel import FixedCargoBox, FixedLoad
from stowage.rules.catalogue import rule_freeze_v1
from tests.checker.test_rules_fixtures import build_instance

E1 = 'expert.e1.heavy_bottom'
E2 = 'expert.e2.weight_order'


def _fixed_case(specs, *, decision=None, stage='A'):
    """Specs: (physical cell ids, mass); decision: (slot, mass, length)."""
    instance = build_instance()
    instance.stage = stage
    instance.rule_overrides = {}
    vessel = instance.vessel
    vessel.id, vessel.slice_of, vessel.sources = 'synthetic_w2_fixed_weight', None, []
    cells = {c.id: c for c in vessel.cells}
    fixed_ids = {cid for ids, _ in specs for cid in ids}
    vessel.fixed_load_cells = [c.model_copy(deep=True) for c in vessel.cells if c.id in fixed_ids]
    vessel.fixed_load_stacks = [s.model_copy(update={'cell_ids': [cid for cid in s.cell_ids if cid in fixed_ids]})
                                for s in vessel.stacks if any(cid in fixed_ids for cid in s.cell_ids)]
    vessel.cells = [c for c in vessel.cells if c.id not in fixed_ids]
    vessel.stacks = [s.model_copy(update={'cell_ids': [cid for cid in s.cell_ids if cid not in fixed_ids]})
                     for s in vessel.stacks if any(cid not in fixed_ids for cid in s.cell_ids)]
    vessel.fixed_loads = []
    for ids, mass in specs:
        supports = [cells[cid] for cid in ids]
        box = FixedCargoBox(cell_ids=ids, length_ft=20 if len(ids) == 1 else 40,
            height_m=2.591, gross_mass_kg=mass, x_m=sum(c.lcg_m for c in supports) / len(supports),
            y_m=supports[0].tcg_m, z_m=supports[0].vcg_base_m + 2.591 / 2,
            stack_id=supports[0].stack_id, tier=int(ids[0][-2:]))
        vessel.fixed_loads.append(FixedLoad(x_m=box.x_m, y_m=box.y_m, z_m=box.z_m,
            mass_kg=mass, kind='cargo', cargo_boxes=[box]))
    instance.onboard = instance.onboard.model_copy(update={'vessel_id': vessel.id, 'placements': []})
    ship = instance.onboard.model_copy(deep=True)
    if decision:
        slot, mass, length = decision
        box = instance.containers[0]
        box.length_ft, box.iso_type = length, '22G1' if length == 20 else '42G1'
        box.height_m, box.tare_kg, box.gross_mass_kg = 2.591, 2200, mass
        box.reefer, box.reefer_setpoint_c = False, None
        box.dg, box.oog, box.special_stowage, box.pod = None, None, [], 'THBKK'
        ship.placements = [Placement(container_id=box.id, slot=slot)]
    return instance, ship


def _out(instance, ship, incremental):
    if incremental:
        candidate = ship.placements[-1]
        return placement_violations(instance, ship.model_copy(update={'placements': []}),
            instance.containers[0], candidate.slot, call_index=0)
    return check_static_rules(instance, ship, call_index=0).violations


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('stage', ['A', 'B'])
def test_review2_fixed_only_weight_order_in_every_column(enabled, stage):
    instance, ship = _fixed_case([(['0010102'], 12000), (['0010104'], 13000)], stage=stage)
    with rule_freeze_v1(enabled=enabled):
        breaches = [v for v in _out(instance, ship, False) if v.rule_id in {E1, E2}]
    assert {v.rule_id for v in breaches} == ({E1, E2} if enabled else set())
    assert all(v.severity == ('tolerated' if stage == 'B' else 'hard') for v in breaches)
    assert all(v.inherited is (stage == 'B') for v in breaches)


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('incremental', [False, True])
def test_review2_new_heavier_box_above_lighter_fixed_is_hard(enabled, incremental):
    instance, ship = _fixed_case([(['0010102'], 12000)], decision=('0010104', 13000, 20), stage='B')
    with rule_freeze_v1(enabled=enabled):
        breaches = [v for v in _out(instance, ship, incremental) if v.rule_id in {E1, E2}]
    assert {v.rule_id for v in breaches} == ({E1, E2} if enabled else set())
    assert all(v.severity == 'hard' and not v.inherited for v in breaches)


@pytest.mark.parametrize('lighter', ['0010102', '0030102'])
@pytest.mark.parametrize('incremental', [False, True])
def test_review2_long_box_compares_with_both_fixed_supports(lighter, incremental):
    instance, ship = _fixed_case([([cid], 12000 if cid == lighter else 16000)
                                 for cid in ('0010102', '0030102')], decision=('0020104', 14000, 40))
    with rule_freeze_v1(enabled=True):
        breaches = [v for v in _out(instance, ship, incremental) if v.rule_id in {E1, E2}]
    assert {v.rule_id for v in breaches} == {E1, E2}
    assert all(lighter in v.slots and v.measured == 14000 and v.limit == 12000 for v in breaches)


@pytest.mark.parametrize('stage', ['A', 'B'])
def test_review2_new_lower_box_cannot_hide_behind_inherited_fixed_witness(stage):
    # Existing lower fixed cargo and upper fixed cargo breach; a new lower box
    # in the other physical column must also produce a hard upper-box witness.
    instance, ship = _fixed_case([(['0010102'], 12000), (['0010104', '0030104'], 15000)],
                                 decision=('0030102', 14000, 20), stage=stage)
    with rule_freeze_v1(enabled=True):
        breaches = [v for v in _out(instance, ship, False) if v.rule_id in {E1, E2}]
    assert {v.rule_id for v in breaches if '0030102' in v.slots and v.severity == 'hard'} == {E1, E2}


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('length', [20, 40])
def test_review3_fixed_heavy_on_deck_inclusive_boundary_counted_once(enabled, length):
    ids = ['0010182'] if length == 20 else ['0010182', '0030182']
    instance, ship = _fixed_case([(ids, 20000), (['0010102'], 25000), (['0010282'], 19999)])
    with rule_freeze_v1(enabled=enabled):
        report = check_static_rules(instance, ship, call_index=0)
    assert report.metrics.get('heavy_on_deck_count') == (1.0 if enabled else None)


@pytest.mark.parametrize('override', [
    {E1: {'weight_order_tolerance_kg': 500}},
    {E1: {'weight_order_tolerance_kg': 500}, E2: {'weight_order_tolerance_kg': 0}},
])
def test_review4_e1_only_or_conflicting_tolerance_fails_closed(override):
    instance, ship = _fixed_case([], decision=('0010104', 12500, 20))
    lower = instance.containers[1]
    lower.length_ft, lower.iso_type, lower.gross_mass_kg = 20, '22G1', 12000
    lower.reefer, lower.dg, lower.oog, lower.special_stowage = False, None, None, []
    ship.placements.insert(0, Placement(container_id=lower.id, slot='0010102'))
    instance.rule_overrides = override
    with rule_freeze_v1(enabled=True):
        assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard'
                   and 'tolerance' in v.message for v in _out(instance, ship, False))


def test_review4_matching_tolerance_duplicates_accepted():
    instance, ship = _fixed_case([(['0010102'], 12000)], decision=('0010104', 12500, 20))
    instance.rule_overrides = {E1: {'weight_order_tolerance_kg': 500},
                               E2: {'weight_order_tolerance_kg': {'value': 500}}}
    with rule_freeze_v1(enabled=True):
        assert not any(v.rule_id in {E1, E2} or
                       (v.rule_id == 'checker.missing_vessel_data' and 'tolerance' in v.message)
                       for v in _out(instance, ship, False))


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('incremental', [False, True])
@pytest.mark.parametrize('stage', ['A', 'B'])
def test_review_fixed_twenty_cannot_rest_above_fixed_long(enabled, incremental, stage):
    instance = build_instance()
    instance.stage = stage
    vessel = instance.vessel
    vessel.fixed_load_cells = [c for c in vessel.cells if c.stack_id[3:5] == '02']
    vessel.fixed_load_stacks = [s for s in vessel.stacks if s.row == 2]
    vessel.fixed_load_hatch_covers = [h for h in vessel.hatch_covers if h.id == 'HC-02-2']
    vessel.cells = [c for c in vessel.cells if c not in vessel.fixed_load_cells]
    vessel.stacks = [s for s in vessel.stacks if s not in vessel.fixed_load_stacks]
    vessel.hatch_covers = [h for h in vessel.hatch_covers if h not in vessel.fixed_load_hatch_covers]
    vessel.nominal_teu = len(vessel.cells)
    supports = [c for c in vessel.fixed_load_cells if c.id in {'0010282', '0030282'}]
    lower = FixedCargoBox(cell_ids=['0010282', '0030282'], length_ft=40,
        height_m=2.591, gross_mass_kg=12000,
        x_m=sum(c.lcg_m for c in supports) / len(supports), y_m=supports[0].tcg_m,
        z_m=supports[0].vcg_base_m + 2.591 / 2, stack_id=supports[0].stack_id, tier=82)
    upper = lower.model_copy(update={'cell_ids': ['0010284'], 'length_ft': 20,
        'tier': 84, 'x_m': lower.x_m - 3.05, 'z_m': lower.z_m + lower.height_m})
    vessel.fixed_loads = [FixedLoad(x_m=b.x_m, y_m=b.y_m, z_m=b.z_m,
        mass_kg=b.gross_mass_kg, kind='cargo', cargo_boxes=[b]) for b in (lower, upper)]
    # This is a validated public model input, not malformed mutable evidence.
    instance = Instance.model_validate(instance.model_dump())
    candidate = instance.containers[0]
    ship = instance.onboard.model_copy(update={'placements': [
        Placement(container_id=candidate.id, slot='0020182')]})
    with rule_freeze_v1(enabled=enabled):
        found = [v for v in _out(instance, ship, incremental)
                 if v.rule_id == 'expert.physical.no_20_on_40']
    assert bool(found) is enabled
    assert all(v.severity == 'hard' and v.containers == []
               and v.slots == ['0010284', '0020282'] for v in found)
