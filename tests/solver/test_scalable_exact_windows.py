"""Exact-window admission plumbing; mocked checker results are not gold evidence."""
from types import SimpleNamespace

import pytest

from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig
from stowage.moves import apply_plan
from stowage.solver import scalable
from stowage.solver.scalable_exact import ExactWindow, exact_windows, repair_exact_window
from stowage.solver.scalable_oracle import build_engine


def plans():
    instance = tiny_instance(719, n_load=2, n_other=0, p40=0)
    instance.containers = [box.model_copy(update={"gross_mass_kg": 14000, "pod": "THBKK"})
                           for box in instance.containers]
    a, b = instance.load_list
    space = get_action_space()
    expensive = space.expand(instance, [Place(a, "0010102"), Place(b, "0030102")])
    cheap = space.expand(instance, [Place(a, "0010182"), Place(b, "0030182")])
    assert apply_plan(instance, expensive).legal and apply_plan(instance, cheap).legal
    assert apply_plan(instance, cheap).count.weighted_nonproductive < apply_plan(instance, expensive).count.weighted_nonproductive
    return instance, expensive, cheap


def engine_factory(instance):
    return lambda: build_engine(instance, get_action_space(),
                                FeasibilityConfig(extra={"global_screen": 0}))


def fake_exact(monkeypatch, candidate, *, status="OPTIMAL"):
    import stowage.solver.exact
    calls = []
    def solve(instance, **kwargs):
        calls.append((instance, kwargs))
        cost = apply_plan(instance, candidate).count.weighted_nonproductive if candidate is not None else None
        return SimpleNamespace(status=status, objective=cost, plan=candidate,
                               lower_bound=cost or 0, expanded_nodes=1)
    monkeypatch.setattr(stowage.solver.exact, "solve_exact", solve)
    return calls


def test_whole_plan_window_releases_first_decision_and_caps_other_suffixes():
    instance, plan, _ = plans()
    actions = get_action_space().from_plan(instance, plan)
    windows = exact_windows(instance, actions, max_boxes=1, whole_plan_boxes=2)
    assert windows[0] == ExactWindow("whole_plan", 0, 2)
    assert all(w.cut > 0 and w.boxes <= 1 for w in windows[1:])
    bounded = exact_windows(instance, actions, max_boxes=1, whole_plan_boxes=0)
    assert bounded and all(w.boxes <= 1 for w in bounded)


@pytest.mark.parametrize("checker_passes", [False, True])
def test_acceptance_requires_cheaper_original_replay_and_original_checker(monkeypatch, checker_passes):
    import stowage.checker
    instance, expensive, cheap = plans()
    calls = fake_exact(monkeypatch, cheap)
    checked = []
    def check(original, candidate):
        checked.append(original)
        return SimpleNamespace(passed=checker_passes)
    monkeypatch.setattr(stowage.checker, "check", check)
    result, info = repair_exact_window(instance, expensive, ExactWindow("whole_plan", 0, 2),
        time_limit_s=1, max_nodes=27, seed=9, feasibility_config=None,
        engine_factory=engine_factory(instance))
    assert bool(result) == checker_passes
    assert info["accepted"] == checker_passes
    assert checked == [instance] and calls[0][0] is instance
    assert calls[0][1]["num_workers"] == 1 and calls[0][1]["max_nodes"] == 27
    assert 0 < calls[0][1]["time_limit_s"] <= 1


def test_equal_cost_candidate_is_rejected_without_original_checker(monkeypatch):
    import stowage.checker
    instance, expensive, _ = plans()
    fake_exact(monkeypatch, expensive)
    monkeypatch.setattr(stowage.checker, "check", lambda *a: pytest.fail("no strict improvement"))
    result, info = repair_exact_window(instance, expensive, ExactWindow("whole_plan", 0, 2),
        time_limit_s=1, max_nodes=20, seed=0, feasibility_config=None,
        engine_factory=engine_factory(instance))
    assert result is None and info["rejection"] == "not strictly better"


def test_timeout_without_exact_incumbent_keeps_the_existing_plan(monkeypatch):
    instance, expensive, _ = plans()
    fake_exact(monkeypatch, None, status="TIME_LIMIT")
    result, info = repair_exact_window(instance, expensive, ExactWindow("whole_plan", 0, 2),
        time_limit_s=1, max_nodes=20, seed=0, feasibility_config=None,
        engine_factory=engine_factory(instance))
    assert result is None and info["status"] == "TIME_LIMIT" and not info["accepted"]
    assert apply_plan(instance, expensive).legal


def test_exact_window_configuration_rejects_unbounded_or_negative_limits():
    for kwargs in ({"exact_window_time_limit_s": float("inf")},
                   {"exact_whole_plan_time_limit_s": float("inf")},
                   {"exact_whole_plan_time_limit_s": -1},
                   {"exact_window_max_nodes": 0}, {"exact_window_max_boxes": -1},
                   {"exact_whole_plan_boxes": -1}):
        with pytest.raises(ValueError):
            scalable.ScalableConfig(**kwargs)


def test_whole_plan_gets_contiguous_budget_when_ordinary_windows_are_disabled(monkeypatch):
    import stowage.checker
    instance, expensive, cheap = plans()
    calls = fake_exact(monkeypatch, cheap)
    monkeypatch.setattr(stowage.checker, "check", lambda *a: SimpleNamespace(passed=True))
    result = scalable.improve(instance, expensive, time_limit_s=2,
        # Isolate the exact-window budget; activated search may otherwise
        # keep improving the mocked candidate with later beam neighborhoods.
        config=scalable.ScalableConfig(max_neighborhoods=1, exact_window_time_limit_s=0,
            exact_whole_plan_time_limit_s=1,
            feasibility_config=FeasibilityConfig(extra={"global_screen": 0})))
    assert result.status == "ok", result.message
    assert (apply_plan(instance, result.plan).count.weighted_nonproductive ==
            apply_plan(instance, cheap).count.weighted_nonproductive)
    assert calls and 0.5 < calls[0][1]["time_limit_s"] <= 1
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if rule_freeze_v1_enabled():
        # A move bound does not certify the level-four optimum.
        assert not result.metrics.get("exact_root_bound_reached", False)
        assert result.metrics["heavy_on_deck_count"] == 0
        assert result.metrics["killed_slots"] == 0
    else:
        assert result.metrics["exact_root_bound_reached"]
    assert result.metrics["exact_root_optimality_scope"] == "configured_engine_weighted_lifts"
    assert not result.metrics["exact_root_production_engine"]


def test_verified_incumbent_survives_cheaper_beam_candidates_failing_checker(monkeypatch):
    import stowage.checker
    from stowage.model import BallastCondition

    instance, expensive, _ = plans()
    expensive = expensive.model_copy(update={
        "ballast": BallastCondition(source="planner", tank_masses_kg={})})
    checked = []

    def check(original, candidate):
        assert original is instance
        checked.append(candidate)
        passed = candidate.moves == expensive.moves
        violations = [] if passed else [SimpleNamespace(
            severity="hard", rule_id="test.beam.racking")]
        return SimpleNamespace(passed=passed, provenance=instance.provenance,
                               violations=violations)

    monkeypatch.setattr(stowage.checker, "check", check)
    result = scalable.improve(instance, expensive, time_limit_s=2,
        config=scalable.ScalableConfig(
            exact_window_time_limit_s=0, exact_whole_plan_time_limit_s=0,
            max_neighborhoods=3, destroy_fraction=1,
            feasibility_config=FeasibilityConfig(extra={"global_screen": 0})))

    assert result.status == "ok", result.message
    assert result.metrics["verified_incumbent"]
    assert result.metrics["incumbent_mask_verified"]
    assert result.metrics["beam_checker_rejections"] > 0
    assert result.metrics["beam_checker_rejection_rule_ids"]["test.beam.racking"] > 0
    assert result.plan.moves == expensive.moves
    assert result.plan.ballast == expensive.ballast
    assert len(checked) > 1
    assert all(candidate.ballast == expensive.ballast for candidate in checked)
