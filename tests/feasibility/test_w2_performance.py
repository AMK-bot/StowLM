"""Fixture integrity for the bounded executable timing probe; no timing assertions."""
import pytest

from stowage.conventions import cells_of_slot
from stowage.model import Instance
from stowage.rules.catalogue import rule_freeze_v1
from tests.feasibility.w2_performance_probe import make_performance_instance


@pytest.mark.parametrize('rung', ['medium','full'])
def test_real_medium_full_cost_prefix_integrity(rung):
    with rule_freeze_v1(enabled=True):
        instance = make_performance_instance(rung)
        Instance.model_validate(instance.model_dump())
    assert instance.vessel.hull and instance.vessel.bridge
    assert bool(instance.vessel.fixed_loads) is (rung == 'medium')
    assert {box.length_ft for box in instance.containers} == {20,40}
    assert len({box.gross_mass_kg for box in instance.containers}) > 2
    assert .3 <= len(instance.onboard.placements)/len(instance.containers) <= .6
    assert all(len(stack.boxes) == 1 and stack.key.endswith('-01') for stack in instance.yard.stacks)
    occupied = {cell for placement in instance.onboard.placements for cell in cells_of_slot(placement.slot)}
    for stack in instance.vessel.stacks:
        pattern = [cell in occupied for cell in stack.cell_ids]
        assert pattern == sorted(pattern, reverse=True)
