"""Repair orchestration tests; doubles isolate search, mask, ballast and checker admission.

The fixture vessel is synthetic. Passing the checker double here tests the
admission protocol, and does not certify its illustrative physics numbers.
"""
from __future__ import annotations

import importlib

import pytest

from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.model import BallastCondition, CheckReport, PlannerResult, Violation


@pytest.fixture
def case(monkeypatch):
    # Candidate legality is tested with the real engine in integration/rule
    # tests. These deliberately synthetic reconstructions isolate orchestration.
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

    def result(sequence, status="ok"):
        return PlannerResult(status=status, plan=space.expand(instance, sequence),
                             planner="stowlm", planner_version="test", seed=19,
                             metrics={"original_metric": 7})

    return instance, actions, alternative, result


@pytest.fixture
def gates(monkeypatch):
    """Record the required finalization-before-check ordering."""
    planner = importlib.import_module("stowage.planner")
    checker = importlib.import_module("stowage.checker")
    events = []

    def finalize(instance, result, **kwargs):
        events.append("finalize")
        assert result.status == "ok"
        ballast = BallastCondition(tank_masses_kg={}, source="finisher")
        return result.model_copy(update={"plan": result.plan.model_copy(update={"ballast": ballast})})

    def check(instance, plan):
        events.append("check")
        assert events[-2] == "finalize"
        assert plan.ballast is not None
        return CheckReport(passed=True)

    monkeypatch.setattr(planner, "finalize", finalize)
    monkeypatch.setattr(checker, "check", check)
    return events


def test_dead_end_backs_off_to_longest_completable_prefix(case, gates, monkeypatch):
    instance, actions, alternative, make_result = case
    scalable = importlib.import_module("stowage.solver.scalable")
    attempted = []

    def complete(instance, prefix, **kwargs):
        attempted.append(list(prefix))
        assert kwargs["seed"] == 19
        assert 0 < kwargs["time_limit_s"] <= 3
        if len(prefix) > 1:
            return make_result(prefix, "dead_end")
        return make_result(alternative)

    monkeypatch.setattr(scalable, "complete", complete)
    from stowage.solver.repair import repair
    original = make_result(actions[:3], "dead_end")
    fixed = repair(instance, original, None, seed=19, time_limit_s=3)
    assert [len(prefix) for prefix in attempted] == [3, 2, 1]
    assert fixed.status == "ok"
    assert fixed.repaired and fixed.repair_note
    assert gates == ["finalize", "check"]
    assert fixed.metrics["repair_succeeded"]
    assert fixed.metrics["repair_kept_action_fraction"] == pytest.approx(1 / 3)
    assert fixed.metrics["repair_actions_changed"] > 0
    assert fixed.metrics["repair_time_s"] >= 0
    assert fixed.metrics["repair_move_count_delta"] == len(fixed.plan.moves) - len(original.plan.moves)
    assert original.status == "dead_end" and original.plan.ballast is None


@pytest.mark.parametrize("reference", ["container", "slot", "move"])
def test_checker_failure_removes_involved_action_and_dependent_suffix(case, gates, monkeypatch, reference):
    instance, actions, alternative, make_result = case
    original = make_result(actions)
    details = {"container": {"containers": [actions[1].box]},
               "slot": {"slots": [actions[1].slot]},
               "move": {"move_index": next(i for i, move in enumerate(original.plan.moves)
                                           if getattr(move, "container_id", None) == actions[1].box)}}
    report = CheckReport(passed=False, violations=[Violation(
        rule_id="lashing.stack_limit", severity="hard", call_index=instance.call_index,
        message="Constructed stack violation.", **details[reference])])
    attempted = []

    def complete(instance, prefix, **kwargs):
        attempted.append(list(prefix))
        return make_result(alternative)

    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "complete", complete)
    from stowage.solver.repair import repair
    fixed = repair(instance, original, report, time_limit_s=3)
    assert fixed.status == "ok"
    assert attempted[0] == actions[:1]
    assert actions[2] not in attempted[0]  # its support was removed
    assert gates == ["finalize", "check"]
    assert fixed.repaired and fixed.metrics["repair_succeeded"]


def test_checker_rejection_never_returns_a_shippable_plan(case, gates, monkeypatch):
    instance, actions, _, make_result = case
    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "complete",
                        lambda instance, prefix, **kwargs: make_result(actions))
    checks = []

    def reject(instance, plan):
        checks.append(plan)
        assert plan.ballast is not None
        return CheckReport(passed=False)

    monkeypatch.setattr(importlib.import_module("stowage.checker"), "check", reject)
    from stowage.solver.repair import repair
    fixed = repair(instance, make_result(actions, "dead_end"), None, time_limit_s=3)
    assert checks
    assert fixed.status in {"error", "timeout"}
    assert fixed.plan is None
    assert fixed.repaired
    assert not fixed.metrics["repair_succeeded"]


def test_violation_in_forced_yard_lift_cuts_the_triggering_action(case, gates, monkeypatch):
    instance, actions, alternative, make_result = case
    original = make_result(actions)
    forced_index = next(i for i, move in enumerate(original.plan.moves) if move.kind == "relocate")
    assert forced_index == 0  # loading box zero first moves a yard blocker
    report = CheckReport(passed=False, violations=[Violation(
        rule_id="moves.top_box_only", severity="hard", call_index=instance.call_index,
        move_index=forced_index, message="Constructed forced-lift violation.")])
    attempted = []

    def complete(instance, prefix, **kwargs):
        attempted.append(list(prefix))
        return make_result(alternative)

    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "complete", complete)
    from stowage.solver.repair import repair
    fixed = repair(instance, original, report, time_limit_s=3)
    assert fixed.status == "ok"
    assert attempted[0] == []
    assert gates == ["finalize", "check"]


def test_failed_ballast_finalization_cannot_be_admitted(case, monkeypatch):
    instance, actions, _, make_result = case
    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "complete",
                        lambda instance, prefix, **kwargs: make_result(actions))
    monkeypatch.setattr(importlib.import_module("stowage.planner"), "finalize",
                        lambda instance, result, **kwargs: result.model_copy(update={
                            "status": "error", "metrics": {"ballast_status": "not_found"}}))
    from stowage.solver.repair import repair
    fixed = repair(instance, make_result(actions, "dead_end"), None, time_limit_s=3)
    assert fixed.status in {"error", "timeout"}
    assert fixed.plan is None
    assert fixed.repaired and not fixed.metrics["repair_succeeded"]


def test_zero_budget_reports_timeout_and_fallback_without_search(case, monkeypatch):
    instance, actions, _, make_result = case

    def forbidden(*args, **kwargs):
        pytest.fail("No search should start without a budget")

    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "complete", forbidden)
    from stowage.solver.repair import repair
    fixed = repair(instance, make_result(actions, "dead_end"), None, time_limit_s=0)
    assert fixed.status == "timeout"
    assert fixed.plan is None
    assert fixed.repaired and not fixed.metrics["repair_succeeded"]
    assert fixed.metrics["repair_time_s"] >= 0


def test_completion_exception_reports_failure_instead_of_raising(case, monkeypatch):
    instance, actions, _, make_result = case

    def broken(*args, **kwargs):
        raise RuntimeError("constructed oracle failure")

    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "complete", broken)
    from stowage.solver.repair import repair
    fixed = repair(instance, make_result(actions, "dead_end"), None, time_limit_s=3)
    assert fixed.status == "error"
    assert fixed.plan is None
    assert fixed.repaired and not fixed.metrics["repair_succeeded"]
    assert "constructed oracle failure" in (fixed.message or fixed.repair_note)


def test_actual_full_checker_rejects_incomplete_completion(case, monkeypatch):
    """The real checker, including all-loaded, remains the final admission gate."""
    instance, actions, _, make_result = case
    checker = importlib.import_module("stowage.checker")
    real_check = checker.check
    reports = []

    def checked(instance, plan):
        report = real_check(instance, plan)
        reports.append(report)
        return report

    def finalized(instance, result, **kwargs):
        # Only the ballast search is isolated; actual physics and all other
        # full-checker parts execute against this synthetic empty departure.
        ballast = BallastCondition(tank_masses_kg={}, source="finisher")
        return result.model_copy(update={"plan": result.plan.model_copy(update={"ballast": ballast})})

    monkeypatch.setattr(checker, "check", checked)
    monkeypatch.setattr(importlib.import_module("stowage.planner"), "finalize", finalized)
    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "complete",
                        lambda instance, prefix, **kwargs: make_result([]))
    from stowage.solver.repair import repair
    fixed = repair(instance, make_result(actions, "dead_end"), None, time_limit_s=3)
    assert reports
    assert all(not report.passed for report in reports)
    assert all("objective.all_loaded" in {v.rule_id for v in report.violations} for report in reports)
    assert fixed.status in {"error", "timeout"}
    assert fixed.plan is None
    assert fixed.repaired and not fixed.metrics["repair_succeeded"]


def test_success_uses_final_candidate_metrics_and_preserves_input_snapshot(case, gates, monkeypatch):
    instance, actions, _, make_result = case
    original = make_result(actions, "dead_end").model_copy(update={
        "metrics": {"ballast_status": "not_found", "original_metric": 7, "nodes": 99}})
    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "complete",
                        lambda *args, **kwargs: make_result(actions).model_copy(update={
                            "metrics": {"ballast_status": "ok", "nodes": 2}}))
    planner = importlib.import_module("stowage.planner")
    finalize = planner.finalize
    def final_metrics(instance, candidate, **kwargs):
        candidate = finalize(instance, candidate, **kwargs)
        return candidate.model_copy(update={"metrics": {**candidate.metrics,
            "ballast_status": "ok", "finalizer_only": 42}})
    monkeypatch.setattr(planner, "finalize", final_metrics)
    from stowage.solver.repair import repair
    fixed = repair(instance, original, None, time_limit_s=3)
    assert fixed.status == "ok"
    assert fixed.metrics["ballast_status"] == "ok"
    assert fixed.metrics["finalizer_only"] == 42
    assert fixed.metrics["nodes"] == 2
    assert "original_metric" not in fixed.metrics
    assert fixed.metrics["repair_original_metrics"]["original_metric"] == 7
    assert fixed.metrics["repair_original_metrics"] == original.metrics


@pytest.mark.parametrize("budget", [0, -1, float("nan")])
def test_move_only_early_return_has_canonical_action_accounting(case, budget):
    instance, actions, _, make_result = case
    original = make_result(actions[:1], "dead_end")
    assert len(original.plan.moves) == 2
    original.plan.actions = None
    from stowage.solver.repair import repair
    fixed = repair(instance, original, None, time_limit_s=budget)
    assert fixed.metrics["repair_input_actions"] == 1
    assert fixed.metrics["repair_actions_removed"] == 1
    assert "kept 0/1" in fixed.repair_note


def test_success_exposes_canonical_action_diff(case, gates, monkeypatch):
    instance, actions, alternative, make_result = case
    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "complete",
                        lambda *args, **kwargs: make_result(alternative))
    from stowage.solver.repair import repair
    report = CheckReport(passed=False, violations=[Violation(
        rule_id="lashing.stack_limit", severity="hard", call_index=instance.call_index,
        message="Constructed cargo failure", containers=[actions[1].box])])
    fixed = repair(instance, make_result(actions), report, time_limit_s=3)
    space = get_action_space()
    assert fixed.metrics["repair_removed_actions"] == [space.encode(a) for a in actions[1:3]]
    assert fixed.metrics["repair_added_actions"] == [space.encode(a) for a in alternative[1:3]]
    assert fixed.metrics["repair_cargo_changed"] is True
    assert actions[1].box in fixed.repair_note
    assert actions[1].slot in fixed.repair_note
    assert alternative[1].slot in fixed.repair_note


@pytest.mark.parametrize("refinement", ["succeeds", "fails", "raises", "times_out"])
def test_widened_search_refines_skipped_longer_prefix(case, gates, monkeypatch, refinement):
    instance, actions, _, make_result = case
    module = importlib.import_module("stowage.solver.repair")
    space = get_action_space()
    # Isolate the search schedule from physical replay, as the review's outcome
    # pattern concerns bounded heuristic probes rather than a physics claim.
    class ScheduleSpace:
        decode = space.decode
        encode = space.encode
        def from_plan(self, instance, plan):
            return [space.decode(a) for a in plan.actions]
    monkeypatch.setattr(module, "get_action_space", lambda *args: ScheduleSpace())
    sequence = [actions[0]] * 20
    clock = [0.]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    def result(prefix, status="ok"):
        value = make_result([], status)
        value.plan.actions = [space.encode(a) for a in prefix]
        return value
    attempted = []
    def complete(instance, prefix, **kwargs):
        attempted.append(len(prefix))
        if len(prefix) == 11:
            if refinement == "raises":
                raise RuntimeError("refinement probe failed")
            if refinement == "times_out":
                clock[0] = 4.
            if refinement == "fails":
                return result(prefix, "dead_end")
        return result(prefix, "dead_end" if len(prefix) > 11 else "ok")
    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "complete", complete)
    fixed = module.repair(instance, result(sequence, "dead_end"), None, time_limit_s=3)
    assert fixed.status == "ok"
    assert fixed.metrics["repair_prefix_length"] == (11 if refinement == "succeeds" else 10)
    assert 11 in attempted


def test_unrepresentable_early_return_marks_accounting_unavailable(case):
    instance, actions, _, make_result = case
    original = make_result(actions[:1], "dead_end")
    original.plan.actions = None
    original.plan.moves[0] = original.plan.moves[0].model_copy(update={"container_id": "unknown"})
    from stowage.solver.repair import repair
    fixed = repair(instance, original, None, time_limit_s=0)
    assert fixed.metrics["repair_action_accounting"] == "unavailable"
    assert fixed.metrics["repair_input_actions"] is None
    assert fixed.metrics["repair_actions_removed"] is None
    assert "unavailable" in fixed.repair_note


def test_checked_plan_is_returned_unchanged_with_bound_shipping_evidence(case, gates, monkeypatch):
    instance, actions, _, make_result = case
    checker = importlib.import_module("stowage.checker")
    real_check = importlib.import_module("stowage.checker.full").check
    from stowage.model import MoveCount, PartReport
    reports = []
    plans = []
    def check(instance, plan):
        # Protocol-only positive oracle: real checker supplies content linkage;
        # synthetic positive parts here do not claim physical feasibility.
        report = real_check(instance, plan).model_copy(update={
            "passed": True, "count": MoveCount(), "violations": [], "shut_out": [],
            "parts": {name: PartReport(name=name) for name in (
                "moves", "rules", "stability", "strength", "visibility", "lashing", "constraints")}})
        reports.append(report)
        plans.append(plan)
        return report
    monkeypatch.setattr(checker, "check", check)
    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "complete",
                        lambda *args, **kwargs: make_result(actions))
    from stowage.solver.repair import repair
    fixed = repair(instance, make_result(actions, "dead_end"), None, time_limit_s=3)
    assert fixed.plan is plans[-1]
    assert checker.report_matches_inputs(reports[-1], instance, fixed.plan)
    assert fixed.shippable
    assert fixed.metrics["shippable"] is True
    fixed.plan.id += ".mutated"
    assert not fixed.shippable
