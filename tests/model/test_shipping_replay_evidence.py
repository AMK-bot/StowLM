"""A passing verdict needs replay evidence, including a valid zero move count."""

from types import SimpleNamespace

import stowage.interfaces  # noqa: F401
from stowage.model import CheckReport, MoveCount, MovePlan, PartReport, PlannerResult
from stowage.planner import run_planner


def _checked_candidate(instance, monkeypatch, *, count):
    report = CheckReport(
        passed=True,
        count=count,
        parts={name: PartReport(name=name) for name in (
            "moves", "rules", "stability", "strength", "visibility", "lashing", "constraints"
        )},
    )
    raw = PlannerResult(
        status="ok", planner="test", planner_version="1",
        plan=MovePlan(id="replay-evidence", instance_id=instance.id, call_index=instance.call_index),
    )
    planner = SimpleNamespace(
        name="test", version="1", sets_ballast=False, plan=lambda *a, **kw: raw,
    )
    # Clean synthetic oracle exercises the guarded pipeline without claiming that
    # this hull-free unit fixture passes the real physical checker.
    monkeypatch.setattr("stowage.planner.finalize", lambda i, r, **kw: r)
    monkeypatch.setattr("stowage.checker.check", lambda *a, **kw: report)
    return run_planner(planner, instance, allow_repair=False)


def test_missing_replay_count_cannot_ship(empty_ship_instance, monkeypatch):
    result, report = _checked_candidate(empty_ship_instance, monkeypatch, count=None)

    assert report.passed
    assert report.count is None
    assert not result.shippable
    assert not result.metrics["shippable"]


def test_zero_replay_count_can_ship(empty_ship_instance, monkeypatch):
    result, report = _checked_candidate(empty_ship_instance, monkeypatch, count=MoveCount())

    assert report.passed
    assert report.count is not None
    assert report.count.total_lifts_now == 0
    assert result.shippable
    assert result.metrics["shippable"]
