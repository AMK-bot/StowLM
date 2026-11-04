"""Logical certificate regressions; real checker failures must fail closed.

Mocked search/check reports isolate the inequality proof. They do not claim
physics feasibility for the contract fixture, whose hull data are incomplete.
"""
from types import SimpleNamespace

import pytest

from stowage.model import BallastCondition, CheckReport, MoveCount, MovePlan
from stowage.model.fixtures import build_instance
from stowage.solver.exact_oracle import checker_brute_force_certificate


def inputs():
    instance = build_instance()
    plan = MovePlan(id="oracle-witness", instance_id=instance.id,
                    call_index=instance.call_index,
                    ballast=BallastCondition(source="planner", tank_masses_kg={}))
    return instance, plan


def install(monkeypatch, *, objective=1., witness_cost=1, counting_cost=0,
            oracle_status="optimal", passed=True, plan=None):
    import stowage.solver.exact_oracle as module

    count = MoveCount(relocations=witness_cost)
    oracle = SimpleNamespace(status=oracle_status, objective=objective, plan=plan,
                             expanded_nodes=17, wall_time_s=.001)
    bound = SimpleNamespace(relocations=counting_cost, restow_lifts=0, hatch_cover_lifts=0,
                            future_restow_lifts=0, future_hatch_cover_lifts=0)
    monkeypatch.setattr(module, "brute_force_optimum", lambda *a, **kw: oracle)
    monkeypatch.setattr(module, "lower_bound", lambda *a, **kw: bound)
    monkeypatch.setattr(module, "check", lambda *a: CheckReport(passed=passed, count=count))


@pytest.mark.parametrize("oracle_objective", [1., 1])
def test_independent_relaxed_bound_and_checker_witness_close_certificate(monkeypatch, oracle_objective):
    instance, plan = inputs()
    snapshot = plan.model_dump()
    install(monkeypatch, objective=oracle_objective)
    result = checker_brute_force_certificate(instance, incumbent=plan)
    assert result.status == "OPTIMAL"
    assert result.checker_passed and result.report.passed
    assert result.brute_force_agreement
    assert result.objective == result.lower_bound == 1
    assert result.certificate_source == "independent_brute_force_and_full_checker"
    assert result.plan is not None
    assert plan.model_dump() == snapshot


def test_cheaper_relaxed_solution_does_not_certify_checked_incumbent(monkeypatch):
    instance, plan = inputs()
    install(monkeypatch, objective=0., witness_cost=1)
    result = checker_brute_force_certificate(instance, incumbent=plan)
    assert result.status == "FEASIBLE"
    assert result.checker_passed and not result.brute_force_agreement
    assert result.objective == 1 and result.lower_bound == 0
    assert result.certificate_source is None


def test_failed_arrival_ballast_does_not_prove_other_ballast_infeasible(monkeypatch):
    instance, plan = inputs()
    plan.ballast = None
    install(monkeypatch, passed=False, plan=plan)
    result = checker_brute_force_certificate(instance)
    assert result.status == "CHECK_FAILED"
    assert not result.checker_passed and not result.brute_force_agreement
    assert result.plan is None and result.objective is None
    assert result.report is not None and not result.report.passed


def test_fractional_weights_never_claim_float_bruteforce_equality(monkeypatch):
    instance, plan = inputs()
    instance.objective.relocate = .1
    install(monkeypatch, objective=.1, witness_cost=1, counting_cost=0)
    result = checker_brute_force_certificate(instance, incumbent=plan)
    assert result.status == "FEASIBLE"
    assert result.objective == .1
    assert not result.brute_force_agreement
    assert result.certificate_source is None


def test_fractional_counting_equality_is_distinct_from_bruteforce_agreement(monkeypatch):
    instance, plan = inputs()
    instance.objective.relocate = .1
    install(monkeypatch, objective=.1, witness_cost=1, counting_cost=1)
    result = checker_brute_force_certificate(instance, incumbent=plan)
    assert result.status == "OPTIMAL"
    assert result.certificate_source == "counting_bound_and_full_checker"
    assert not result.brute_force_agreement


@pytest.mark.parametrize("mismatch", ["instance", "call"])
def test_witness_must_match_the_input_instance_and_call(monkeypatch, mismatch):
    instance, plan = inputs()
    plan = plan.model_copy(update={"instance_id": "other"} if mismatch == "instance"
                           else {"call_index": 1})
    install(monkeypatch)
    with pytest.raises(ValueError, match="match instance and call"):
        checker_brute_force_certificate(instance, incumbent=plan)


def test_real_checker_rejects_missing_hull_without_false_infeasibility(monkeypatch):
    import stowage.solver.exact_oracle as module

    instance, plan = inputs()
    monkeypatch.setattr(module, "brute_force_optimum", lambda *a, **kw: SimpleNamespace(
        status="limit", objective=None, plan=plan, expanded_nodes=1, wall_time_s=.001))
    result = checker_brute_force_certificate(instance)
    assert result.status == "CHECK_FAILED"
    assert result.report is not None and not result.report.passed
    assert not result.checker_passed
    assert any(v.rule_id == "checker.missing_vessel_data" for v in result.report.violations)
