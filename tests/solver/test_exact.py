"""Independent tiny-oracle comparisons and honest exact-search status checks.

The benchmark fixture uses placeholder hulls. Its objective witnesses are
explicitly relaxed, never operational plans; physics acceptance is tested
separately at the planner boundary.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from stowage.actions import get_action_space
from stowage.bounds import lower_bound
from stowage.bounds.bruteforce import brute_force_optimum
from stowage.model import Instance
from stowage.moves import apply_plan, count_moves
from stowage.planner import Planner
from stowage.solver.exact import ExactPlanner, solve_exact
from stowage.rules.catalogue import rule_freeze_v1


@pytest.fixture(scope="module")
def tiny_factory():
    path = Path(__file__).resolve().parents[2] / "experiments/exact-solver/benchmark.py"
    spec = importlib.util.spec_from_file_location("exact_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.tiny_instance


@pytest.mark.parametrize("seed", range(50))
def test_fifty_tiny_optima_match_independent_brute_force(tiny_factory, seed):
    instance = tiny_factory(seed)
    snapshot = instance.model_dump()
    exact = solve_exact(instance, time_limit_s=5., max_nodes=100_000, num_workers=1,
                        check_physics=False, check_static_rules=True)
    oracle = brute_force_optimum(instance, time_limit_s=5., max_nodes=100_000,
                                 check_static_rules=True)
    assert oracle.status == "optimal", (seed, oracle)
    assert exact.status == "OPTIMAL", (seed, exact)
    assert exact.objective == oracle.objective
    assert lower_bound(instance).weighted <= exact.objective
    assert exact.plan is not None
    replay = apply_plan(instance, exact.plan)
    assert replay.legal, replay.violations
    assert set(instance.load_list) <= {p.container_id for p in replay.final_state.ship.placements}
    assert count_moves(instance, exact.plan).weighted_nonproductive == exact.objective
    space = get_action_space("boxslot")
    expanded = space.expand(instance, [space.decode(a) for a in exact.plan.actions])
    assert count_moves(instance, expanded) == count_moves(instance, exact.plan)
    assert expanded.moves == exact.plan.moves
    assert exact.plan.ballast is None
    assert instance.model_dump() == snapshot


def inversion_instance(tiny_factory):
    data = tiny_factory(1).model_dump()
    a, b = data["load_list"]
    data["load_list"] = []
    data["yard"]["stacks"] = []
    data["onboard"]["placements"] = [dict(slot="0010182", container_id=a),
                                        dict(slot="0010184", container_id=b)]
    for box in data["containers"]:
        box["pod"] = "THBKK" if box["id"] == a else "THSGZ"
    return Instance.model_validate(data)


@rule_freeze_v1(enabled=False)
def test_completed_departure_still_searches_for_better_future_moves(tiny_factory):
    # Legacy Stage A explicitly permits voluntary shifts after completion.
    instance = inversion_instance(tiny_factory)
    result = solve_exact(instance, num_workers=1, check_physics=False)
    assert result.status == "OPTIMAL"
    assert result.objective == 1
    assert [move.kind for move in result.plan.moves] == ["restow_shift"]


def test_node_cap_does_not_claim_initial_departure_optimal(tiny_factory):
    result = solve_exact(inversion_instance(tiny_factory), max_nodes=1, num_workers=1, check_physics=False)
    assert result.status in ("FEASIBLE", "TIME_LIMIT")
    assert result.objective == 2
    assert result.gap is None or result.gap > 0


def test_zero_cost_cycles_terminate_with_exact_zero(tiny_factory):
    instance = tiny_factory(2)
    for field in ("relocate", "restow", "hatch_cover", "future_restow", "future_hatch_cover"):
        setattr(instance.objective, field, 0.)
    result = solve_exact(instance, time_limit_s=5., num_workers=1, check_physics=False)
    assert result.status == "OPTIMAL"
    assert result.objective == 0
    assert result.gap == 0


def test_no_yard_destination_is_proved_infeasible(tiny_factory):
    data = tiny_factory(0).model_dump()
    a, b = data["load_list"]
    blocker = next(box["id"] for box in data["containers"] if box["id"] not in (a, b))
    data["load_list"] = [a]
    data["yard"]["stacks"] = [dict(key="A-001-01", length_ft=20, boxes=[a, blocker])]
    data["containers"] = [box for box in data["containers"] if box["id"] != b]
    data["port"]["yard"]["blocks"][0]["n_bays"] = 1
    result = solve_exact(Instance.model_validate(data), num_workers=1, check_physics=False)
    assert result.status == "INFEASIBLE"
    assert result.plan is None and result.objective is None


def test_seeded_plan_and_input_are_deterministic(tiny_factory):
    instance = tiny_factory(6)
    first = solve_exact(instance, seed=17, num_workers=1, check_physics=False)
    second = solve_exact(instance, seed=17, num_workers=1, check_physics=False)
    assert first.status == second.status == "OPTIMAL"
    assert first.plan == second.plan
    assert first.expanded_nodes == second.expanded_nodes


def test_missing_hull_never_returns_operational_success(tiny_factory):
    planner = ExactPlanner(num_workers=1, max_nodes=100)
    assert isinstance(planner, Planner)
    assert planner.name == "solver.exact" and planner.sets_ballast
    result = planner.plan(tiny_factory(3), time_limit_s=.05)
    assert result.status != "ok"


@pytest.mark.parametrize("kwargs", [{"time_limit_s": -1.}, {"max_nodes": 0},
                                   {"num_workers": 0}, {"time_limit_s": float("nan")}])
def test_invalid_search_configuration_rejected(tiny_factory, kwargs):
    with pytest.raises(ValueError):
        solve_exact(tiny_factory(3), **kwargs)


@rule_freeze_v1(enabled=False)
def test_unresolved_better_ballast_candidate_prevents_optimality(tiny_factory, monkeypatch):
    # The unresolved candidate is a legacy Stage A shift, excluded by D69.
    import stowage.solver.exact as exact_module

    instance = inversion_instance(tiny_factory)
    def terminal(inst, plan, ship, **kwargs):
        objective = count_moves(inst, plan).weighted_nonproductive
        # A checker-approved departure costs two. A one-lift stow has an
        # unresolved continuous-ballast search, so it cannot be discarded as
        # infeasible or ignored when certifying the two-lift incumbent.
        return ("unknown" if objective < 2 else "yes", None, plan.provenance)

    monkeypatch.setattr(exact_module, "_validate_terminal", terminal)
    result = solve_exact(instance, time_limit_s=5., max_nodes=100_000,
                         num_workers=1, check_physics=False)
    assert result.status == "FEASIBLE"
    assert result.objective == 2
    assert result.lower_bound <= 1
    assert result.gap > 0


def test_expired_budget_never_fabricates_infeasibility(tiny_factory):
    result = solve_exact(tiny_factory(0), time_limit_s=1e-12,
                         num_workers=1, check_physics=False)
    assert result.status == "TIME_LIMIT"
    assert result.plan is None


def test_under_deck_loading_models_current_and_future_cover_lifts():
    from stowage.model.fixtures import build_instance

    data = build_instance().model_dump()
    box = data["containers"][2]
    box.update(dg=None, oog=None, special_stowage=["under_deck"], pod="THBKK")
    cid = box["id"]
    data.update(containers=[box], load_list=[cid], discharge_list=[])
    data["yard"]["stacks"] = [dict(key="A-001-01", length_ft=20, boxes=[cid])]
    data["port"]["yard"]["blocks"] = [data["port"]["yard"]["blocks"][0]]
    data["port"]["yard"]["blocks"][0].update(n_bays=1, n_rows=1)
    # Explicit test policies isolate cover access from expert cargo ordering.
    data["rule_overrides"] = {
        "expert.e6.heavy_under_deck": {"heavy_threshold": 30000},
        "expert.e1.heavy_low": {"heavy_threshold": 30000},
        "expert.e2.no_heavier_on_lighter": {"tolerance_kg": 10000},
    }
    instance = Instance.model_validate(data)
    result = solve_exact(instance, time_limit_s=5., check_physics=False,
                         check_static_rules=True, num_workers=1)
    oracle = brute_force_optimum(instance, time_limit_s=5., check_static_rules=True)
    assert result.status == "OPTIMAL" and oracle.status == "optimal"
    assert result.objective == oracle.objective
    count = count_moves(instance, result.plan)
    assert count.hatch_cover_lifts == 2
    assert sum(f.hatch_cover_lifts for f in count.future) == 2
    assert count.weighted_nonproductive == result.objective == 4
    space = get_action_space("boxslot")
    expanded = space.expand(instance, space.from_plan(instance, result.plan))
    assert expanded.moves == result.plan.moves
    assert count_moves(instance, expanded) == count
