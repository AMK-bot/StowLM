"""Frozen planner metadata and empty-yard regressions.

The no-load oracle exercises real expansion/replay without claiming that the
model fixture's placeholder hull is a qualified physical condition.
"""
from __future__ import annotations

import pytest

from stowage.actions import get_action_space
from stowage.actions.space import Discharge
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves import apply_plan
from stowage.planner import Planner, run_planner
from stowage.solver import twostep


def _no_load_instance(*, discharge: bool = False, equipment: bool = False) -> Instance:
    data = build_instance().model_dump()
    if not equipment:
        data["port"]["yard"]["blocks"] = []
        data["port"]["equipment"] = []
    data["yard"]["stacks"] = []
    data["load_list"] = []
    data["containers"] = []
    if discharge:
        box = build_instance().containers[2].model_copy(update={"pod": "THLCH"})
        data["stage"] = "B"
        data["containers"] = [box.model_dump()]
        data["onboard"]["placements"] = [{"container_id": box.id, "slot": "0010182"}]
        data["discharge_list"] = [box.id]
    return Instance.model_validate(data)


class _NoLoadOracle:
    """Only allow the fixture's discharge, checked by real physical replay."""

    def __init__(self, instance, space):
        self.instance, self.space = instance, space
        self.history = []

    def legal_actions(self):
        done = {a.box for a in self.history}
        candidates = [Discharge(cid) for cid in self.instance.discharge_list if cid not in done]
        legal = []
        for action in candidates:
            try:
                plan = self.space.expand(self.instance, [*self.history, action])
            except ValueError:
                continue
            if apply_plan(self.instance, plan).legal:
                legal.append(action)
        return legal

    def is_legal(self, action):
        return action in self.legal_actions()

    def push(self, action):
        assert self.is_legal(action)
        self.history.append(action)

    def copy(self):
        other = type(self)(self.instance, self.space)
        other.history = self.history[:]
        return other

    def is_complete(self):
        return {a.box for a in self.history} == set(self.instance.discharge_list)

    def dead_end(self):
        return None


@pytest.mark.parametrize("discharge", [False, True], ids=["empty", "discharge-only"])
def test_canonical_no_load_needs_no_yard_equipment(discharge):
    instance = _no_load_instance(discharge=discharge)
    canonical = twostep._canonical_instance(instance)
    assert canonical.port.equipment == []
    assert canonical.port.yard.blocks == canonical.yard.stacks == []
    assert {b.id for b in canonical.containers} == set(instance.discharge_list)


@pytest.mark.parametrize("discharge", [False, True], ids=["empty", "discharge-only"])
def test_no_load_plan_with_empty_yard_and_equipment(monkeypatch, discharge):
    monkeypatch.setattr(twostep, "make_feasibility_engine", _NoLoadOracle)
    instance = _no_load_instance(discharge=discharge)
    before = instance.model_dump()
    result = twostep.TwoStepPlanner().plan(instance, time_limit_s=2)
    if discharge:
        # A schema-valid yard with no blocks has no physical discharge target.
        assert result.status == "dead_end", result.message
        assert result.plan is None
        assert "IndexError" not in (result.message or "")
        return
    assert result.status == "ok", result.message
    assert result.plan is not None
    assert result.metrics["preplan_slots"] == []
    assert result.metrics["terminal_status"] == "ok"
    actions = get_action_space("boxslot").from_plan(instance, result.plan)
    assert actions == [Discharge(cid) for cid in instance.discharge_list]
    replay = apply_plan(instance, result.plan)
    assert replay.legal
    assert replay.count.loads == 0
    assert replay.count.discharges == int(discharge)
    assert instance.model_dump() == before


def test_discharge_only_plan_with_equipped_empty_yard(monkeypatch):
    monkeypatch.setattr(twostep, "make_feasibility_engine", _NoLoadOracle)
    instance = _no_load_instance(discharge=True, equipment=True)
    result = twostep.TwoStepPlanner().plan(instance, time_limit_s=2)
    assert result.status == "ok", result.message
    actions = get_action_space("boxslot").from_plan(instance, result.plan)
    assert actions == [Discharge(cid) for cid in instance.discharge_list]
    replay = apply_plan(instance, result.plan)
    assert replay.legal and replay.count.discharges == 1
    assert replay.count.loads == 0


def test_raw_result_uses_frozen_baseline_id():
    planner = twostep.TwoStepPlanner()
    assert isinstance(planner, Planner)
    result = planner.plan(_no_load_instance(), time_limit_s=0)
    assert planner.name == result.planner == "baseline.twostep"


def test_run_planner_preserves_frozen_baseline_id():
    result, report = run_planner(
        twostep.TwoStepPlanner(), _no_load_instance(), time_limit_s=0, allow_repair=False)
    assert result.planner == "baseline.twostep"
    assert result.status == "timeout"
    assert result.plan is report is None
    assert result.metrics["repair_status"] == "disabled"
    assert result.shippable is False


def test_raw_error_retains_frozen_baseline_id(monkeypatch):
    def unavailable(*args, **kwargs):
        raise NotImplementedError("explicit dependency failure")

    monkeypatch.setattr(twostep, "make_feasibility_engine", unavailable)
    result = twostep.TwoStepPlanner().plan(_no_load_instance(), time_limit_s=2)
    assert result.status == "error"
    assert result.plan is None
    assert result.planner == "baseline.twostep"


def test_wall_clock_bounded_planner_declares_nondeterminism():
    planner = twostep.TwoStepPlanner()
    assert planner.deterministic is False


@pytest.mark.parametrize("budget", [0, 2], ids=["timeout", "completed"])
def test_raw_result_discloses_nondeterministic_wall_clock_search(monkeypatch, budget):
    monkeypatch.setattr(twostep, "make_feasibility_engine", _NoLoadOracle)
    result = twostep.TwoStepPlanner().plan(_no_load_instance(), seed=7, time_limit_s=budget)
    assert result.status == ("ok" if budget else "timeout"), result.message
    assert result.metrics["deterministic"] == 0
