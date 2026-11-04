# Copied from experiments/mvp/frontier/lab/c4/c2-hatch/constructor_target_tests.py; engine integration changes are local.
"""Cold cost targets include constants and never become global proofs."""
from pathlib import Path
import sys

from types import SimpleNamespace
import pytest
from ortools.sat.python import cp_model
from .test_plain import fixture
from .constructor_target import TargetConstruction, construct, integral_objective, set_target


def target_context():
    model = cp_model.CpModel()
    choice = model.new_int_var(0, 1, "choice")
    # Total cost is forced yard 3*2 + cover constant2 + optional cost choice.
    model.minimize(1000 * choice + 2000)
    return dict(model=model, base=SimpleNamespace(relocations=3),
                weights=SimpleNamespace(relocate=2), scale=1000,
                terms=[1000 * choice], constant=2000)


def solve(model):
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    return solver.solve(model)


def test_target_includes_forced_yard_and_cover_constant():
    context = target_context()
    selector = set_target(context, 7)
    assert solve(context["model"]) == cp_model.INFEASIBLE
    set_target(context, 8, selector)
    assert solve(context["model"]) == cp_model.OPTIMAL


def test_restricted_infeasibility_does_not_raise_independent_bound():
    result = TargetConstruction(independent_lower_bound=4, target_infeasible=[4, 5], final_target=6)
    assert result.independent_lower_bound == 4
    assert not hasattr(result, "lower_bound")
    assert not result.full_checker_pass
    assert "not_a_global_bound" in result.family


@pytest.mark.parametrize("budget", [0, -1, float("inf"), float("nan")])
def test_invalid_budget_is_rejected(budget):
    with pytest.raises(ValueError, match="budget_s"):
        construct(fixture(), budget_s=budget)


def test_fractional_weights_cannot_enter_integer_target_lane():
    instance = fixture().model_copy(deep=True)
    instance.objective.relocate = .5
    assert not integral_objective(instance)
    result = construct(instance, budget_s=1)
    assert result.status == "unsupported_nonintegral_or_large_objective"
    assert result.independent_lower_bound is None


@pytest.mark.parametrize("total,expected", [(2., .3), (30., 4.)])
def test_independent_bound_gets_declared_small_budget(monkeypatch, total, expected):
    from . import bound
    seen = []

    def unsupported(instance, *, budget_s, seed, tables):
        seen.append(budget_s)
        return SimpleNamespace(status="counting_only")

    monkeypatch.setattr(bound, "build_model", unsupported)
    result = construct(fixture(), budget_s=total)
    assert seen == [pytest.approx(expected)]
    assert result.status == "unsupported_arrival_or_forecast"
