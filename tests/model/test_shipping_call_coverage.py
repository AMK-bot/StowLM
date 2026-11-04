"""Required future calls cannot inherit the current call's shipping evidence."""

import pytest

from stowage.model import CheckReport, Instance, MoveCount, MovePlan, PartReport, PlannerResult


@pytest.mark.parametrize("required_calls", [[], [1], [1, 2]])
def test_shipping_evidence_requires_no_unchecked_calls(empty_ship_instance, required_calls):
    payload = empty_ship_instance.model_dump()
    payload.update(stage="D" if required_calls else "A", plan_calls=required_calls)
    instance = Instance.model_validate(payload)
    result = PlannerResult(
        status="ok",
        planner="test",
        planner_version="1",
        plan=MovePlan(id="current-call", instance_id=instance.id, call_index=instance.call_index),
    )
    # Synthetic internal producer: isolate the property's guard from run_planner,
    # which already rejects omitted required calls before attaching evidence.
    report = CheckReport(
        passed=True,
        count=MoveCount(),
        parts={name: PartReport(name=name) for name in (
            "moves", "rules", "stability", "strength", "visibility", "lashing", "constraints"
        )},
    )
    result._record_full_check(instance, report)

    assert not result.later_plans
    assert result.shippable is (not required_calls)
