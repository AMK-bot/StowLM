"""The planner pipeline always checks candidates and preserves fallback evidence."""
import pytest

from dataclasses import replace
from types import SimpleNamespace

import stowage.interfaces  # Register frozen endpoints before installing test overrides.
from stowage.frozen import FROZEN, stub_override
from stowage.model import CheckReport, MovePlan, PlannerResult, Provenance
from stowage.planner import run_planner


def candidate(instance, **updates):
    result = PlannerResult(status="ok", planner="test", planner_version="1", plan=MovePlan(
        id="test-plan", instance_id=instance.id, call_index=instance.call_index))
    return result.model_copy(update=updates)


def planner_for(result, *, sets_ballast=False):
    return SimpleNamespace(name="test", version="1", sets_ballast=sets_ballast,
                           plan=lambda *a, **kw: result)


def test_finalize_check_order_and_taint(empty_ship_instance):
    events = []
    inst = empty_ship_instance
    def finish(i, r, **kw):
        events.append("finalize")
        return r
    def check(i, p, **kw):
        events.append("check")
        return CheckReport(passed=True)
    with stub_override("planner.finalize", finish, reason="pipeline test"), stub_override(
        "checker.check", check, reason="pipeline test"
    ):
        result, report = run_planner(planner_for(candidate(inst)), inst)
    assert events == ["finalize", "check"]
    assert report.passed and not result.shippable and not result.metrics["shippable"]
    assert not result.metrics["fallback_needed"]
    assert result.provenance.tainted and report.provenance.tainted


def test_unavailable_repair_is_failure_not_faked_success(empty_ship_instance, monkeypatch):
    inst = empty_ship_instance
    monkeypatch.setitem(FROZEN, "solver.repair", replace(FROZEN["solver.repair"], implemented=False))
    with stub_override("planner.finalize", lambda i, r, **k: r, reason="test"), stub_override(
        "checker.check", lambda *a, **k: CheckReport(passed=False), reason="test"
    ):
        result, report = run_planner(planner_for(candidate(inst)), inst)
    assert not report.passed and not result.metrics["shippable"]
    assert result.repair_note == "repair unavailable"
    assert result.metrics["fallback_needed"] and not result.repaired


def test_repair_rechecked_and_original_taint_preserved(empty_ship_instance, monkeypatch):
    inst = empty_ship_instance
    checks = []
    monkeypatch.setitem(FROZEN, "solver.repair", replace(FROZEN["solver.repair"], implemented=True))
    def check(i, p, **kw):
        checks.append(p.id)
        return CheckReport(passed=len(checks) == 2)
    def repair(i, r, report, **kw):
        assert not report.passed
        return candidate(i, repaired=True, plan=r.plan.model_copy(update={"id": "repaired-plan"}))
    tainted = candidate(inst, provenance=Provenance(stubs={"test.original": "taint"}))
    with stub_override("planner.finalize", lambda i, r, **k: r, reason="test"), stub_override(
        "checker.check", check, reason="test"
    ), stub_override("solver.repair", repair, reason="test"):
        result, report = run_planner(planner_for(tainted), inst)
    assert checks == ["test-plan", "repaired-plan"]
    assert report.passed and result.repaired
    assert "test.original" in result.provenance.stubs
    assert result.metrics["fallback_needed"]


def test_missing_declared_ballast_cannot_ship(empty_ship_instance):
    inst = empty_ship_instance
    with stub_override("checker.check", lambda *a, **k: CheckReport(passed=True), reason="test"):
        result, report = run_planner(planner_for(candidate(inst), sets_ballast=True), inst,
                                     allow_repair=False)
    assert result.status == "error" and not report.passed
    assert report.violations[-1].rule_id == "checker.invalid_ballast"


def test_exception_and_no_plan_are_fallback_needed(empty_ship_instance):
    def fail(*a, **kw):
        raise ValueError("bad planner")
    planner = SimpleNamespace(name="broken", version="1", sets_ballast=False, plan=fail)
    result, report = run_planner(planner, empty_ship_instance, allow_repair=False)
    assert result.status == "error" and report is None
    assert result.metrics["fallback_needed"] and not result.metrics["shippable"]
    assert "bad planner" in result.message


@pytest.mark.parametrize("repair_raises", [False, True])
def test_declared_ballast_rechecked_after_unchanged_or_failed_repair(empty_ship_instance, monkeypatch, repair_raises):
    monkeypatch.setitem(FROZEN, "solver.repair", replace(FROZEN["solver.repair"], implemented=True))
    def repair(i, r, report, **kw):
        if repair_raises:
            raise RuntimeError("repair failed")
        return r
    with stub_override("checker.check", lambda *a, **k: CheckReport(passed=True), reason="regression"), \
         stub_override("solver.repair", repair, reason="regression"):
        result, report = run_planner(planner_for(candidate(empty_ship_instance), sets_ballast=True), empty_ship_instance)
    assert not report.passed and not result.shippable
    assert any(v.rule_id == "checker.invalid_ballast" for v in report.violations)


def test_unchecked_later_calls_cannot_ship(empty_ship_instance):
    raw = candidate(empty_ship_instance)
    raw.later_plans = [raw.plan.model_copy(update={"id": "unchecked", "call_index": 1})]
    with stub_override("planner.finalize", lambda i, r, **k: r, reason="regression"), \
         stub_override("checker.check", lambda *a, **k: CheckReport(passed=True), reason="regression"):
        result, report = run_planner(planner_for(raw), empty_ship_instance, allow_repair=False)
    assert report.passed and not result.shippable
