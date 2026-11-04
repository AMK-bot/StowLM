"""D76 sample budgets and first-sample evidence must survive selection honestly."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from eval.sample_reporting import aggregate_sample_reporting, sample_reporting


def checked(*, repaired=False, planner="stowlm", k=8):
    return {"planner": planner, "report_binding_valid": True,
            "report": {"passed": True, "count": {}, "shut_out": [], "violations": [],
                       "parts": {name: {"ran": True, "error": None, "violations": []}
                                 for name in ("moves", "rules", "stability", "strength",
                                              "visibility", "lashing", "constraints")}},
            "result": {"planner": planner, "repaired": repaired,
                       "metrics": {"n_samples": k, "chosen_sample": 4,
                                   "n_checker_feasible": 3}}, "shippable": True}


def evidence(record):
    record["sample_evidence"] = [{"sample_index": 0, "status": "ok", "repaired": False,
                                   "report": deepcopy(record["report"]),
                                   "report_binding_valid": True}]
    return record


def test_selected_best_and_feasible_counts_cannot_establish_pass_at_one():
    row = sample_reporting(checked())
    assert row["k"] == 8
    assert row["selection_label"] == "checker-selected best of 8"
    assert row["pass_at_1"] is None
    assert row["pass_at_1_status"] == "unknown: first-sample checker evidence missing"


def test_adapter_configuration_proves_budget_for_unattempted_allocation():
    row = sample_reporting({"allocation_only": True},
                           planner=SimpleNamespace(name="stowlm", n_samples=8))
    assert row["k"] == 8
    assert row["pass_at_1"] is False
    assert row["pass_at_1_status"] == "not attempted"


def test_legacy_budget_is_unknown_and_does_not_assume_one():
    row = sample_reporting({"result": {"metrics": {}}})
    assert row["k"] is None
    assert row["k_status"] == "unknown"


def test_declared_deterministic_single_plan_planner_proves_one_attempt():
    planner = SimpleNamespace(name="deterministic-solver", deterministic=True)
    row = sample_reporting({}, planner=planner)
    assert row["k"] == 1
    assert row["selection_label"] == "k=1"


def test_bound_first_sample_full_report_establishes_own_pass():
    row = sample_reporting(evidence(checked()))
    assert row["pass_at_1"] is True
    assert row["pass_at_1_status"] == "measured"


@pytest.mark.parametrize("defect", ["unbound", "missing_part", "internal_error", "timeout", "repaired"])
def test_unresolved_or_repaired_first_sample_cannot_establish_own_pass(defect):
    record = evidence(checked())
    sample = record["sample_evidence"][0]
    if defect == "unbound":
        sample["report_binding_valid"] = False
    elif defect == "missing_part":
        del sample["report"]["parts"]["lashing"]
    elif defect == "internal_error":
        sample["report"]["parts"]["stability"]["error"] = "numerical failure"
    elif defect == "timeout":
        sample["report"]["parts"]["stability"]["metrics"] = {"time_budget_exceeded": 1.0}
    else:
        sample["repaired"] = True
    assert sample_reporting(record)["pass_at_1"] is None


def test_rejected_first_sample_and_full_allocation_denominator():
    passing = evidence(checked())
    failing = evidence(checked())
    failing["sample_evidence"][0]["report"]["passed"] = False
    failing["sample_evidence"][0]["report"]["violations"] = [
        {"rule_id": "rules.dg", "severity": "hard"}]
    missing = {"allocation_only": True, "feasibility_status": "unknown",
               "sample_reporting": sample_reporting({}, parameters={"n_samples": 8})}
    rejected = {"allocation_only": True, "feasibility_status": "generation-rejected",
                "sample_reporting": sample_reporting({}, parameters={"n_samples": 8})}
    result = aggregate_sample_reporting([passing, failing, missing, rejected])
    assert result["k"] == 8
    assert result["n_allocated"] == 4
    assert result["pass_at_1_count"] == 1
    assert result["pass_at_1_rate"] == .25
    assert result["pass_at_1_unknown_count"] == 0
    assert result["pass_at_1_ci95"][0] < .25 < result["pass_at_1_ci95"][1]


def test_unknown_first_samples_keep_measurement_unknown_with_visible_bounds():
    result = aggregate_sample_reporting([evidence(checked()), checked()])
    assert result["pass_at_1_count"] == 1
    assert result["pass_at_1_unknown_count"] == 1
    assert result["pass_at_1_rate"] is None
    assert result["pass_at_1_ci95"] is None
    assert result["pass_at_1_rate_bounds"] == [.5, 1.]


def test_one_sample_bound_own_report_can_measure_pass_at_one():
    record = checked(k=1)
    assert sample_reporting(record)["pass_at_1"] is True
    record["report_binding_valid"] = False
    assert sample_reporting(record)["pass_at_1"] is None


def test_spark_always_reports_one_and_excludes_repair_in_all_populations():
    own = checked(planner="muse-spark-zero-shot", k=1)
    repaired = checked(planner="muse-spark-zero-shot", k=1, repaired=True)
    for population in ("own", "shipped"):
        summary = aggregate_sample_reporting([own, repaired], population=population)
        assert summary["k"] == 1
        assert summary["best_of_k_count"] == 1
        assert summary["best_of_k_rate"] == .5
        assert summary["population"] == "own"
    assert sample_reporting(own)["selection_label"] == "own plans; one sample"


def test_original_spark_method_identity_survives_repaired_result_planner():
    record = checked(planner="repair-solver", k=1, repaired=True)
    del record["planner"]
    record["method"] = "muse-spark-zero-shot"
    assert sample_reporting(record)["spark_own_only"] is True


def test_explicit_sample_zero_evidence_survives_json_serialization():
    import json
    record = json.loads(json.dumps(evidence(checked())))
    assert sample_reporting(record)["pass_at_1"] is True


def test_duplicate_sample_zero_evidence_refuses_ambiguous_measurement():
    record = evidence(checked())
    record["sample_evidence"].append(deepcopy(record["sample_evidence"][0]))
    assert sample_reporting(record)["pass_at_1"] is None


def test_actual_stowlm_pass_at_one_producer_schema_is_consumed():
    record = checked()
    record["result"]["metrics"]["pass_at_1"] = {
        "sample": 0, "complete": True, "checker_passed": True,
        "checked_in_selection": True, "objective": 3}
    assert sample_reporting(record)["pass_at_1"] is True


def test_actual_producer_metrics_survive_repair_nesting():
    record = checked(repaired=True)
    record["result"]["metrics"] = {"repair_original_metrics": {
        "n_samples": 8, "pass_at_1": {"sample": 0, "complete": False,
            "checker_passed": False, "reason": "sample 0 dead_end"}}}
    row = sample_reporting(record)
    assert row["k"] == 8
    assert row["pass_at_1"] is False


@pytest.mark.parametrize("reason", ["not checked: per-instance time limit", "LinAlgError: singular matrix",
                                   "full checker rejected candidate or report binding"])
def test_legacy_producer_failure_without_evaluated_verdict_stays_unknown(reason):
    record = checked()
    record["result"]["metrics"]["pass_at_1"] = {
        "sample": 0, "complete": True, "checker_passed": False,
        "checked_in_selection": True, "reason": reason}
    assert sample_reporting(record)["pass_at_1"] is None
