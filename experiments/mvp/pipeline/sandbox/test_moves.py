"""Offline tests of actual crane replay and independent checker diagnostics."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.contract.schema import MoveVerdict
from experiments.mvp.pipeline.sandbox import moves
from stowage.checker.rules import check_static_rules
from stowage.model import CheckReport, MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import Load
from stowage.model.ship import Placement
from stowage.moves import apply_plan


def case():
    instance = build_instance()
    forty, a, b, c = [box.id for box in instance.containers]
    plan = MovePlan(id="sandbox.test", instance_id=instance.id, call_index=0, moves=[
        Load(call_index=0, container_id=forty, src="A-001-01-01", dst="0020282"),
        Load(call_index=0, container_id=a, src="B-001-01-01", dst="0010182"),
        Load(call_index=0, container_id=b, src="A-003-01-01", dst="0030182"),
        Load(call_index=0, container_id=c, src="A-004-01-01", dst="0010184"),
    ])
    assert apply_plan(instance, plan).legal
    return instance, plan, (forty, a, b, c)


@pytest.fixture
def parts(monkeypatch):
    # Physics has its own real-computation integration tests. Isolate crane and
    # report plumbing here so these tests don't require a certified hull fixture.
    module = SimpleNamespace(fast_check=lambda instance, ship: (
        [moves.violation_to_dict(v) for v in check_static_rules(instance, ship, call_index=0).violations],
        {"gm_m": None}), full_gauges=lambda instance, ship, report: {"gm_m": None})
    monkeypatch.setitem(sys.modules, "experiments.mvp.pipeline.sandbox.physics", module)
    import stowage.checker

    def check(instance, plan):
        replay = apply_plan(instance, plan)
        ship = replay.final_state.ship
        part = check_static_rules(instance, ship, call_index=instance.call_index)
        violations = [*replay.violations, *part.violations]
        return CheckReport(passed=not any(v.severity == "hard" for v in violations),
                           violations=violations, parts={"rules": part}, count=replay.count)
    monkeypatch.setattr(stowage.checker, "check", check)


def test_swap_proposes_real_plan_and_preserves_inputs():
    instance, plan, (_, a, b, _) = case()
    before = instance.model_dump_json(), plan.model_dump_json()
    prepared = moves.prepare_move(instance.model_dump(mode="json"), plan.model_dump(mode="json"), a, "0030182")
    assert prepared.replay.legal
    assert {p.container_id: p.slot for p in prepared.ship.placements}[b] == "0010182"
    assert {p.container_id: p.slot for p in prepared.ship.placements}[a] == "0030182"
    assert prepared.swapped_box == b
    assert (instance.model_dump_json(), plan.model_dump_json()) == before
    assert moves._counts(prepared)["extra_lifts"] == 0
    assert moves._counts(prepared)["crane_order_preserved"] is True
    assert moves.move_plan(instance, plan, a, "0030182").model_dump(mode="json") == prepared.plan.model_dump(mode="json")


def test_arrival_cargo_has_actual_engine_shift_lift():
    instance, original, (_, _, _, c) = case()
    replay = apply_plan(instance, original)
    instance.onboard = replay.final_state.ship
    instance.yard = replay.final_state.yard
    instance.load_list = []
    plan = original.model_copy(update={"moves": []})
    prepared = moves.prepare_move(instance, plan, c, "0030184")
    assert prepared.replay.legal
    assert prepared.replay.count.restow_lifts == 1
    assert moves._counts(prepared)["extra_lifts"] == 1
    assert moves._counts(prepared)["crane_order_preserved"] is False
    assert "adding explicit restow lifts" in moves._counts(prepared)["scope_note"]


def test_fast_is_emitted_before_full_and_contract_is_json(parts):
    instance, plan, (_, _, _, c) = case()
    emitted = []
    result = moves.try_move(instance, plan, c, "0030184", on_fast=emitted.append)
    assert len(emitted) == 1
    fast = emitted[0]
    assert fast.screen == "fast" and fast.gauges["full_check_pending"]
    assert fast.gauges["label"] == "fast screen, full check running"
    assert result.screen == "full" and not result.gauges["full_check_pending"]
    assert result.wall_s >= fast.wall_s
    assert MoveVerdict.model_validate(json.loads(result.model_dump_json())) == result


def test_targeted_stack_failure_survives_replay_rejection(parts):
    instance, plan, (_, _, b, c) = case()
    # A twenty-foot box above a forty-foot box stops crane replay. Nevertheless
    # the requested destination has a real 1 kg stack rating.
    target = next(s for s in instance.vessel.stacks if "0030284" in s.cell_ids)
    target.max_mass_kg = target.max_mass_20_kg = target.max_mass_40_kg = 1
    result = moves.try_move(instance, plan, b, "0030284")
    assert not result.legal
    assert result.gauges["diagnostic_only"]
    assert result.extra_lifts is None
    failure = next(v for v in result.broken if v["rule_id"] == "expert.physical.stack_weight" and b in v["boxes"])
    # The 40-foot base contributes half its mass to this physical column.
    forty = instance.containers[0]
    assert failure["measured"] == next(box.gross_mass_kg for box in instance.containers if box.id == b) + forty.gross_mass_kg/2
    assert failure["limit"] == 1 and b in failure["boxes"]
    assert any(v["source"] == "proposal_replay" for v in result.broken)
    assert failure["source"] == "requested_stow_snapshot"


def test_swap_between_arrival_cargo_and_new_load_uses_explicit_buffer_lifts():
    instance, original, (_, a, b, _) = case()
    instance.yard.stacks = [s for s in instance.yard.stacks if a not in s.boxes]
    instance.onboard.placements = [Placement(container_id=a, slot="0010182")]
    instance.load_list.remove(a)
    plan = original.model_copy(update={"moves": [m for m in original.moves if m.container_id != a]})
    prepared = moves.prepare_move(instance, plan, b, "0010182")
    assert prepared.replay.legal
    assert moves._same_placements(prepared.replay.final_state.ship, prepared.ship)
    assert moves._counts(prepared)["extra_lifts"] == 2


@pytest.mark.parametrize("box, slot", [("missing", "0010182"), ("missing", "not-a-slot")])
def test_invalid_input_is_clean_and_does_not_invent_checker_rules(box, slot):
    instance, plan, _ = case()
    result = moves.try_move(instance, plan, box, slot)
    assert not result.legal and result.gauges["input_error"]
    assert result.broken == [] and result.extra_lifts is None


def test_extracts_original_checker_measurement_and_nonnumeric_null():
    from stowage.model import Violation
    report = CheckReport(passed=False, violations=[Violation(rule_id="expert.physical.stack_weight",
        severity="hard", call_index=0, measured=63000, limit=60000, unit="kg", message="Too heavy")])
    row = moves.broken_from_report(report)[0]
    assert row["measured"] == 63000 and row["limit"] == 60000 and row["unit"] == "kg"
    assert row["boxes"] == []
