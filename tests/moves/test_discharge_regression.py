"""Productive discharge belongs to the current call's explicit work list."""
from __future__ import annotations

import pytest

from stowage.checker.full import check
from stowage.model import Instance, MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import Discharge, HatchCoverOff, HatchCoverOn, RestowOff, RestowOn
from stowage.moves import IllegalMoveError, apply_move, apply_plan, initial_state


def repeated_call_problem():
    instance = build_instance(layover=True)
    discharge, through = instance.containers[-2:]
    instance.rotation.calls.append(instance.rotation.calls[0].model_copy(update={
        "index": 3, "eta_s": 3 * 86400,
    }))
    through.pod = instance.port.locode
    instance.load_list = []
    instance = Instance.model_validate(instance.model_dump())
    moves = [
        HatchCoverOff(call_index=0, cover_id="HC-02-1"),
        Discharge(call_index=0, container_id=through.id, src="0010104", dst="B-002-01-01"),
        Discharge(call_index=0, container_id=discharge.id, src="0010102", dst="B-002-02-01"),
        HatchCoverOn(call_index=0, cover_id="HC-02-1"),
    ]
    return instance, MovePlan(id="repeated-call", instance_id=instance.id, call_index=0, moves=moves)


def test_single_discharge_rejects_through_box_at_repeated_current_port():
    instance, plan = repeated_call_problem()
    state = apply_move(instance, initial_state(instance), plan.moves[0])
    before = state.model_dump_json()
    with pytest.raises(IllegalMoveError) as caught:
        apply_move(instance, state, plan.moves[1], move_index=1)
    assert caught.value.violation.rule_id == "expert.moves.top_access"
    assert caught.value.violation.move_index == 1
    assert "discharge list" in caught.value.violation.message
    assert state.model_dump_json() == before


def test_plan_rejects_early_discharge_at_repeated_current_port():
    instance, plan = repeated_call_problem()
    result = apply_plan(instance, plan)
    assert not result.legal
    assert any(v.move_index == 1 and v.containers == [plan.moves[1].container_id]
               and "discharge list" in v.message for v in result.violations)
    assert result.count.discharges == 0
    assert result.count.total_lifts_now == 1  # Only opening the cover succeeded.
    assert {p.container_id for p in result.final_state.ship.placements} == {
        box.id for box in instance.containers[-2:]
    }


def test_full_checker_rejects_early_discharge_in_moves_part():
    instance, plan = repeated_call_problem()
    report = check(instance, plan)
    assert not report.passed
    assert any(v.rule_id == "expert.moves.top_access" and v.move_index == 1
               and "discharge list" in v.message for v in report.parts["moves"].violations)


def test_single_move_still_allows_listed_import_discharge():
    instance, plan = repeated_call_problem()
    discharge = instance.containers[-2]
    # The simulator's single-move API must still accept the listed import after
    # its through blocker has been removed through the temporary-restow API.
    state = apply_move(instance, initial_state(instance), plan.moves[0])
    state = apply_move(instance, state, RestowOff(
        call_index=0, container_id=instance.containers[-1].id, src="0010104"))
    result = apply_move(instance, state, plan.moves[2])
    assert discharge.id not in {p.container_id for p in result.ship.placements}
    assert any(discharge.id in stack.boxes for stack in result.yard.stacks)


def test_repeated_port_through_box_uses_counted_restow_instead_of_discharge():
    instance, plan = repeated_call_problem()
    through = instance.containers[-1]
    plan.moves = [
        plan.moves[0],
        RestowOff(call_index=0, container_id=through.id, src="0010104"),
        plan.moves[2],
        RestowOn(call_index=0, container_id=through.id, dst="0010102"),
        plan.moves[3],
    ]
    result = apply_plan(instance, plan)
    assert result.legal, result.violations
    assert (result.count.discharges, result.count.restow_lifts,
            result.count.hatch_cover_lifts, result.count.total_lifts_now) == (1, 2, 2, 5)
    assert result.count.weighted_nonproductive == 6
    assert [(p.container_id, p.slot) for p in result.final_state.ship.placements] == [
        (through.id, "0010102")
    ]


def test_listed_discharge_still_requires_current_port_pod():
    instance, plan = repeated_call_problem()
    # Mutated instances bypass model validation, so replay retains its POD guard.
    instance.containers[-2].pod = "THBKK"
    with pytest.raises(IllegalMoveError) as caught:
        apply_move(instance, initial_state(instance), plan.moves[2])
    assert "destination" in caught.value.violation.message
