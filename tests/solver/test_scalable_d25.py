"""D25/public-v2 integration on synthetic, hull-free fixture-builder cargo.

These explicit local-only oracles test search plumbing, not gold admission:
completion LP and ballast/full-checker gates are intentionally outside the fixture.
The checker owns weight legality; tests never implement a second physical model.
"""
from __future__ import annotations

import pytest

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.checker.rules import placement_violations
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import Placement
from stowage.moves import apply_plan
from stowage.solver.scalable import ScalableConfig, ScalableSolver, complete, improve
from stowage.solver.scalable_oracle import build_engine


@pytest.fixture
def legacy_rules():
    """These assertions specify the frozen legacy class/E6/clearance contract."""
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=False):
        yield


@pytest.fixture
def local_case():
    instance = tiny_instance(91, n_load=2, n_other=0, p40=0)
    instance.containers = [box.model_copy(update={"gross_mass_kg": mass, "pod": "THBKK"})
                           for box, mass in zip(instance.containers, (19000, 11000))]
    config = ScalableConfig(max_neighborhoods=0, feasibility_config=FeasibilityConfig(
        extra={"completion_screens": 0, "global_screen": 0}))
    prefix = [Place(instance.load_list[0], "0010102"),
              Place(instance.load_list[1], "0010104")]
    return instance, config, prefix


def test_d25_same_class_heavier_upper_prefix_is_retained(legacy_rules, local_case):
    instance, config, prefix = local_case
    instance.containers[0].gross_mass_kg = 11000
    instance.containers[1].gross_mass_kg = 19000
    # Ask the actual 2.4 placement checker, rather than rederive D25 thresholds.
    ship = instance.onboard.model_copy(update={"placements": [Placement(
        container_id=prefix[0].box, slot=prefix[0].slot)]})
    violations = placement_violations(instance, ship, instance.containers[1], prefix[1].slot)
    assert not {"expert.e1.heavy_bottom", "expert.e2.weight_order"} & {
        v.rule_id for v in violations}
    result = complete(instance, prefix, config=config, time_limit_s=3)
    assert result.status == "ok", result.message
    assert result.metrics["retained_actions"] == 2
    assert result.metrics["discarded_actions"] == 0
    assert get_action_space().from_plan(instance, result.plan) == prefix
    assert apply_plan(instance, result.plan).legal
    assert not result.metrics["checker_passed"]
    assert result.metrics["requires_finalize_and_check"]
    assert "diagnostic" in result.metrics["oracle_mode"]


class PublicOnlyEngine:
    """Proxy rejects private implementation inspection; delegates only public API."""

    def __init__(self, backend):
        object.__setattr__(self, "backend", backend)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AssertionError(f"Search inspected private oracle attribute: {name}")
        if name == "copy":
            return lambda: PublicOnlyEngine(self.backend.copy())
        return getattr(self.backend, name)


def test_public_only_oracle_supports_plan_complete_and_improve(local_case, monkeypatch):
    import stowage.solver.scalable as search
    instance, config, prefix = local_case
    calls = []

    def factory(instance, space, *, config):
        calls.append(config)
        return PublicOnlyEngine(make_feasibility_engine(instance, space, config=config))

    monkeypatch.setattr(search, "make_feasibility_engine", factory)
    constructed = ScalableSolver(config).plan(instance, time_limit_s=3)
    assert constructed.status == "ok", constructed.message
    repaired = complete(instance, prefix[:1], config=config, time_limit_s=3)
    assert repaired.status == "ok", repaired.message
    assert get_action_space().from_plan(instance, repaired.plan)[0] == prefix[0]
    improved = improve(instance, repaired.plan, config=config, time_limit_s=3)
    assert improved.status == "ok", improved.message
    assert improved.metrics["retained_actions"] == 2
    assert apply_plan(instance, improved.plan).count.weighted_nonproductive <= apply_plan(
        instance, repaired.plan).count.weighted_nonproductive
    assert calls and all(c is config.feasibility_config for c in calls)


def test_public_adapter_copy_and_pop_preserve_independence(local_case):
    instance, config, prefix = local_case
    oracle = build_engine(instance, get_action_space(), config.feasibility_config)
    assert oracle.dead_end() is None
    assert prefix[0] in oracle.legal_actions()
    oracle.push(prefix[0])
    cloned = oracle.copy()
    cloned.push(prefix[1])
    assert cloned.is_complete()
    assert oracle.depth == 1 and not oracle.is_complete()
    assert cloned.pop() == prefix[1]
    assert cloned.actions() == oracle.actions() == prefix[:1]
    assert prefix[1] in cloned.legal_actions()
    assert cloned.mode == oracle.mode


def test_adapter_forwards_public_forces_restow(local_case):
    instance, config, prefix = local_case
    backend = make_feasibility_engine(instance, get_action_space(), config=config.feasibility_config)
    seen = []

    class FlaggedEngine(PublicOnlyEngine):
        def forces_restow(self, action):
            seen.append(action)
            return action == prefix[0]

    flagged = FlaggedEngine(backend)
    oracle = build_engine(instance, get_action_space(), config.feasibility_config,
                          factory=lambda *args, **kwargs: flagged)
    assert oracle.forces_restow(prefix[0]) is True
    assert oracle.forces_restow(prefix[1]) is False
    assert seen == prefix


def test_search_ranking_uses_public_forced_restow_signal(local_case):
    from stowage.solver.scalable import _Node, _action_rank, _pod_map
    instance, _, prefix = local_case
    seen = []

    class RestowSignal:
        enabled = False

        def forces_restow(self, action):
            seen.append(action)
            return self.enabled

    engine = RestowSignal()
    node = _Node(engine, Expander(instance))
    regular = _action_rank(instance, node, prefix[0], _pod_map(instance))
    engine.enabled = True
    forced = _action_rank(instance, node, prefix[0], _pod_map(instance))
    assert forced > regular
    assert seen == [prefix[0], prefix[0]]


def test_same_class_mass_change_does_not_change_search_priority(legacy_rules, local_case):
    from stowage.solver.scalable import _Node, _action_rank, _pod_map
    instance, _, prefix = local_case
    rank_before = _action_rank(instance, _Node(object(), Expander(instance)),
                               prefix[0], _pod_map(instance))
    instance.containers[0].gross_mass_kg = 11000
    rank_after = _action_rank(instance, _Node(object(), Expander(instance)),
                              prefix[0], _pod_map(instance))
    assert rank_after == rank_before
