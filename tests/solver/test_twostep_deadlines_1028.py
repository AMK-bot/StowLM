"""Deadline regressions; mechanical doubles make no physical-feasibility claim."""
from __future__ import annotations

import math

import pytest

from stowage.solver import twostep, twostep_terminal
from tests.solver.test_twostep import PhysicalOnlyOracle, interchangeable_instance


@pytest.mark.parametrize("budget", [-1.0, math.inf, -math.inf, math.nan])
def test_invalid_budget_never_constructs_oracle(monkeypatch, budget):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid budget must not start search")

    monkeypatch.setattr(twostep, "make_feasibility_engine", forbidden)
    result = twostep.TwoStepPlanner().plan(interchangeable_instance(), time_limit_s=budget)
    assert result.status == "error"
    assert result.plan is None
    assert result.message == "time_limit_s must be finite and nonnegative"
    assert result.metrics["preplan_status"] == "not_started"
    with pytest.raises(ValueError, match="finite and nonnegative"):
        twostep.preplan(interchangeable_instance(), time_limit_s=budget)


def test_terminal_does_not_mutate_after_membership_exhausts_deadline(monkeypatch):
    monkeypatch.setattr(twostep, "make_feasibility_engine", PhysicalOnlyOracle)
    instance = interchangeable_instance()
    prestow = twostep.preplan(instance, time_limit_s=5.0)
    assert prestow.status == "ok"
    clock = [0.0]
    mutations = []

    class SlowMembershipOracle(PhysicalOnlyOracle):
        def is_legal(self, action):
            answer = super().is_legal(action)
            clock[0] = 2.0  # membership began in budget and returned after it
            return answer

        def push(self, action):
            mutations.append(action)
            super().push(action)

    monkeypatch.setattr(twostep_terminal, "monotonic", lambda: clock[0])
    plan, metrics, status, _ = twostep_terminal.terminal_plan(
        instance, prestow, deadline=1.0, seed=0, engine_factory=SlowMembershipOracle)
    assert status == "timeout" and plan is None
    assert metrics["terminal_cp_solves"] == 1
    assert metrics["terminal_expansions"] == 0
    assert mutations == []


def test_opaque_carrier_overrun_is_disclosed(monkeypatch):
    clock = [0.0]

    class SlowInitOracle(PhysicalOnlyOracle):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            clock[0] = 3.0

    monkeypatch.setattr(twostep, "make_feasibility_engine", SlowInitOracle)
    monkeypatch.setattr(twostep, "monotonic", lambda: clock[0])
    result = twostep.TwoStepPlanner().plan(interchangeable_instance(), time_limit_s=2.0)
    assert result.status == "timeout" and result.plan is None
    assert result.wall_time_s == 3.0
    assert result.metrics["time_budget_s"] == 2.0
    assert result.metrics["budget_overrun_s"] == 1.0
