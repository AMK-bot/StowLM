"""Bounds track the current operational replay boundary, not simulator events."""

import pytest

from stowage.bounds import lower_bound
from stowage.bounds.bruteforce import brute_force_optimum
from stowage.model import GateOut, Instance, Load, MovePlan, Relocate
from stowage.model.fixtures import build_instance
from stowage.moves import apply_move, apply_plan, count_moves, initial_state


def _blocked_load():
    data = build_instance().model_dump()
    boxes = data["containers"][1:3]
    for box in boxes:
        box.update(dg=None, special_stowage=[], pod="THBKK")
    a, b = [box["id"] for box in boxes]
    data.update(containers=boxes, load_list=[a], discharge_list=[])
    data["yard"].update(stacks=[dict(key="A-001-01", length_ft=20, boxes=[a, b])], quay_buffer=[])
    return Instance.model_validate(data), a, b


def test_reported_gate_shortcut_is_simulator_only():
    instance, a, b = _blocked_load()
    instance.objective.relocate = 2
    gate = GateOut(call_index=0, container_id=b, src="A-001-01-02")
    load = Load(call_index=0, container_id=a, src="A-001-01-01", dst="0010182")
    state = apply_move(instance, initial_state(instance), gate)
    assert apply_move(instance, state, load).ship.placements[0].container_id == a
    plan = MovePlan(id="gate-shortcut", instance_id=instance.id, call_index=0, moves=[gate, load])
    assert lower_bound(instance).relocations == 1
    assert lower_bound(instance).weighted == 2
    assert not apply_plan(instance, plan).legal
    with pytest.raises(ValueError, match="illegal plan"):
        count_moves(instance, plan)
    legal_plan = plan.model_copy(update={"moves": [
        Relocate(call_index=0, container_id=b, src="A-001-01-02", dst="A-002-01-01"),
        load,
    ]})
    assert count_moves(instance, legal_plan).weighted_nonproductive == lower_bound(instance).weighted


def test_repeated_current_port_off_list_blocker_requires_counted_restow():
    data = build_instance(layover=True).model_dump()
    target, blocker = [p["container_id"] for p in data["onboard"]["placements"]]
    data["containers"] = [box for box in data["containers"] if box["id"] in (target, blocker)]
    data["load_list"] = []
    data["yard"]["stacks"] = []
    for box in data["containers"]:
        if box["id"] in (target, blocker):
            box["pod"] = "THLCH"
    data["rotation"]["calls"].append(dict(locode="THLCH", index=3, max_on_deck_tiers=2))
    instance = Instance.model_validate(data)
    # Only the listed target may discharge; matching the current LOCODE does
    # not turn a through box for a repeated call into a free productive lift.
    assert lower_bound(instance).restow_lifts == 1
    plan = MovePlan.model_validate(dict(
        id="repeated-call", instance_id=instance.id, call_index=0, moves=[
            dict(kind="hatch_cover_off", call_index=0, cover_id="HC-02-1", dst="quay"),
            dict(kind="restow_shift", call_index=0, container_id=blocker,
                 src="0010104", dst="0010282"),
            dict(kind="discharge", call_index=0, container_id=target,
                 src="0010102", dst="A-001-01-01"),
            dict(kind="hatch_cover_on", call_index=0, cover_id="HC-02-1"),
        ]))
    count = count_moves(instance, plan)
    assert count.restow_lifts == 1
    assert lower_bound(instance).weighted == count.weighted_nonproductive


def test_oracle_can_use_only_a_deterministic_node_cap():
    data = build_instance().model_dump()
    data.update(containers=[], load_list=[], discharge_list=[])
    data["yard"].update(stacks=[], quay_buffer=[])
    result = brute_force_optimum(Instance.model_validate(data), max_nodes=1, time_limit_s=None)
    assert result.status == "optimal"
    assert result.objective == 0


@pytest.mark.parametrize("limit", [-1, 0, float("nan"), float("inf")])
def test_oracle_rejects_invalid_explicit_wall_caps(limit):
    with pytest.raises(ValueError, match="limits"):
        brute_force_optimum(build_instance(), time_limit_s=limit)
