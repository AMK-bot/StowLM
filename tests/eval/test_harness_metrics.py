"""D11 statistics preserve failed attempts and undefined comparisons."""
import copy

import pytest

from eval.harness_metrics import aggregate_results, paired_comparison, render_row


def record(identity="i", *, passed=True, objective=10, lower=4, reference=8):
    return {"instance_id": identity, "input_sha256": identity, "split": "val", "rung": "tiny",
            "result": {"status": "ok", "wall_time_s": 2, "metrics": {}, "repaired": False},
            "report": {"passed": passed, "shut_out": [] if passed else ["box"],
                       "count": {"loads": 4, "discharges": 1, "relocations": 2,
                                 "restow_lifts": 1, "hatch_cover_lifts": 2,
                                 "future": [{"call_index": 1, "restow_lifts": 4, "hatch_cover_lifts": 1}],
                                 "weighted_nonproductive": objective},
                       "parts": {"stability": {"margins": {"gm_m": .2}, "metrics": {"gm_m": .4}}},
                       "violations": []},
            "lower_bound": {"weighted": lower}, "reference_solver": reference,
            "timing": {"total_s": 3, "plan_s": 2}, "fallback_needed": not passed,
            "provenance": {"versions": {"checker": "1"}, "stubs": {}}}


def test_all_lift_types_and_d11_populations():
    good, bad = record(), record("bad", passed=False, objective=99)
    bad["report"]["violations"] = [{"severity": "hard"}, {"severity": "tolerated"}]
    summary = aggregate_results([good, bad])
    assert summary["n_instances"] == 2
    assert summary["pass_rate"] == .5
    assert summary["fallback_rate"] == .5
    assert summary["shut_outs"]["sum"] == 1
    assert summary["moves"]["total_lifts_now"]["mean"] == 10
    assert summary["moves"]["future_restow_lifts"]["sum"] == 4
    assert summary["moves"]["future_hatch_cover_lifts"]["sum"] == 1
    assert summary["moves"]["weighted_nonproductive"]["mean"] == 10
    assert summary["attempted_moves"]["weighted_nonproductive"]["sum"] == 109
    assert summary["moves"]["nonproductive_lifts_including_projection"]["mean"] == 10
    assert summary["gap_to_lower_bound"]["absolute"]["mean"] == 6
    assert summary["gap_to_solver"]["relative"]["mean"] == .25
    assert summary["margins"]["stability.gm_m"]["mean"] == .2
    assert summary["violation_counts"]["tolerated"] == 1


def test_missing_and_zero_bounds_are_not_fabricated():
    records = [record("zero", lower=0, reference=None), record("missing", lower=None, reference=None)]
    summary = aggregate_results(records)
    assert summary["gap_to_lower_bound"]["absolute"]["n"] == 1
    assert summary["gap_to_lower_bound"]["relative"]["n"] == 0
    assert summary["gap_to_solver"]["absolute"]["mean"] is None
    assert aggregate_results([record(objective=0, lower=0)])["gap_to_lower_bound"]["relative"]["mean"] == 0
    missing = {"instance_id": "x", "report": None, "result": {"status": "error"}, "fallback_needed": True}
    empty = aggregate_results([missing])
    assert empty["pass_rate"] == 0
    assert empty["shut_outs"]["n"] == 0
    assert empty["moves"]["loads"]["mean"] is None
    assert empty["fallback_rate"] == 1
    assert aggregate_results([])["pass_rate"] is None


def test_row_matches_table_and_marks_taint_and_missing_values():
    r = record(reference=None)
    r["result"]["provenance"] = {"stubs": {"checker.check": "test"}, "versions": {"checker": "2"}}
    summary = aggregate_results([r], split="val")
    assert summary["provenance"]["mixed_versions"]
    with pytest.raises(ValueError, match="tiny-run"):
        render_row(summary, method="test", run="experiments/eval-runs/test")
    row = render_row(summary, method="test", tiny_run=True, date="2026-10-27", run="experiments/eval-runs/test")
    assert len(row.split("|")) == 19  # Dedicated internal-error and mask-time-limit columns.
    assert "TINY-RUN; unofficial" in row
    assert "checker.check: test" in row
    assert "restow now/later 1/4" in row
    assert "covers now/later 2/1" in row
    assert "N/A" in row
    assert "100.0%" in row


def test_paired_bootstrap_is_seeded_and_uses_matched_differences():
    first = [record(str(i), objective=i + 2) for i in range(4)]
    second = [record(str(i), objective=i + 5) for i in reversed(range(4))]
    result = paired_comparison(first, second, n_bootstrap=100, seed=8)
    assert result == paired_comparison(first, second, n_bootstrap=100, seed=8)
    assert result["mean_difference"] == -3
    assert result["confidence_interval"] == [-3, -3]
    assert result["n_pairs"] == 4


def test_failed_objectives_excluded_but_pass_difference_remains():
    left = [record("a"), record("b", passed=False)]
    right = [record("a", objective=12), record("b")]
    result = paired_comparison(left, right, n_bootstrap=10)
    assert result["n_pairs"] == 1
    assert result["n_excluded"] == 1
    assert result["pass_rate_difference"] == -.5
    assert result["per_instance"][1]["difference"] is None
    assert paired_comparison(left, right, metric="pass_rate", n_bootstrap=10)["n_pairs"] == 2
    assert paired_comparison([], [], n_bootstrap=10)["confidence_interval"] is None


def test_pairing_rejects_unmatched_or_mismatched_data():
    with pytest.raises(ValueError, match="identical"):
        paired_comparison([record("a")], [record("b")])
    with pytest.raises(ValueError, match="duplicate"):
        paired_comparison([record(), record()], [record()])
    other = copy.deepcopy(record())
    other["input_sha256"] = "changed"
    with pytest.raises(ValueError, match="input_sha256"):
        paired_comparison([record()], [other])
    with pytest.raises(ValueError, match="confidence"):
        paired_comparison([], [], confidence=1)


def test_weighted_breakdowns_use_recorded_weights_only():
    r = record()
    r["instance"] = {"objective": {"relocate": 2, "restow": 3, "hatch_cover": 4,
                                   "future_restow": 5, "future_hatch_cover": 6}}
    weighted = aggregate_results([r])["weighted_nonproductive_by_type"]
    assert {k: v["sum"] for k, v in weighted.items()} == {
        "relocations": 4, "restow_lifts": 3, "hatch_cover_lifts": 8,
        "future_restow_lifts": 20, "future_hatch_cover_lifts": 6}
    assert aggregate_results([record()])["weighted_nonproductive_by_type"]["relocations"]["mean"] is None
    with pytest.raises(ValueError, match="unsupported"):
        paired_comparison([r], [r], metric="typo")


def test_fallback_counts_repair_need_separately_from_final_pass_rate():
    first = record("initial-pass")
    # A final failure alone cannot establish that a repair was needed: the
    # guarded pipeline records that event before repair/check exceptions.
    unknown = record("missing-event", passed=False)
    unknown.pop("fallback_needed")
    repaired = record("repair-pass")
    repaired["result"]["repaired"] = True
    unavailable = record("repair-unavailable", passed=False)
    summary = aggregate_results([first, unknown, repaired, unavailable])
    assert summary["pass_rate"] == .25
    assert summary["shipped_pass_rate"] == .5
    assert summary["fallback_count"] == 2
    assert summary["fallback_unknown_count"] == 1
    assert summary["fallback_rate"] is None
    assert aggregate_results([first, repaired, unavailable])["fallback_rate"] == pytest.approx(2 / 3)
    paired = copy.deepcopy(first)
    paired.update(instance_id=unknown["instance_id"], input_sha256=unknown["input_sha256"])
    assert paired_comparison([unknown], [paired], metric="fallback_rate", n_bootstrap=2)["n_pairs"] == 0


def test_mixed_complete_and_dead_samples_use_all_outcomes_and_distinct_denominators():
    mixed, all_dead, clean = record("mixed"), record("dead", passed=False), record("clean")
    mixed["result"]["metrics"] = {"n_samples": 32, "n_complete": 1, "n_dead_end": 31}
    all_dead["result"].update(status="dead_end", metrics={"n_samples": 32, "n_dead_end": 32,
                                                            "mask_dead_ends": 32})
    clean["result"]["metrics"] = {"n_samples": 4, "n_dead_end": 0}
    summary = aggregate_results([mixed, all_dead, clean])
    assert summary["mask_dead_end_count"] == 63
    assert summary["mask_dead_end_rate"] == pytest.approx(2 / 3)
    assert summary["mask_dead_end_instance_rate"] == pytest.approx(2 / 3)
    assert summary["mask_sample_count"] == 68
    assert summary["mask_dead_end_sample_rate"] == pytest.approx(63 / 68)
    assert summary["dead_end_rate"] == pytest.approx(1 / 3)
    legacy = record()
    legacy["result"]["metrics"] = {"mask_dead_ends": 3}
    assert aggregate_results([legacy])["mask_dead_end_count"] == 3
    assert aggregate_results([legacy])["mask_dead_end_sample_rate"] is None


def condition_part(identity, phase, *, ran=True, error=None, violations=()):
    return {"name": "stability", "ran": ran, "error": error, "violations": list(violations),
            "metrics": {f"condition.id: {identity}": 1, f"condition.phase: {phase}": 1,
                        "condition.environment_id: sea": 1, "condition.density_id: declared": 1,
                        "condition.locode: THLCH": 1, "condition.call_index": int(phase == "arrival"),
                        "condition.water_density_kg_m3": 1025}, "margins": {}}


def test_voyage_condition_identity_pass_failure_and_missing_evidence_are_reported():
    from eval.harness_metrics import voyage_conditions
    r = record(passed=False)
    for phase, identity in (("departure", "dep:0"), ("arrival", "arr:1")):
        for part in ("stability", "strength"):
            r["report"]["parts"][f"condition:{identity}:{part}"] = condition_part(identity, phase)
    arrival = r["report"]["parts"]["condition:arr:1:strength"]
    arrival["violations"] = [{"rule_id": "strength.bm", "severity": "hard"}]
    conditions = voyage_conditions(r)
    assert conditions["departure"]["passed"] is True
    assert conditions["departure"]["call_index"] == 0
    assert conditions["arrival"]["passed"] is False
    assert conditions["arrival"]["condition_id"] == "arr:1"
    assert conditions["arrival"]["water_density_kg_m3"] == 1025
    assert conditions["arrival"]["parts"]["strength"]["hard_rule_ids"] == ["strength.bm"]
    missing = record("legacy")
    summary = aggregate_results([r, missing])
    assert summary["voyage_conditions"]["departure"]["n_passed"] == 1
    assert summary["voyage_conditions"]["departure"]["n_missing"] == 1
    assert summary["voyage_conditions"]["arrival"]["failed_parts"] == {"strength": 1}
    assert summary["voyage_conditions"]["arrival"]["pass_rate"] == 0
    row = render_row(summary, date="2026-10-27")
    assert "departure 50.0% (1/2; missing 1)" in row
    assert "arrival 0.0% (0/2; missing 1)" in row
    assert "arrival.strength=1" in row
    assert "departure 0.0%" not in render_row(aggregate_results([missing]))


def test_voyage_unrun_or_missing_parts_cannot_count_as_condition_pass():
    from eval.harness_metrics import voyage_conditions
    r = record()
    r["report"]["parts"]["condition:a:stability"] = condition_part("a", "arrival", ran=False,
                                                                            error="derivation failed")
    result = voyage_conditions(r)["arrival"]
    assert result["passed"] is False
    assert result["parts"]["strength"]["passed"] is None
    arrival = aggregate_results([r])["voyage_conditions"]["arrival"]
    assert arrival["failed_parts"] == {}
    assert arrival["unevaluated_parts"] == {"stability": 1}


def test_shipping_verdict_excludes_unshippable_objective_without_hiding_checker_pass():
    good, unchecked = record("good", objective=3), record("unchecked", objective=100)
    good["shippable"], unchecked["shippable"] = True, False
    summary = aggregate_results([good, unchecked])
    assert summary["pass_rate"] == 1
    assert summary["shipping_rate"] == .5
    assert summary["n_shippable"] == 1
    assert summary["moves"]["weighted_nonproductive"]["mean"] == 3
    assert summary["gap_to_solver"]["absolute"]["n"] == 1
    assert paired_comparison([unchecked], [unchecked], n_bootstrap=2)["n_pairs"] == 0


def test_solver_reference_method_and_proof_scope_are_visible_in_summary_and_row():
    r = record()
    r["reference_solver"] = {"weighted_nonproductive": 8, "method": "solver.exact",
                             "planner_version": "exact-test", "proof_status": "proven_model_optimum",
                             "optimality_scope": "engine action space"}
    summary = aggregate_results([r])
    assert summary["solver_references"]["methods"] == ["solver.exact"]
    assert summary["solver_references"]["proof_statuses"] == ["proven_model_optimum"]
    row = render_row(summary)
    assert "reference solver.exact" in row
    assert "proof proven_model_optimum" in row
    assert "scope engine action space" in row


def test_detached_endpoint_reports_do_not_supply_voyage_passes():
    from eval.harness_metrics import voyage_conditions
    record = {"report_binding_valid": False, "report": {"parts": {
        "condition:departure:stability": {"metrics": {"condition.phase: departure": 1}},
        "condition:departure:strength": {"metrics": {"condition.phase: departure": 1}}}}}
    assert voyage_conditions(record)["departure"]["passed"] is None
