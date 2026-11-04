"""D61/D62 data contracts; move and checker enforcement belongs to later layers."""

import pytest
from pydantic import ValidationError

from stowage.io import load_instance, save_instance
from stowage.model import Discharge, Instance, Load, MovePlan, PortProfile, QuayCraneSpec, YardBlock
from stowage.model.fixtures import build_instance


def _port_payload(height=8):
    return dict(
        locode="THLCH", archetype="synthetic_contract",
        max_crane_height_tiers=height, yard_max_tiers=5,
        equipment=[dict(id="rtg", kind="RTG", stack_tiers=6, count=1)],
        yard=dict(id="synthetic_contract", blocks=[dict(
            id="A", n_bays=2, n_rows=2, max_tiers=4, equipment_id="rtg")]),
        quay_crane=dict(count=1, max_on_deck_tiers=height),
    )


def test_quay_height_preserves_independent_yard_limits_and_synthetic_sides():
    port = PortProfile.model_validate(_port_payload())
    assert port.max_crane_height_tiers == port.quay_crane.max_on_deck_tiers == 8
    assert (port.yard_max_tiers, port.yard.blocks[0].max_tiers, port.equipment[0].stack_tiers) == (5, 4, 6)
    assert port.berthing_side == "port"
    assert port.yard.blocks[0].truck_lane_side == "low"


@pytest.mark.parametrize("height", [0, -1])
def test_crane_height_must_be_at_least_one(height):
    payload = _port_payload()
    payload["max_crane_height_tiers"] = height
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        PortProfile.model_validate(payload)


def test_minimum_crane_height_is_valid():
    payload = _port_payload(1)
    payload["yard_max_tiers"] = 1
    payload["yard"]["blocks"][0]["max_tiers"] = 1
    payload["equipment"][0]["stack_tiers"] = 1
    assert PortProfile.model_validate(payload).max_crane_height_tiers == 1


@pytest.mark.parametrize("field", ["yard_max_tiers", "max_tiers", "stack_tiers"])
def test_yard_limits_can_exceed_quay_height(field):
    payload = _port_payload()
    target = payload if field == "yard_max_tiers" else (
        payload["yard"]["blocks"][0] if field == "max_tiers" else payload["equipment"][0])
    target[field] = 9
    port = PortProfile.model_validate(payload)
    assert port.max_crane_height_tiers == port.quay_crane.max_on_deck_tiers == 8
    actual = port if field == "yard_max_tiers" else (
        port.yard.blocks[0] if field == "max_tiers" else port.equipment[0])
    assert getattr(actual, field) == 9


def test_all_yard_limits_are_independent_of_minimum_quay_height():
    port = PortProfile.model_validate(_port_payload(height=1))
    assert port.max_crane_height_tiers == port.quay_crane.max_on_deck_tiers == 1
    assert (port.yard_max_tiers, port.yard.blocks[0].max_tiers,
            port.equipment[0].stack_tiers) == (5, 4, 6)
    assert PortProfile.model_validate_json(port.model_dump_json()) == port


@pytest.mark.parametrize("quay_height", [7, 9])
def test_current_quay_limit_must_equal_governing_height(quay_height):
    payload = _port_payload()
    payload["quay_crane"]["max_on_deck_tiers"] = quay_height
    with pytest.raises(ValidationError, match="max_on_deck_tiers must equal max_crane_height_tiers"):
        PortProfile.model_validate(payload)


@pytest.mark.parametrize("nested_object", [False, True])
def test_legacy_constructor_retains_missing_unified_height(nested_object):
    payload = _port_payload()
    del payload["max_crane_height_tiers"]
    if nested_object:
        payload["quay_crane"] = QuayCraneSpec(**payload["quay_crane"])
    port = PortProfile(**payload)
    assert port.max_crane_height_tiers is None
    assert port.quay_crane.max_on_deck_tiers == 8
    assert "max_crane_height_tiers" not in payload  # Do not mutate caller-owned input.


@pytest.mark.parametrize("explicit_none", [False, True])
def test_legacy_constructor_preserves_independent_yard_and_quay_limits(explicit_none):
    payload = _port_payload()
    del payload["max_crane_height_tiers"]
    if explicit_none:
        payload["max_crane_height_tiers"] = None
    payload["yard_max_tiers"] = 3
    payload["yard"]["blocks"][0]["max_tiers"] = 3
    payload["equipment"][0]["stack_tiers"] = 3
    payload["quay_crane"]["max_on_deck_tiers"] = 2
    port = PortProfile.model_validate(payload)
    assert port.max_crane_height_tiers is None
    assert port.quay_crane.max_on_deck_tiers == 2
    assert port.yard_max_tiers == port.yard.blocks[0].max_tiers == port.equipment[0].stack_tiers == 3
    assert PortProfile.model_validate_json(port.model_dump_json()) == port


def test_port_sides_survive_json_round_trip():
    payload = _port_payload()
    payload["berthing_side"] = "starboard"
    payload["yard"]["blocks"][0]["truck_lane_side"] = "high"
    port = PortProfile.model_validate(payload)
    restored = PortProfile.model_validate_json(port.model_dump_json())
    assert restored == port
    assert restored.berthing_side == "starboard"
    assert restored.yard.blocks[0].truck_lane_side == "high"


def test_invalid_ship_or_lane_side_rejected():
    payload = _port_payload()
    payload["berthing_side"] = "low"
    with pytest.raises(ValidationError, match="berthing_side"):
        PortProfile.model_validate(payload)
    block = _port_payload()["yard"]["blocks"][0]
    block["truck_lane_side"] = "port"
    with pytest.raises(ValidationError, match="truck_lane_side"):
        YardBlock.model_validate(block)


def _direct_export_payload():
    payload = build_instance().model_dump(mode="json")
    box = payload["containers"][0]
    box["direct_delivery"] = True
    payload["yard"]["stacks"] = [stack for stack in payload["yard"]["stacks"] if box["id"] not in stack["boxes"]]
    return payload, box["id"]


def test_direct_export_needs_no_yard_position_and_round_trips(tmp_path):
    payload, cid = _direct_export_payload()
    instance = Instance.model_validate(payload)
    assert cid in instance.load_list
    assert next(box for box in instance.containers if box.id == cid).direct_delivery
    restored = load_instance(save_instance(instance, tmp_path / "direct.json"))
    assert restored == instance
    assert restored.port.max_crane_height_tiers == instance.port.max_crane_height_tiers
    assert restored.port.quay_crane.max_on_deck_tiers == instance.port.quay_crane.max_on_deck_tiers


@pytest.mark.parametrize("location", ["stack", "quay_buffer"])
def test_direct_delivery_cannot_appear_anywhere_in_yard_snapshot(location):
    payload, cid = _direct_export_payload()
    if location == "stack":
        payload["yard"]["stacks"].append(dict(key="A-001-01", length_ft=40, boxes=[cid]))
    else:
        payload["yard"]["quay_buffer"] = [cid]
    with pytest.raises(ValidationError, match="direct-delivery container must not appear in the yard snapshot"):
        Instance.model_validate(payload)


def test_unmarked_export_still_requires_yard_source():
    payload, cid = _direct_export_payload()
    next(box for box in payload["containers"] if box["id"] == cid)["direct_delivery"] = False
    with pytest.raises(ValidationError, match="every load_list container must be in the yard"):
        Instance.model_validate(payload)


def test_direct_import_can_be_onboard_for_discharge():
    payload = build_instance(layover=True).model_dump(mode="json")
    cid = payload["discharge_list"][0]
    next(box for box in payload["containers"] if box["id"] == cid)["direct_delivery"] = True
    instance = Instance.model_validate(payload)
    assert cid in {p.container_id for p in instance.onboard.placements}


def test_direct_export_cannot_already_be_onboard():
    payload, cid = _direct_export_payload()
    payload["onboard"]["placements"] = [dict(container_id=cid, slot="0020182")]
    with pytest.raises(ValidationError, match="direct-delivery load_list container must not already be onboard"):
        Instance.model_validate(payload)


def test_direct_delivery_defaults_false_for_ordinary_boxes():
    assert all(not box.direct_delivery for box in build_instance().containers)


def test_direct_delivery_move_endpoints_round_trip_in_plan():
    instance = build_instance(layover=True)
    load_fields = dict(call_index=0, container_id=instance.load_list[0], src="truck", dst="0020182")
    discharge_fields = dict(call_index=0, container_id=instance.discharge_list[0], src="0010102", dst="truck")
    plan = MovePlan(id="direct-transfer", instance_id=instance.id, call_index=0,
                    moves=[Load(**load_fields), Discharge(**discharge_fields)])
    restored = MovePlan.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert restored.moves[0].src == "truck"
    assert restored.moves[1].dst == "truck"


def test_yard_move_endpoints_remain_available_and_validated():
    instance = build_instance(layover=True)
    load = Load(call_index=0, container_id=instance.load_list[0], src="A-001-01-01", dst="0020182")
    discharge = Discharge(call_index=0, container_id=instance.discharge_list[0], src="0010102", dst="A-006-01-01")
    assert load.src == "A-001-01-01"
    assert discharge.dst == "A-006-01-01"
    for invalid in (None, "gate", "A-001-01"):
        with pytest.raises(ValidationError, match="src"):
            Load(call_index=0, container_id=load.container_id, src=invalid, dst=load.dst)
        with pytest.raises(ValidationError, match="dst"):
            Discharge(call_index=0, container_id=discharge.container_id, src=discharge.src, dst=invalid)
    with pytest.raises(ValidationError, match="src"):
        Load(call_index=0, container_id=load.container_id, dst=load.dst)
    with pytest.raises(ValidationError, match="dst"):
        Discharge(call_index=0, container_id=discharge.container_id, src=discharge.src)
