"""Reported budget includes synchronous lower-bound and oracle overruns."""
from types import SimpleNamespace

from stowage.solver import scalable
from tests.solver.test_scalable import problem


def test_lower_bound_overrun_is_disclosed_without_starting_oracle(monkeypatch):
    clock = [0.0]

    def slow_bound(*args, **kwargs):
        clock[0] = 3.0
        return SimpleNamespace(weighted=0.0)

    def forbidden(*args, **kwargs):
        raise AssertionError("expired lower bound must not start oracle")

    monkeypatch.setattr(scalable.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(scalable, "lower_bound", slow_bound)
    monkeypatch.setattr(scalable, "make_feasibility_engine", forbidden)
    result = scalable.ScalableSolver().plan(problem(), time_limit_s=2.0)
    assert result.status == "timeout" and result.plan is None
    assert result.wall_time_s == 3.0
    assert result.metrics["time_budget_s"] == 2.0
    assert result.metrics["budget_overrun_s"] == 1.0
