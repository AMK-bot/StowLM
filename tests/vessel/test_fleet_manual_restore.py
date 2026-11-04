"""D27 manual recipes must actually pass at both D50 voyage endpoints.

Use production recipes, models, voyage declaration and the complete checker.
No experiment harness imports, checker spies, mocked physics or solver calls.
APPROXIMATION: the declared synthetic designer conditions use dry ISO20 boxes,
synthetic CSC plates, 97% consumables and the existing expert-rule policy.
"""
from math import floor

import pytest

from stowage.checker.full import check
from stowage.conventions import CONTAINER_HEIGHT_STD_M, make_container_id
from stowage.generate.instances_voyage import attach_default_voyage
from stowage.model import Instance, MovePlan, derive_arrival_liquid_state
from stowage.model.fixtures import build_instance
from stowage.model.plan import BallastCondition
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_manual import STANDARD_ROUTE, standard_manual_conditions


FLEET = ('feeder_500', 'feeder_800', 'feeder_1700')
# These are the original D27 designer policy values, not acceptance overrides.
# The heavy-under-deck threshold matches the production recipe's fill rule.
DESIGNER_POLICY = {
    'expert.e6.heavy_under_deck': {'heavy_threshold': 25000.},
    'expert.e1.heavy_bottom': {'heavy_threshold': 25000.},
    'expert.e2.weight_order': {'tolerance': 0.},
}


@pytest.fixture(scope='module')
def manuals():
    result = {}

    def get(name):
        if name not in result:
            vessel = load_vessel(name)
            recipes = standard_manual_conditions(vessel, load_ship_params(name))
            assert [recipe['condition'] for recipe in recipes] == ['C1', 'C2', 'C3']
            result[name] = vessel, {recipe['condition']: recipe for recipe in recipes}
        return result[name]

    return get


def _physical_recipe(vessel, recipe):
    cells = {cell.id: cell for cell in vessel.cells}
    occupied = dict(recipe['cargo_cells'])
    assert len(occupied) == len(recipe['cargo_cells'])
    assert len(occupied) == recipe['boxes'] > 0
    assert all(cid in cells and cells[cid].accepts_20 for cid in occupied)
    assert all(2200. <= mass <= 30480. for mass in occupied.values())
    assert sum(occupied.values()) == pytest.approx(recipe['cargo_mass_kg'], abs=1e-6)
    assert recipe['cargo_mass_kg'] <= recipe['cargo_budget_kg'] + 1e-6
    for stack in vessel.stacks:
        used = [cid for cid in stack.cell_ids if cid in occupied]
        assert used == stack.cell_ids[:len(used)], stack.id
        limit = stack.max_mass_20_kg if stack.max_mass_20_kg is not None else stack.max_mass_kg
        assert sum(occupied[cid] for cid in used) <= limit + 1e-6, stack.id
        assert len(used) * CONTAINER_HEIGHT_STD_M <= stack.max_height_m + 1e-9, stack.id
        assert all(occupied[below] >= occupied[above]
                   for below, above in zip(used, used[1:])), stack.id
        if stack.on_deck:
            assert all(occupied[cid] < 25000. for cid in used)
    for tank in vessel.tanks:
        mass = recipe['departure_tank_masses_kg'][tank.id]
        capacity = tank.capacity_m3 * tank.content_density_kg_m3
        assert 0. <= mass <= capacity + 1e-6
        if not tank.adjustable:
            assert mass == pytest.approx(.97 * capacity, rel=1e-12)


def _manual_instance(vessel, recipe):
    """Rebuild historical fixture context using only current production models."""
    payload = build_instance().model_dump(mode='python')
    containers, placements = [], []
    for serial, (cid, mass) in enumerate(recipe['cargo_cells'], 1):
        identifier = make_container_id('QZXU', serial)
        containers.append(dict(id=identifier, iso_type='22G1', length_ft=20,
            height_m=CONTAINER_HEIGHT_STD_M, tare_kg=2200., gross_mass_kg=mass,
            max_gross_mass_kg=30480., pol='THLCH', pod='THBKK'))
        placements.append(dict(slot=cid, container_id=identifier))
    payload.update(id=f"D27_restore_{vessel.id}_{recipe['condition']}",
        vessel=vessel, containers=containers, load_list=[], discharge_list=[],
        onboard=dict(vessel_id=vessel.id, placements=placements,
                     tank_masses_kg=dict(recipe['departure_tank_masses_kg'])),
        rule_overrides={**payload.get('rule_overrides', {}), **DESIGNER_POLICY})
    payload['yard']['stacks'] = []
    payload['port']['max_draught_m'] = vessel.hull.max_draught_m
    payload['port']['quay_crane']['max_on_deck_tiers'] = max(
        len(stack.cell_ids) for stack in vessel.stacks if stack.on_deck)
    payload['port']['quay_crane']['max_lift_height_m'] = 60.
    for call in payload['rotation']['calls']:
        call['max_on_deck_tiers'] = None
    instance = attach_default_voyage(Instance.model_validate(payload))
    assert tuple(call.locode for call in instance.rotation.calls) == recipe['route'] == STANDARD_ROUTE
    assert instance.voyage.consumption is None
    assert not instance.voyage.ballast_operations
    return instance


def test_feeder_500_c2_homogeneous_18t_count_preserves_budget_and_supports(manuals):
    vessel, recipes = manuals('feeder_500')
    recipe = recipes['C2']
    _physical_recipe(vessel, recipe)
    assert recipe['uniform_mass_kg'] == 18000.
    # The reviewed HEEL reserve changes this current budget; preserve the
    # homogeneous recipe and physical limits rather than the old box count.
    assert recipe['boxes'] == floor(recipe['cargo_budget_kg'] / 18000.) == 249
    assert {mass for _, mass in recipe['cargo_cells']} == {18000.}
    assert recipe['cargo_mass_kg'] == 4482000.
    assert 0. <= recipe['cargo_budget_kg'] - recipe['cargo_mass_kg'] < 18000.


@pytest.mark.parametrize('name,condition',
    [(name, condition) for name in FLEET for condition in ('C1', 'C2', 'C3')],
    ids=[f'{name}-{condition}' for name in FLEET for condition in ('C1', 'C2', 'C3')])
def test_actual_standard_manual_condition_passes_complete_voyage_checker(manuals, name, condition):
    vessel, recipes = manuals(name)
    recipe = recipes[condition]
    _physical_recipe(vessel, recipe)
    instance = _manual_instance(vessel, recipe)
    arrival = derive_arrival_liquid_state(instance.onboard, instance.voyage, vessel=vessel)
    tank_by_id = {tank.id: tank for tank in vessel.tanks}
    for tank in vessel.tanks:
        if tank.adjustable:
            assert arrival.tank_masses_kg.get(tank.id, 0.) == instance.onboard.tank_masses_kg[tank.id]
    for group in vessel.voyage_policy.groups:
        before = sum(instance.onboard.tank_masses_kg[tid] for tid in group.tank_ids)
        after = sum(arrival.tank_masses_kg.get(tid, 0.) for tid in group.tank_ids)
        assert after == pytest.approx(.1 * before, abs=1e-6)
        assert all(not tank_by_id[tid].adjustable for tid in group.tank_ids)
    plan = MovePlan(id=f'{instance.id}.manual', instance_id=instance.id,
        call_index=instance.call_index, ballast=BallastCondition(source='planner',
            tank_masses_kg={tank.id: recipe['departure_tank_masses_kg'][tank.id]
                            for tank in vessel.tanks if tank.adjustable}))
    report = check(instance, plan)
    diagnostics = [(violation.rule_id, violation.measured, violation.limit)
                   for violation in report.violations]
    # Check both labelled endpoints explicitly; a departure-only implementation
    # must fail even if its unlabelled aggregate report happens to pass.
    for identity in (instance.voyage.departure, instance.voyage.arrival):
        for part_name in ('stability', 'strength'):
            key = f'condition:{identity.id}:{part_name}'
            assert key in report.parts, (key, diagnostics)
            part = report.parts[key]
            assert part.ran, (key, diagnostics)
            assert not [violation for violation in part.violations if violation.severity == 'hard'], (key, diagnostics)
    assert report.passed, diagnostics


@pytest.mark.parametrize('name', FLEET)
def test_c2_manual_condition_keeps_at_least_half_cargo_mass_below_deck(manuals, name):
    """D93 designer-recipe regression, not an extra checker/operating rule."""
    condition = 'C2'
    vessel, recipes = manuals(name)
    recipe = recipes[condition]
    cells = {cell.id: cell for cell in vessel.cells}
    stacks = {stack.id: stack for stack in vessel.stacks}
    total = sum(mass for _, mass in recipe['cargo_cells'])
    on_deck = sum(mass for cid, mass in recipe['cargo_cells']
                  if stacks[cells[cid].stack_id].on_deck)
    assert total > 0.
    assert on_deck <= .5 * total, (name, condition, on_deck, total)
