"""Historical diagnostic comparisons cannot be mistaken for production proofs."""
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2] / "experiments/scalable-solver"


def test_published_tiny_excess_carries_its_own_material_qualifications():
    report = json.loads((ROOT / "quality-final-report.json").read_text())
    cohort = report["cohorts"]["quality-final-v263-tiny"]
    comparison = cohort["prior_run_reference_comparison"]
    assert comparison["n"] == 3
    assert comparison["diagnostic_c2_off"] is True
    assert comparison["independent_proved_optima_this_revision"] == 0
    assert comparison["production_quality_evidence"] is False
    assert "earlier source-transition" in comparison["qualification"]


def test_rerunnable_aggregation_preserves_historical_qualification():
    spec = importlib.util.spec_from_file_location("quality_qualification_test", ROOT / "quality_final_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cohort = dict(diagnostic_c2_off=True, independent_exact_proved_optima=0, cases=[dict(
        prior_same_input_exact_evidence=[dict(after_relative_excess_to_prior_run_optimum=.5)])])
    comparison = module.prior_reference_comparison(cohort)
    assert comparison["n"] == 1 and comparison["mean_relative_excess"] == .5
    assert comparison["production_quality_evidence"] is False
    assert comparison["independent_proved_optima_this_revision"] == 0
