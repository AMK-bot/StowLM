"""Mixed-length column ratings from convergence review item 7.

The synthetic 40/45-foot box shares its mass between two 20-foot columns.
ASSUMPTION: a column containing 20-foot boxes uses its 20-foot rating, pending
expert confirmation; the two supports retain their own independent limits.
"""

import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.model import Placement
from tests.checker.test_rules_fixtures import build_instance


def _mixed_stow(length):
    instance = build_instance()
    instance.containers = [
        box.model_copy(update={
            "iso_type": "22G1" if index < 2 else "42G1" if length == 40 else "L5G1",
            "length_ft": 20 if index < 2 else length,
            "height_m": 2.896 if index == 2 and length == 45 else 2.591,
            "tare_kg": 2200, "gross_mass_kg": 20000,
            "reefer": False, "reefer_setpoint_c": None, "dg": None,
            "special_stowage": [], "oog": None, "pod": "THBKK",
        })
        for index, box in enumerate(instance.containers[:3])
    ]
    instance.rule_overrides = {}
    for stack in instance.vessel.stacks:
        # Both the generic limit and half the 40-foot rating are too low for
        # this mixed column; neither should reduce its documented 20-foot rating.
        stack.max_mass_kg = 25000
        stack.max_mass_20_kg = 30000
        stack.max_mass_40_kg = 40000
    partial = instance.onboard.model_copy(update={"placements": [
        Placement(slot=slot, container_id=box.id)
        for box, slot in zip(instance.containers, ("0010102", "0030102"))
    ]})
    complete = partial.model_copy(update={"placements": [
        *partial.placements,
        Placement(slot="0020104", container_id=instance.containers[2].id),
    ]})
    return instance, partial, complete


@pytest.mark.parametrize("length", [40, 45])
@pytest.mark.parametrize("failing_support", [None, "00101H", "00301H"],
                         ids=["exact_rating_passes", "first_support_fails", "second_support_fails"])
def test_mixed_columns_use_each_twenty_foot_rating(length, failing_support):
    instance, partial, complete = _mixed_stow(length)
    if failing_support is not None:
        next(s for s in instance.vessel.stacks if s.id == failing_support).max_mass_20_kg = 29999
    report = check_static_rules(instance, complete, call_index=0)
    full = [v for v in report.violations if v.rule_id == "expert.physical.stack_weight"]
    incremental = [v for v in placement_violations(
        instance, partial, instance.containers[2], "0020104", call_index=0,
    ) if v.rule_id == "expert.physical.stack_weight"]
    assert bool(full) is (failing_support is not None)
    assert incremental == full
    assert report.margins["stack_mass_kg"] == (0 if failing_support is None else -1)
    if failing_support is None:
        assert not any(v.severity == "hard" for v in report.violations)
    else:
        assert len(full) == 1
        assert full[0].measured == 30000
        assert full[0].limit == 29999
        expected_bottom = "0010102" if failing_support == "00101H" else "0030102"
        assert set(full[0].slots) == {expected_bottom, "0020104"}


@pytest.mark.parametrize("length", [40, 45])
@pytest.mark.parametrize("failing", [False, True], ids=["generic_passes", "generic_fails"])
def test_mixed_columns_without_twenty_rating_use_generic_limit(length, failing):
    instance, partial, complete = _mixed_stow(length)
    for stack in instance.vessel.stacks:
        stack.max_mass_20_kg = None
        stack.max_mass_kg = 29999 if failing else 30000
        stack.max_mass_40_kg = 20000
    report = check_static_rules(instance, complete, call_index=0)
    full = [v for v in report.violations if v.rule_id == "expert.physical.stack_weight"]
    incremental = [v for v in placement_violations(
        instance, partial, instance.containers[2], "0020104", call_index=0,
    ) if v.rule_id == "expert.physical.stack_weight"]
    assert bool(full) is failing
    assert incremental == full
    assert report.margins["stack_mass_kg"] == (-1 if failing else 0)
    assert all(v.limit == 29999 and v.measured == 30000 for v in full)
