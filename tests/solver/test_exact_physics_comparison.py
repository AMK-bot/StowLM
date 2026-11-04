"""Evidence accounting must not promote historical or unchecked rows to proof.

The real-physics campaign is an experiment, not a fixture with physics patched
out. These tests protect its reporting contract; they do not claim 50 optima.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def evidence_module():
    path = Path(__file__).parents[2] / "experiments/exact-solver/fix_epoch_evidence.py"
    spec = spec_from_file_location("exact_epoch_evidence", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_physics_campaign_requires_current_source_checker_and_independent_proof():
    summarize = evidence_module().summarize
    rows = [
        dict(rung="tiny", status="OPTIMAL", source_unchanged=True,
             checker_passed=True, independent_proof=True, objective=2., bound36=1., wall_time_s=.1),
        dict(rung="tiny", status="OPTIMAL", source_unchanged=False,
             checker_passed=True, independent_proof=True, objective=0., bound36=0., wall_time_s=.2),
        dict(rung="tiny", status="OPTIMAL", source_unchanged=True,
             checker_passed=False, independent_proof=True, objective=0., bound36=0., wall_time_s=.3),
        dict(rung="tiny", status="OPTIMAL", source_unchanged=True,
             checker_passed=True, independent_proof=False, objective=0., bound36=0., wall_time_s=.4),
    ]
    tiny = summarize(rows)["tiny"]
    assert tiny["attempts"] == 4
    assert tiny["independent_physical_optima"] == 1
    assert tiny["proof_rate"] == .25
    assert tiny["quarantined_source_changes"] == 1
    assert tiny["physical_optimum_gaps_to_36"] == [1.]
    assert tiny["optimal_plan_checker_pass_rate"] == 2 / 3


def test_physics_campaign_without_checked_optima_has_unknown_pass_rate():
    summarize = evidence_module().summarize
    row = dict(rung="small", status="TIME_LIMIT", source_unchanged=True,
               checker_passed=None, independent_proof=False, objective=None,
               bound36=2., wall_time_s=3.)
    result = summarize([row])["small"]
    assert result["proof_rate"] == 0
    assert result["optimal_plan_checker_pass_rate"] is None
    assert result["physical_optimum_gaps_to_36"] == []


def test_independent_physical_optimum_does_not_require_engine_completion():
    summarize = evidence_module().summarize
    row = dict(rung="tiny", status="TIME_LIMIT", source_unchanged=True,
               checker_passed=True, independent_proof=True, objective=None,
               physical_objective=2., bound36=1., wall_time_s=3.)
    result = summarize([row])["tiny"]
    assert result["engine_optima"] == 0
    assert result["independent_physical_optima"] == 1
    assert result["proof_rate"] == 1
    assert result["physical_optimum_gaps_to_36"] == [1.]
