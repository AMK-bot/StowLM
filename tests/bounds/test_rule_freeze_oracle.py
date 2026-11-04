"""Hand-solved move-engine optima with and without the rule-freeze switch."""

import pytest

from stowage.bounds import lower_bound
from stowage.bounds.bruteforce import brute_force_optimum
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves.engine import apply_plan


def _tiny_data():
    data = build_instance().model_dump()
    # Isolate one physical deck stack, without fixed cargo or physics checks.
    stack = next(s for s in data["vessel"]["stacks"] if s["id"] == "00101D")
    stack.update(hatch_cover_id=None, cell_ids=["0010182", "0010184"])
    data["vessel"].update(stacks=[stack], fixed_loads=[], hatch_covers=[])
    data["vessel"]["cells"] = [c for c in data["vessel"]["cells"] if c["id"] in stack["cell_ids"]]
    data["containers"] = data["containers"][1:3]
    for box in data["containers"]:
        box.update(dg=None, special_stowage=[], direct_delivery=False, pod="THBKK")
    block = data["port"]["yard"]["blocks"][0]
    block.update(n_bays=2, n_rows=1)
    data["port"]["yard"]["blocks"] = [block]
    data["yard"].update(stacks=[], quay_buffer=[])
    data["onboard"]["placements"] = []
    data.update(load_list=[], discharge_list=[])
    return data


@pytest.mark.parametrize("onboard", [False, True])
def test_truck_productive_lift_has_zero_nonproductive_optimum(bounds_rule_freeze, onboard):
    enabled = bounds_rule_freeze
    data = _tiny_data()
    box = data["containers"][0]
    data["containers"] = [box]
    cid = box["id"]
    box.update(direct_delivery=enabled,
               dg=dict(un_number="0004", imdg_class="1.4S", stowage_category="01"))
    data["port"]["port_rules"] = [
        dict(rule_id="port.dg.direct_delivery", params={"classes": ["1"]})]
    if onboard:
        box["pod"] = data["port"]["locode"]
        data["onboard"]["placements"] = [dict(slot="0010182", container_id=cid)]
        data["discharge_list"] = [cid]
    else:
        data["load_list"] = [cid]
        if not enabled:
            data["yard"]["stacks"] = [dict(key="A-001-01", length_ft=20, boxes=[cid])]
    instance = Instance.model_validate(data)
    result = brute_force_optimum(instance, time_limit_s=None)
    assert result.status == "optimal" and result.objective == 0
    assert len(result.plan.moves) == 1
    move = result.plan.moves[0]
    assert move.kind == ("discharge" if onboard else "load")
    assert (move.dst if onboard else move.src) == ("truck" if enabled else "A-001-01-01")
    replay = apply_plan(instance, result.plan)
    assert replay.legal, replay.violations
    assert replay.count.total_lifts_now == 1
    assert replay.count.weighted_nonproductive == result.objective
    assert lower_bound(instance).relocations == lower_bound(instance).weighted == 0


def test_no_shift_destination_forces_two_blocker_lifts(bounds_rule_freeze):
    data = _tiny_data()
    target, blocker = (box["id"] for box in data["containers"])
    data["containers"][0]["pod"] = data["port"]["locode"]
    data["onboard"]["placements"] = [dict(slot="0010182", container_id=target),
                                          dict(slot="0010184", container_id=blocker)]
    data["discharge_list"] = [target]
    instance = Instance.model_validate(data)
    result = brute_force_optimum(instance, time_limit_s=None)
    # Only the blocking top box can move first; no other ship stack exists.
    # Removing and returning it are each a nonproductive lift.
    assert result.status == "optimal" and result.objective == 2
    assert [m.kind for m in result.plan.moves] == ["restow_off", "discharge", "restow_on"]
    replay = apply_plan(instance, result.plan)
    assert replay.legal, replay.violations
    assert replay.count.restow_lifts == 2
    assert replay.count.weighted_nonproductive == result.objective


def test_direct_export_does_not_hide_actual_yard_digout(bounds_rule_freeze):
    data = _tiny_data()
    direct, target = data["containers"]
    blocker = build_instance().model_dump()["containers"][3]
    blocker.update(dg=None, oog=None, special_stowage=[], direct_delivery=False, pod="THBKK")
    data["containers"].append(blocker)
    direct.update(direct_delivery=bounds_rule_freeze,
                  dg=dict(un_number="0004", imdg_class="1.4S", stowage_category="01"))
    data["port"]["port_rules"] = [
        dict(rule_id="port.dg.direct_delivery", params={"classes": ["1"]})]
    data["load_list"] = [direct["id"], target["id"]]
    data["yard"]["stacks"] = [dict(key="A-001-01", length_ft=20, boxes=[target["id"], blocker["id"]])]
    if not bounds_rule_freeze:
        data["yard"]["stacks"].append(dict(key="A-002-01", length_ft=20, boxes=[direct["id"]]))
    instance = Instance.model_validate(data)
    result = brute_force_optimum(instance, time_limit_s=None)
    assert result.status == "optimal" and result.objective == 1
    assert lower_bound(instance).relocations == lower_bound(instance).weighted == 1
    replay = apply_plan(instance, result.plan)
    assert replay.legal, replay.violations
    assert replay.count.relocations == 1
    assert replay.count.weighted_nonproductive == result.objective


def test_invalid_future_departure_can_be_repaired_by_current_shift(bounds_rule_freeze):
    data = _tiny_data()
    full_vessel = build_instance().model_dump()["vessel"]
    extra = next(s for s in full_vessel["stacks"] if s["id"] == "00301D")
    extra.update(hatch_cover_id=None, cell_ids=["0030182"])
    data["vessel"]["stacks"].append(extra)
    data["vessel"]["cells"].extend(c for c in full_vessel["cells"] if c["id"] == "0030182")
    target, support = (box["id"] for box in data["containers"])
    data["containers"][1]["pod"] = "THSGZ"
    data["onboard"]["placements"] = [dict(slot="0010184", container_id=target),
                                          dict(slot="0010182", container_id=support)]
    data["rotation"]["calls"][1]["max_on_deck_tiers"] = 1
    instance = Instance.model_validate(data)
    result = brute_force_optimum(instance, time_limit_s=None)
    # Active future H=1 disallows the initial tier-2 discharge. Shifting the
    # target to the other stack's bottom gives a countable future departure.
    assert result.status == "optimal"
    assert result.objective == int(bounds_rule_freeze)
    assert [m.kind for m in result.plan.moves] == (["restow_shift"] if bounds_rule_freeze else [])
    replay = apply_plan(instance, result.plan)
    assert replay.legal, replay.violations
    assert replay.count.weighted_nonproductive == result.objective
