"""D68 measures the planner separately from solver fallback and instance facts."""
import pytest

from eval import harness_metrics as metrics


def row(key="i", *, passed=True, repaired=False, objective=10, **extra):
    return {"spec_key": key, "instance_id": key, "input_sha256": key, "rung": "small",
            "test_family": "in_distribution", "result": {"repaired": repaired, "metrics": {}},
            "report": {"passed": passed, "parts": {}, "violations": [],
                       "count": {"weighted_nonproductive": objective}}, **extra}


def test_own_plan_headline_excludes_fallback_but_shipped_population_keeps_it():
    own, fallback = row("own"), row("fallback", repaired=True, objective=100)
    summary = metrics.aggregate_results([own, fallback])
    assert summary["pass_rate"] == .5
    assert summary["n_own_passed"] == 1
    assert summary["n_shipped_passed"] == 2
    assert summary["shipped_pass_rate"] == 1
    assert summary["own_pass_rate_ci95"] == metrics.wilson_interval(1, 2)
    assert summary["shipped_pass_rate_ci95"] == metrics.wilson_interval(2, 2)
    assert summary["moves"]["weighted_nonproductive"]["mean"] == 10
    shipped = metrics.aggregate_results([own, fallback], population="shipped")
    assert shipped["pass_rate"] == 1
    assert shipped["moves"]["weighted_nonproductive"]["mean"] == 55
    text = metrics.render_row(summary)
    assert "own 50.0%" in text and "shipped 100.0%" in text


def test_paired_defaults_own_and_small_samples_have_exact_sign_test():
    first = [row("a", objective=1), row("b", objective=3), row("c", repaired=True, objective=2)]
    second = [row("a", objective=2), row("b", objective=3), row("c", objective=4)]
    comparison = metrics.paired_comparison(first, second, n_bootstrap=10)
    assert comparison["population"] == "own"
    assert comparison["n_pairs"] == 2
    assert comparison["wins"] == 1 and comparison["ties"] == 1 and comparison["losses"] == 0
    assert comparison["sign_test"]["p_value"] == 1
    assert comparison["sign_test"]["n_non_ties"] == 1
    assert metrics.paired_comparison(first, second, population="shipped", n_bootstrap=10)["n_pairs"] == 3
    assert "wins/ties/losses=1/1/0" in metrics.render_paired_comparison(
        comparison, first_method="A", second_method="B")


def test_mask_availability_denominator_and_pipeline_breakdown_are_explicit():
    dead = row("dead", passed=False, result={"status": "dead_end", "metrics": {"n_dead_end": 1}})
    unavailable = [row(str(i), passed=False, mask_global_screen="unavailable: stale") for i in range(3)]
    rejected = row("rejected", passed=False, allocation_only=True, feasibility_status="generation-rejected")
    pending = row("pending", passed=False, allocation_only=True, feasibility_status="unknown")
    summary = metrics.aggregate_results([dead, *unavailable, rejected, pending])
    assert summary["dead_end_rate"] == 1
    assert summary["mask_dead_end_rate"] == 1
    assert summary["mask_available_instance_count"] == 1
    assert summary["n_generation_rejected"] == 1 and summary["n_not_attempted"] == 1
    assert summary["n_method_failed"] == 4 and summary["n_attempted"] == 4
    assert summary["attempted_pass_rate"] == 0
    assert summary["checker_outcome_counts"]["failed_check"] == 4
    assert "infeasible" not in summary["checker_outcome_counts"]


def test_gap_to_lower_bound_labels_both_denominators_and_incomparability():
    summary = metrics.aggregate_results([row("a", lower_bound=0), row("b", lower_bound=2)])
    text = metrics.render_row(summary)
    assert "n_relative=1; n_absolute=2" in text
    assert metrics.MOVE_MEAN_POPULATION in text
    assert metrics.MOVE_MEAN_POPULATION in metrics.render_table_header().split("Gap to lower bound")[1]


def test_instance_ledger_shares_witness_and_best_plan_binding_between_methods():
    from eval.instance_facts import apply_instance_facts, build_instance_facts
    failed = row(passed=False)
    good = row(objective=2)
    good["report"]["parts"] = {"condition:mid:stability": {
        "ran": True, "metrics": {"condition.phase: mid_voyage": 1, "gm_m": .21,
                                    "weather_ratio": 1.08},
        "margins": {"gm_m": .06, "weather_ratio": .08}}}
    loose = row(objective=8)
    loose["report"]["parts"] = {"condition:arr:stability": {
        "ran": True, "metrics": {"condition.phase: arrival": 1, "weather_ratio": 2},
        "margins": {"weather_ratio": 1}}}
    facts = build_instance_facts({"stowlm": [failed], "solver": [good], "gold": [loose]})
    assert facts["i"]["feasibility_status"] == "feasible-witnessed"
    assert facts["i"]["best_passing_plan"]["method"] == "solver"
    assert facts["i"]["stability"]["tightest_ratio"] == pytest.approx(1.08)
    assert facts["i"]["stability"]["binding"] is True
    left = apply_instance_facts([failed], facts)
    right = apply_instance_facts([good], facts)
    assert left[0]["feasibility_status"] == right[0]["feasibility_status"] == "feasible-witnessed"
    assert metrics.aggregate_results(left)["n_known_feasible_failed"] == 1
    assert metrics.aggregate_results(left)["stability"]["binding_share"] == 1
    assert metrics.aggregate_results(left)["stability"] == metrics.aggregate_results(right)["stability"]


def test_failed_plans_cannot_create_binding_evidence_and_shared_hashes_must_match():
    from eval.instance_facts import build_instance_facts
    failed = row(passed=False)
    failed["report"]["parts"] = {"condition:a:stability": {"metrics": {"weather_ratio": .1},
                                                             "margins": {"weather_ratio": -.9}}}
    assert build_instance_facts([[failed]])["i"]["stability"]["binding"] is None
    with pytest.raises(ValueError, match="input_sha256"):
        build_instance_facts([[row()], [row(input_sha256="different")]])


def test_all_d57_states_and_code_criteria_compete_for_tightest_ratio():
    from eval.instance_facts import build_instance_facts
    best = row()
    best["report"]["parts"] = {
        "condition:dep:stability": {"metrics": {"condition.phase: departure": 1, "gm_m": .3},
                                    "margins": {"gm_m": .15}},
        "condition:mid:stability": {"metrics": {"condition.phase: mid_voyage": 1,
            "criteria_part_a_2_2_1_to_3_active": 1, "area_0_30_mrad": .0561},
            "margins": {"area_0_30_mrad": .0011}},
        "condition:arr:stability": {"metrics": {"condition.phase: arrival": 1,
            "criteria_part_b_2_3_active": 1, "area_0_30_mrad": .2},
            "margins": {"containership_area_0_30_mrad": .1}}}
    fact = build_instance_facts({"solver": [best]})["i"]["stability"]
    assert fact["tightest_ratio"] == pytest.approx(1.02)
    assert fact["tightest_state"] == "mid_voyage"
    assert fact["n_measured_states"] == 3
    assert fact["binding"] is True


def test_inactive_criteria_and_failed_plan_with_smaller_objective_are_excluded():
    from eval.instance_facts import build_instance_facts
    good, failed = row(objective=3), row(objective=1, passed=False)
    good["report"]["parts"] = {"condition:a:stability": {"metrics": {
        "criteria_part_b_2_3_active": 0, "area_0_30_mrad": .101,
        "gm_m": .4}, "margins": {"containership_area_0_30_mrad": .001, "gm_m": .25}}}
    fact = build_instance_facts({"good": [good], "failed": [failed]})["i"]
    assert fact["best_passing_plan"]["method"] == "good"
    assert fact["stability"]["tightest_ratio"] == pytest.approx(.4 / .15)
    assert fact["stability"]["binding"] is False


def test_nested_reference_witness_and_original_status_survive_reconsolidation():
    from eval.instance_facts import apply_instance_facts, build_instance_facts
    rejected = row(passed=False, report=None, allocation_only=True, feasibility_status="generation-rejected")
    first = apply_instance_facts([rejected], build_instance_facts({"x": [rejected], "gold": [row()]}))
    assert first[0]["method_feasibility_status"] == "generation-rejected"
    assert build_instance_facts({"x": first})["i"]["feasibility_status"] == "generation-rejected"
    unknown = row(passed=False, reference_solver={"witness": row(), "method": "solver.exact"})
    assert build_instance_facts({"llm": [unknown]})["i"]["feasibility_status"] == "feasible-witnessed"


def test_validated_timeout_incumbent_is_a_physical_witness_without_becoming_shipped():
    from eval.instance_facts import build_instance_facts
    reference = row(shippable=False, reference_validated=True)
    reference["result"]["status"] = "timeout"
    reference["report"]["parts"] = {"condition:arr:stability": {
        "metrics": {"weather_ratio": 1.05}, "margins": {"weather_ratio": .05}}}
    failed = row(passed=False, reference_solver={"witness": reference})
    fact = build_instance_facts({"llm": [failed]})["i"]
    assert fact["feasibility_status"] == "feasible-witnessed"
    assert fact["stability"]["binding"] is True
    assert metrics.paired_comparison([row()], [reference], population="shipped", n_bootstrap=2)["n_pairs"] == 0
    reference["reference_validated"] = False
    assert build_instance_facts({"ref": [reference]})["i"]["feasibility_status"] == "unknown"


def test_definitive_violation_takes_cause_precedence_over_timeout():
    timed = row(passed=False)
    timed["report"]["parts"] = {
        "stability": {"ran": False, "metrics": {"time_budget_exceeded": 1},
                      "violations": [{"rule_id": "checker.internal_error", "severity": "hard"}]},
        "rules": {"ran": True, "violations": [{"rule_id": "rules.cargo", "severity": "hard"}]}}
    summary = metrics.aggregate_results([timed])
    assert metrics.checker_outcome(timed) == "failed_check"
    assert summary["n_checker_timeout"] == 0
    assert summary["unevaluated"] == {"checker.internal_error": 1}


def test_exact_sign_test_excludes_ties_and_reports_known_binomial_tail():
    a = [row(str(i), objective=1) for i in range(5)]
    b = [row(str(i), objective=2) for i in range(5)]
    comparison = metrics.paired_comparison(a, b, n_bootstrap=10)
    assert comparison["wins"] == 5
    assert comparison["sign_test"]["p_value"] == .0625


def test_pass_rate_pair_wins_favor_higher_success_not_lower_cost():
    from tests.eval.test_harness_pair_honesty import row
    comparison = metrics.paired_comparison([row('i', 1, passed=True)],
                                           [row('i', 1, passed=False)],
                                           metric='pass_rate', n_bootstrap=2)
    assert comparison['mean_difference'] == 1
    assert comparison['wins'] == 1
    assert comparison['losses'] == 0
