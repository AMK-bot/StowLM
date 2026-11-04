"""Infrastructure faults and ungenerated allocations retain their true causes."""
import pytest

from eval import harness_metrics as metrics


def row(*, passed=False, parts=None, family="heldout_slice", rung="medium", **updates):
    return {"instance_id": "i", "test_family": family, "rung": rung,
            "report": {"passed": passed, "parts": parts or {}, "violations": []},
            "result": {"status": "ok", "metrics": {}}, **updates}


def test_timeout_is_a_nonpass_and_never_a_physics_infeasibility():
    timeout = row(parts={"stability": {"ran": False, "error": "budget", "metrics": {
        "time_budget_exceeded": 1.0}, "violations": [{"rule_id": "checker.internal_error", "severity": "hard"}]}})
    summary = metrics.aggregate_results([row(passed=True), timeout, row()])
    assert summary["n_checker_timeout"] == 1
    assert summary["n_failed_check"] == 1
    assert summary["pass_rate"] == pytest.approx(1 / 3)
    assert "checker timeout 1" in metrics.render_row(summary)


def test_unevaluated_parts_are_bucketed_by_rule_without_becoming_violations():
    unavailable = row(parts={"condition:arr:strength": {
        "ran": False, "error": "readout failed", "metrics": {"condition.phase: arrival": 1},
        "violations": [{"rule_id": "iacs.s1.loading_instrument", "severity": "hard"}]}})
    unavailable["report"]["violations"] = unavailable["report"]["parts"]["condition:arr:strength"]["violations"]
    summary = metrics.aggregate_results([unavailable])
    assert summary["hard_rule_counts"] == {}
    assert summary["unevaluated"] == {"iacs.s1.loading_instrument": 1}
    arrival = summary["voyage_conditions"]["arrival"]
    assert arrival["n_evaluated"] == 0
    assert arrival["n_failed"] == 0
    assert arrival["failed_parts"] == {}
    assert arrival["unevaluated_parts"] == {"strength": 1}
    assert arrival["unevaluated"] == {"iacs.s1.loading_instrument": 1}
    assert arrival["hard_rule_counts"] == {}
    assert metrics.voyage_conditions(unavailable)["arrival"]["passed"] is False


def test_mask_unavailable_rows_do_not_become_dead_ends():
    unavailable = row(result={"status": "dead_end", "metrics": {"n_samples": 2, "n_dead_end": 2,
                      "diagnostics": {"global_screen": "unavailable: stale epoch"}}})
    dead = row(result={"status": "dead_end", "metrics": {"n_samples": 2, "n_dead_end": 2,
                     "diagnostics": {"global_screen": "on"}}})
    summary = metrics.aggregate_results([unavailable, dead])
    assert summary["mask_unavailable_count"] == 1
    assert summary["mask_unavailable_rate"] == .5
    assert summary["dead_end_rate"] == 1
    assert summary["mask_dead_end_count"] == 2
    assert summary["mask_dead_end_sample_rate"] == 1


def test_every_allocated_spec_has_a_status_and_family_rung_wilson_interval():
    rows = [row(passed=True, feasibility_status="feasible-witnessed") for _ in range(12)]
    rows += [row(report=None, feasibility_status=status) for status in (
        "proven-infeasible", "unknown", "generation-rejected")]
    summary = metrics.aggregate_results(rows)
    assert summary["n_allocated"] == 15
    assert summary["feasibility_status_counts"] == {
        "feasible-witnessed": 12, "proven-infeasible": 1, "unknown": 1, "generation-rejected": 1}
    assert summary["pass_rate"] == .8
    grouped = summary["by_family_rung"]["heldout_slice"]["medium"]
    assert grouped["n_allocated"] == 15
    assert grouped["pass_rate_ci95"] == pytest.approx([.5481455128483064, .9295245065301845])
    assert summary["by_family"]["heldout_slice"]["pass_rate"] == .8
    perfect = metrics.aggregate_results([row(passed=True) for _ in range(15)])
    assert perfect["pass_rate_ci95"][0] == pytest.approx(.7961166989641515)
    assert metrics.aggregate_results([])["pass_rate_ci95"] is None


def test_allocation_placeholders_change_pass_denominator_without_becoming_attempts():
    generated = row(passed=True, fallback_needed=False)
    rejected = row(report=None, allocation_only=True, feasibility_status="generation-rejected")
    summary = metrics.aggregate_results([generated, rejected])
    assert summary["n_instances"] == 1
    assert summary["n_allocated"] == 2
    assert summary["fallback_rate"] == 0
    assert summary["pass_rate"] == .5
    assert summary["by_family"]["heldout_slice"]["n_allocated"] == 2
    assert summary["checker_outcome_counts"] == {"generation_rejected": 1, "passed": 1}


def test_final_arrival_measures_drive_stability_binding_without_inventing_missing_data():
    # The Code's six B 2.3 minima are coefficient / form factor C.
    coefficients = {"area_0_30_mrad": .009, "area_0_40_mrad": .016,
                    "area_30_40_mrad": .006, "gz_at_or_above_30_m": .033,
                    "gz_max_m": .042, "area_0_flood_mrad": .029}
    def measured(ratio):
        return row(parts={"condition:arr:stability": {"ran": True, "metrics": {
            "condition.phase: arrival": 1, "containership_form_factor": .1,
            **{key: coefficient / .1 * ratio for key, coefficient in coefficients.items()}},
            "margins": {"gm_m": .25, **{f"containership_{key}": coefficient / .1 * (ratio - 1)
                        for key, coefficient in coefficients.items()}}}})
    one, two = measured(1.1), measured(1.3)
    assert metrics.arrival_stability(one) == {"b2_3_ratio": pytest.approx(1.1), "gm_margin_m": .25}
    summary = metrics.aggregate_results([one, two, row()])
    binding = summary["by_family"]["heldout_slice"]["stability"]
    assert binding["n_measured"] == 0  # Neither measured plan passed the full checker.
    assert binding["binding_share"] is None
    assert "<= 1.10" in binding["criterion"]
    assert metrics.aggregate_results([row()])["stability"]["binding_share"] is None
    incomplete = measured(1.1)
    del incomplete['report']['parts']['condition:arr:stability']['margins']['containership_area_0_30_mrad']
    assert metrics.arrival_stability(incomplete)['b2_3_ratio'] is None
    inactive = measured(1.1)
    inactive['report']['parts']['condition:arr:stability']['metrics']['criteria_part_b_2_3_active'] = 0
    assert metrics.arrival_stability(inactive)['b2_3_ratio'] is None
    one["report_binding_valid"] = False
    assert metrics.arrival_stability(one) == {"b2_3_ratio": None, "gm_margin_m": None}
