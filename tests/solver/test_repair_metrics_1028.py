"""Repair metrics describe the replacement plan, with input claims archived.

Search/mask/finalizer/checker doubles isolate metric provenance. Their acceptance
does not assert physical feasibility for the synthetic fixture.
"""

import importlib

import pytest

from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.model import CheckReport, PlannerResult, Violation
from stowage.solver.repair import repair


@pytest.fixture
def exact_style_result(monkeypatch):
    class CandidateMask:
        def is_legal(self, action):
            return True

        def push(self, action):
            pass

        def is_complete(self):
            return True

    monkeypatch.setattr(importlib.import_module("stowage.feasibility"), "make_feasibility_engine",
                        lambda *args, **kwargs: CandidateMask())
    instance = tiny_instance(571, n_load=4, n_other=0, p40=0)
    boxes = instance.load_list
    actions = [Place(boxes[0], "0010282"), Place(boxes[1], "0010182"),
               Place(boxes[2], "0010184"), Place(boxes[3], "0030282")]
    alternative = [actions[0], Place(boxes[1], "0030182"),
                   Place(boxes[2], "0030184"), actions[3]]
    space = get_action_space()
    original = PlannerResult(
        status="ok", plan=space.expand(instance, actions), planner="exact",
        planner_version="test", metrics={
            "optimality_status": "OPTIMAL", "objective": 4,
            "lower_bound": 4, "gap": 0., "expanded_nodes": 123,
            "ballast_status": "ok", "original_only": {"nested": [7]},
        })
    return instance, original, space.expand(instance, alternative)


@pytest.mark.parametrize("candidate_metrics", [
    {"replacement_only": True},
    {"replacement_only": True, "objective": 6, "lower_bound": 4, "gap": 1 / 3},
])
def test_repair_replaces_exact_metrics_without_inheriting_certificate(
    exact_style_result, monkeypatch, candidate_metrics,
):
    instance, original, replacement_plan = exact_style_result
    replacement = PlannerResult(
        status="ok", plan=replacement_plan, planner="scalable",
        planner_version="test", metrics=candidate_metrics)
    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "complete",
                        lambda *args, **kwargs: replacement)
    monkeypatch.setattr(importlib.import_module("stowage.planner"), "finalize",
                        lambda instance, candidate, **kwargs: candidate)
    monkeypatch.setattr(importlib.import_module("stowage.checker"), "check",
                        lambda *args, **kwargs: CheckReport(passed=True))
    failed = CheckReport(passed=False, violations=[Violation(
        rule_id="lashing.stack_limit", severity="hard", call_index=instance.call_index,
        message="Synthetic failure forcing changed cargo actions",
        containers=[instance.load_list[1]])])

    fixed = repair(instance, original, failed, time_limit_s=3)

    assert fixed.status == "ok"
    assert fixed.metrics["repair_cargo_changed"] is True
    assert fixed.plan.moves != original.plan.moves
    assert fixed.metrics.get("optimality_status") != "OPTIMAL"
    for key in ("objective", "lower_bound", "gap"):
        assert fixed.metrics.get(key) == candidate_metrics.get(key)
    for key in ("expanded_nodes", "ballast_status", "original_only"):
        assert key not in fixed.metrics
    assert fixed.metrics["replacement_only"] is True
    assert fixed.metrics["repair_original_metrics"] == original.metrics
    fixed.metrics["repair_original_metrics"]["original_only"]["nested"].append(8)
    assert original.metrics["original_only"]["nested"] == [7]


def test_failed_repair_does_not_retain_original_certificate(exact_style_result):
    instance, original, _ = exact_style_result

    failed = repair(instance, original, None, time_limit_s=0)

    assert failed.status == "timeout"
    assert failed.plan is None
    assert failed.metrics["repair_original_metrics"] == original.metrics
    assert all(key not in failed.metrics for key in original.metrics)
