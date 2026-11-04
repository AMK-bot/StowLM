"""D93 deck-share regression; runs against the live tree, never mutates it.

PREPARED ONLY: the feeder_1700 C2 case is expected to fail before manual-c2.patch.
This synthetic recipe guard is not a new checker or operating limit.
"""
import pytest
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_manual import standard_manual_conditions

FLEET = ('feeder_500', 'feeder_800', 'feeder_1700')

@pytest.fixture(scope='module')
def manuals():
    cache = {}
    def get(name):
        if name not in cache:
            vessel = load_vessel(name)
            cache[name] = vessel, {recipe['condition']: recipe for recipe in
                standard_manual_conditions(vessel, load_ship_params(name))}
        return cache[name]
    return get


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
