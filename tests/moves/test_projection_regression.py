"""D15's future scenario is a count, not an executable restow sequence."""
from __future__ import annotations

import pytest

from stowage.model import Instance, MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import Discharge, HatchCoverOff, HatchCoverOn, RestowOff, RestowOn
from stowage.moves import IllegalMoveError, apply_move, apply_plan, initial_state, project_future_moves


def _two_future_calls():
    instance = build_instance(layover=True)
    lower, upper = instance.containers[-2:]
    lower.pod, upper.pod = "THBKK", "THSGZ"
    instance.containers = [lower, upper]
    instance.yard.stacks = []
    instance.load_list = []
    instance.discharge_list = []
    return Instance.model_validate(instance.model_dump()), lower.id, upper.id


def test_fixed_slot_projection_does_not_certify_original_slot_return():
    instance, lower, upper = _two_future_calls()
    before = instance.model_dump_json()
    future = project_future_moves(instance, instance.onboard, from_call=0)
    assert [(f.call_index, f.restowed_boxes, f.restow_lifts, f.hatch_cover_lifts) for f in future] == [
        (1, 1, 2, 2), (2, 0, 0, 2),
    ]
    assert instance.model_dump_json() == before

    # At the next call, discharging the tier-02 support makes the stated tier-04
    # return impossible. This is why the projection cannot supply a future plan.
    arrival = instance.model_copy(deep=True)
    arrival.call_index = 1
    arrival.port.locode = "THBKK"
    arrival.discharge_list = [lower]
    arrival = Instance.model_validate(arrival.model_dump())
    moves = [
        HatchCoverOff(call_index=1, cover_id="HC-02-1"),
        RestowOff(call_index=1, container_id=upper, src="0010104"),
        Discharge(call_index=1, container_id=lower, src="0010102", dst="B-002-01-01"),
    ]
    state = initial_state(arrival)
    for move in moves:
        state = apply_move(arrival, state, move)
    removed = state.model_dump_json()
    with pytest.raises(IllegalMoveError) as caught:
        apply_move(arrival, state, RestowOn(call_index=1, container_id=upper, dst="0010104"))
    assert caught.value.violation.rule_id == "expert.moves.bottom_up"
    assert state.model_dump_json() == removed

    # An explicit executable policy must pick a supported destination instead.
    replay = apply_plan(arrival, MovePlan(
        id="projection.supported_return", instance_id=arrival.id, call_index=1,
        moves=[*moves, RestowOn(call_index=1, container_id=upper, dst="0010102"),
               HatchCoverOn(call_index=1, cover_id="HC-02-1")],
    ))
    assert replay.legal, replay.violations
    assert (replay.count.discharges, replay.count.restow_lifts, replay.count.hatch_cover_lifts) == (1, 2, 2)
    assert [(p.slot, p.container_id) for p in replay.final_state.ship.placements] == [("0010102", upper)]
    assert replay.count.future == [future[1]]


@pytest.mark.parametrize("lifts_per_box", [1, 2, 4])
def test_fixed_slot_projection_retains_d15_configurable_count(lifts_per_box):
    instance, _, _ = _two_future_calls()
    instance.objective.future_restow_lifts_per_box = lifts_per_box
    future = project_future_moves(instance, instance.onboard, from_call=0)
    assert [(f.restowed_boxes, f.restow_lifts, f.hatch_cover_lifts) for f in future] == [
        (1, lifts_per_box, 2), (0, 0, 2),
    ]
