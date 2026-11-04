"""Cross-model references and stage-D contracts beyond the two fixture snapshots."""

import pytest
from pydantic import ValidationError

from stowage.model import Instance, MovePlan, Load
from stowage.model.fixtures import build_instance


def test_set_catalogs_normalize_but_yard_box_order_is_preserved():
    payload = build_instance().model_dump(mode="json")
    payload["containers"].reverse()
    payload["load_list"].reverse()
    payload["yard"]["stacks"].reverse()
    assert Instance.model_validate(payload) == build_instance()


def test_pod_may_resolve_at_repeated_later_call():
    payload = build_instance().model_dump(mode="json")
    payload["rotation"]["calls"].append(dict(locode="THLCH", index=3))
    payload["containers"][0]["pod"] = "THLCH"
    assert Instance.model_validate(payload).containers[0].pod == "THLCH"


def test_stage_d_forecast_uses_its_own_load_call():
    payload = build_instance().model_dump(mode="json")
    box = payload["containers"][1].copy()
    box["pol"], box["pod"] = "THBKK", "THSGZ"
    payload.update(stage="D", plan_calls=[1], forecasts=[dict(
        call_index=1, scenarios=[dict(probability=1.0, containers=[box])])])
    assert Instance.model_validate(payload).forecasts[0].call_index == 1
    box["pod"] = "THBKK"
    with pytest.raises(ValidationError, match="unresolved forecast POD"):
        Instance.model_validate(payload)


@pytest.mark.parametrize("field,value,match", [
    ("plan_calls", [99], "plan_calls"),
    ("load_list", ["QZXU9999990"], "check digit|unknown container"),
])
def test_invalid_call_or_container_reference(field, value, match):
    payload = build_instance().model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError, match=match):
        Instance.model_validate(payload)


def test_plan_moves_use_same_call():
    instance = build_instance()
    with pytest.raises(ValidationError, match="call_index"):
        MovePlan(id="bad", instance_id=instance.id, call_index=0, moves=[
            Load(call_index=1, container_id=instance.load_list[0], src="A-001-01-01", dst="0020182")])
