"""The deciding crane gate is never replaced by a diagnostic stow finding."""
from types import SimpleNamespace

from experiments.mvp.pipeline.sandbox import moves
from stowage.model import CheckReport, PartReport, Violation


def violation(rule, message="Reported failure"):
    return Violation(rule_id=rule, message=message, severity="hard", call_index=0)


def prepared(*, reached=True, legal=True, violations=()):
    return SimpleNamespace(replay=SimpleNamespace(legal=legal, violations=list(violations)))


def test_full_pass_is_the_only_passing_outcome():
    result = moves._outcome(prepared(), CheckReport(passed=True), True)
    assert result["outcome"] == "passes_as_edited"
    assert result["deciding_cause"]["kind"] == "passed"
    assert result["stow_assessment"]["passed"] is True
    assert result["full_check_scope"] == "edited_plan"


def test_clean_snapshot_still_refuses_fixed_crane_order():
    replay = violation("expert.moves.bottom_up")
    result = moves._outcome(prepared(legal=False, violations=[replay]), CheckReport(passed=True), False)
    assert result["outcome"] == "stow_fine_crane_order_failed"
    assert result["deciding_cause"]["rule_id"] == replay.rule_id
    assert result["deciding_cause"]["source"] == "proposal_replay"
    assert result["stow_assessment"] == {
        "passed": True, "source": "requested_stow_snapshot", "diagnostic_only": True,
        "checked_plan_reached": False, "violations": []}


def test_snapshot_rule_failure_cannot_displace_decisive_crane_failure():
    replay = violation("expert.moves.hatch_clear")
    securing = violation("iacs.member_irs.container_securing")
    report = CheckReport(passed=False, violations=[securing])
    result = moves._outcome(prepared(legal=False, violations=[replay]), report, False)
    assert result["outcome"] == "stow_breaks_rule"
    assert result["deciding_cause"]["rule_id"] == replay.rule_id
    assert result["stow_assessment"]["passed"] is False
    assert result["stow_assessment"]["violations"][0]["rule_id"] == securing.rule_id
    assert result["full_check_scope"] == "requested_stow_snapshot"


def test_reached_stow_failure_leads_with_actual_full_checker_rule():
    strength = violation("iacs.strength.shear_force")
    result = moves._outcome(prepared(), CheckReport(passed=False, violations=[strength]), True)
    assert result["outcome"] == "stow_breaks_rule"
    assert result["deciding_cause"]["rule_id"] == strength.rule_id
    assert result["deciding_cause"]["source"] == "full_plan_check"
    assert result["stow_assessment"]["diagnostic_only"] is False


def test_reached_placements_with_failed_end_condition_is_crane_only():
    covers = violation("expert.moves.hatch_clear", "All hatch covers must be replaced before departure.")
    report = CheckReport(passed=False, violations=[covers])
    result = moves._outcome(prepared(legal=False, violations=[covers]), report, True)
    assert result["outcome"] == "stow_fine_crane_order_failed"
    assert result["deciding_cause"]["rule_id"] == covers.rule_id
    assert result["stow_assessment"]["passed"] is True
    assert result["stow_assessment"]["violations"] == []


def test_unavailable_checker_is_not_an_invented_stow_safety_claim():
    error = violation("checker.internal_error")
    report = CheckReport(passed=False, violations=[error], parts={
        "strength": PartReport(name="strength", ran=False, error="unavailable", violations=[error])})
    result = moves._outcome(prepared(), report, True)
    assert result["outcome"] is None
    assert result["stow_assessment"]["passed"] is None
    assert result["deciding_cause"]["kind"] == "check_unavailable"


def test_lift_display_always_pairs_actual_with_projected_delta():
    def count(now, future):
        return SimpleNamespace(total_lifts_now=now, future=[SimpleNamespace(
            restow_lifts=future, hatch_cover_lifts=0)])
    plan = SimpleNamespace(moves=[SimpleNamespace(kind="load", container_id=str(i)) for i in range(190)])
    request = SimpleNamespace(baseline=SimpleNamespace(count=count(190, 0)), base_plan=plan,
        replay=SimpleNamespace(legal=True, executed=190, count=count(190, 24)),
        plan=plan, swapped_box=None)
    result = moves._counts(request)
    assert result["extra_lifts"] == 0
    assert result["lift_counts"]["projected_later_delta"] == 24
    assert result["lift_counts"]["display"] == (
        "Crane lifts this call: +0. Projected later-port lifts: +24 (projection).")
    assert result["scope_note"] == "This edits one box and keeps the crane order and ballast. It doesn't replan."
    assert result["crane_order_preserved"] is True
    assert result["quality_note"] == "Legal isn't the same as good."


def test_failed_crane_replay_never_fills_in_projection_from_stopped_prefix():
    base = SimpleNamespace(total_lifts_now=190, future=[])
    plan = SimpleNamespace(moves=[SimpleNamespace(kind="load", container_id=str(i)) for i in range(190)])
    request = SimpleNamespace(baseline=SimpleNamespace(count=base), base_plan=plan,
        replay=SimpleNamespace(legal=False, executed=4, count=base),
        plan=plan, swapped_box=None)
    result = moves._counts(request)
    assert result["extra_lifts"] is None
    assert result["lift_counts"]["projected_later_delta"] is None
    assert "unavailable" in result["lift_counts"]["display"]
