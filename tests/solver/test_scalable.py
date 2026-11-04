"""Search integration tests; the physical test oracle is NOT a gold-plan checker."""
from __future__ import annotations

import pytest

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.samples import legal_slots, tiny_instance
from stowage.actions.space import Place
from stowage.feasibility import IllegalActionError
from stowage.moves import apply_move, apply_plan, initial_state
from stowage.rules.catalogue import rule_freeze_v1_enabled


class PhysicalOracle:
    """Small test double: isolates search plumbing, never certifies full physics."""

    def __init__(self, instance, space, **kwargs):
        self.instance, self.space = instance, space
        self.ex = Expander(instance)
        self.state = initial_state(instance)
        self.history = []
        self._validated = {}

    def is_legal(self, action):
        if not isinstance(action, Place) or action.box not in self.instance.load_list:
            return False
        if self.ex.location(action.box)[0] not in ("yard", "quay"):
            return False
        if action in self._validated:
            return self._validated[action] is not None
        try:
            trial = self.ex.copy()
            state = self.state
            for move in trial.step(action):
                state = apply_move(self.instance, state, move)
            self._validated[action] = (trial, state)
            return True
        except ValueError:
            self._validated[action] = None
            return False

    def push(self, action):
        if not self.is_legal(action):
            raise IllegalActionError("test oracle rejected action")
        # is_legal already independently replayed every physical lift for
        # this exact state/action. Reuse that proof without a second expansion.
        if action in self._validated:
            trial, self.state = self._validated[action]
            self.ex = trial.copy()
        else:
            # Structural-action doubles may override membership directly.
            for move in self.ex.step(action):
                self.state = apply_move(self.instance, self.state, move)
        self.history.append(action)
        self._validated = {}

    def legal_actions(self):
        return [Place(cid, slot) for cid in self.instance.load_list
                if self.ex.location(cid)[0] == "yard"
                for slot in legal_slots(self.instance, self.ex, cid)
                if self.is_legal(Place(cid, slot))]

    def is_complete(self):
        return all(self.ex.location(cid)[0] == "ship" for cid in self.instance.load_list)

    def dead_end(self):
        return None

    @property
    def depth(self):
        return len(self.history)

    def actions(self):
        return list(self.history)

    def to_plan(self):
        return self.space.expand(self.instance, self.history)

    def copy(self):
        other = PhysicalOracle(self.instance, self.space)
        other.ex, other.state, other.history = self.ex.copy(), self.state, list(self.history)
        other._validated = dict(self._validated)
        return other


@pytest.fixture
def search(monkeypatch):
    import stowage.solver.scalable as module
    monkeypatch.setattr(module, "make_feasibility_engine", PhysicalOracle)
    return module


def problem():
    return tiny_instance(71, n_load=3, n_other=0, p40=0)


def test_construct_replays_and_reports_bound(search):
    instance = problem()
    # Activated replay resolves later-call port rules on every physical lift;
    # allow that real work while preserving the original legacy smoke budget.
    budget = 5 if rule_freeze_v1_enabled() else 2
    result = search.ScalableSolver().plan(instance, seed=3, time_limit_s=budget)
    assert result.status == "ok", result.message
    space = get_action_space()
    assert result.plan.action_space == f"{space.name}@{space.version}"
    replay = apply_plan(instance, result.plan)
    assert replay.legal
    assert replay.count.loads == len(instance.load_list)
    assert result.metrics["lower_bound"] <= replay.count.weighted_nonproductive
    assert result.metrics["gap"] == 0
    engine = PhysicalOracle(instance, get_action_space())
    for action in get_action_space().from_plan(instance, result.plan):
        assert engine.is_legal(action)
        engine.push(action)
    assert engine.is_complete()


def test_complete_preserves_legal_partial_action(search):
    instance = problem()
    prefix = Place(instance.load_list[0], "0010182")
    result = search.complete(instance, [prefix], seed=4, time_limit_s=2)
    assert result.status == "ok", result.message
    assert get_action_space().from_plan(instance, result.plan)[0] == prefix
    assert apply_plan(instance, result.plan).legal


def test_complete_fixes_rejected_partial_action(search):
    instance = problem()
    result = search.complete(instance, [Place(instance.load_list[0], "9999982")], time_limit_s=2)
    assert result.status == "ok", result.message
    assert apply_plan(instance, result.plan).legal


def test_improvement_never_worsens_complete_incumbent(search):
    instance = problem()
    initial = search.ScalableSolver().plan(instance, time_limit_s=2)
    improved = search.improve(instance, initial.plan, seed=3, time_limit_s=2)
    assert improved.status == "ok", improved.message
    assert apply_plan(instance, improved.plan).count.weighted_nonproductive <= apply_plan(instance, initial.plan).count.weighted_nonproductive


def test_zero_budget_is_timeout(search):
    result = search.ScalableSolver().plan(problem(), time_limit_s=0)
    assert result.status == "timeout"


def test_unavailable_oracle_fails_closed(monkeypatch):
    import stowage.solver.scalable as module
    def unavailable(*args, **kwargs):
        raise NotImplementedError("shared feasibility engine unavailable")
    monkeypatch.setattr(module, "make_feasibility_engine", unavailable)
    result = module.ScalableSolver().plan(problem(), time_limit_s=1)
    assert result.status == "error"
    assert "feasibility" in result.message.lower()


def test_lns_preserves_nonzero_incumbent_and_reports_exact_gap(search):
    # Force hold access: fixture has only eight deck cells. Hold covers and
    # future POD blockers give search a real nonzero objective to improve.
    instance = tiny_instance(72, n_load=10, n_other=0, p40=0)
    config = search.ScalableConfig(beam_width=1, branch_factor=2, max_neighborhoods=3)
    # This tests incumbent preservation, not latency. Give the shared host
    # enough headroom; dedicated deadline tests cover budget enforcement.
    first = search.ScalableSolver(config).plan(instance, seed=1, time_limit_s=5)
    assert first.status == "ok", first.message
    value = apply_plan(instance, first.plan).count.weighted_nonproductive
    assert value > 0
    improved = search.improve(instance, first.plan, seed=2, time_limit_s=5, config=config)
    assert improved.status == "ok", improved.message
    replay = apply_plan(instance, improved.plan)
    assert replay.legal and replay.count.loads == 10
    assert replay.count.weighted_nonproductive <= value
    assert improved.metrics["neighborhoods"] > 0
    assert improved.metrics["gap"] == pytest.approx(
        (replay.count.weighted_nonproductive - improved.metrics["lower_bound"])
        / replay.count.weighted_nonproductive)


@pytest.mark.parametrize("kwargs", [{"beam_width": 0}, {"destroy_fraction": 0},
                                    {"default_time_limit_s": float("inf")}])
def test_configuration_rejects_invalid_limits(search, kwargs):
    with pytest.raises(ValueError):
        search.ScalableConfig(**kwargs)


def test_empty_load_call_is_complete(search):
    instance = tiny_instance(73, n_load=0, n_other=1, p40=0)
    result = search.ScalableSolver().plan(instance, time_limit_s=1)
    assert result.status == "ok", result.message
    assert result.metrics["gap"] == 0
    assert apply_plan(instance, result.plan).legal


def test_local_necessary_condition_mode_is_accepted(search):
    from stowage.feasibility import FeasibilityConfig
    config = search.ScalableConfig(feasibility_config=FeasibilityConfig(extra={"certify_completion": 0}))
    result = search.ScalableSolver(config).plan(problem(), time_limit_s=2)
    assert result.status == "ok", result.message


def test_explicit_oracle_budget_and_margin_are_preserved(search, monkeypatch):
    from stowage.feasibility import FeasibilityConfig
    seen = []
    def capture(instance, space, *, config):
        seen.append(config)
        return PhysicalOracle(instance, space)
    monkeypatch.setattr(search, "make_feasibility_engine", capture)
    instance = problem()
    oracle_config = FeasibilityConfig(stability_margin_scale=2, extra={"search_nodes": 400})
    result = search.ScalableSolver(search.ScalableConfig(feasibility_config=oracle_config)).plan(instance, time_limit_s=2)
    assert result.status == "ok", result.message
    assert seen[0].stability_margin_scale == oracle_config.stability_margin_scale
    assert seen[0].extra["search_nodes"] == 400
    assert oracle_config.extra == {"search_nodes": 400}


def test_oracle_structural_action_fallback(search, monkeypatch):
    from stowage.actions.space import Relocate
    instance = problem()
    ex = Expander(instance)
    box = next(stack[-1] for stack in ex.yard.values())
    source = ex.location(box)[1]
    override = Relocate(box, ex.yard_target(box, source))
    # This exercises the structural-action protocol, not unrestricted slot
    # search. Keep a complete three-box domain, checked by the real physical
    # oracle, so activated lift/profile checks don't consume the smoke budget
    # exploring every unrelated destination in the fixture vessel.
    destinations = dict(zip(instance.load_list, ("0010182", "0010282", "0030182")))

    class OverrideOracle(PhysicalOracle):
        def is_legal(self, action):
            if not self.history:
                return action == override
            return (isinstance(action, Place) and destinations.get(action.box) == action.slot
                    and super().is_legal(action))

        def legal_actions(self):
            return ([override] if not self.history else
                    [action for cid, slot in destinations.items()
                     if self.is_legal(action := Place(cid, slot))])

        def copy(self):
            other = OverrideOracle(self.instance, self.space)
            other.ex, other.state, other.history = self.ex.copy(), self.state, list(self.history)
            other._validated = dict(self._validated)
            return other

    monkeypatch.setattr(search, "make_feasibility_engine", OverrideOracle)
    result = search.ScalableSolver(search.ScalableConfig(max_neighborhoods=0)).plan(instance, time_limit_s=2)
    assert result.status == "ok", (result.message, result.metrics)
    replay = apply_plan(instance, result.plan)
    assert replay.legal and replay.count.loads == len(instance.load_list) == 3
    assert result.plan.moves[0].kind == "relocate"


def test_beam_recovers_archived_sibling_after_dead_end(search, monkeypatch):
    instance = tiny_instance(74, n_load=2, n_other=0, p40=0)
    first, second = instance.load_list
    bad, good = Place(first, "0010182"), Place(first, "0010282")
    finish = Place(second, "0030282")

    class DeadEndOracle(PhysicalOracle):
        def legal_actions(self):
            if not self.history:
                return [bad, good]
            if self.history == [good]:
                return [finish]
            return []

        def is_legal(self, action):
            return action in self.legal_actions() and super().is_legal(action)

        def copy(self):
            other = DeadEndOracle(self.instance, self.space)
            other.ex, other.state, other.history = self.ex.copy(), self.state, list(self.history)
            return other

    monkeypatch.setattr(search, "make_feasibility_engine", DeadEndOracle)
    # Make the dead-end branch deliberately look better; recovery must not
    # depend on which equivalent slot a seeded tie happens to select.
    rank = search._action_rank
    monkeypatch.setattr(search, "_action_rank", lambda inst, node, action, pods:
                        (0, -1, 0, 0, 0, "") if action == bad else rank(inst, node, action, pods))
    config = search.ScalableConfig(beam_width=1, branch_factor=2, max_neighborhoods=0, backtrack_limit=4)
    result = search.ScalableSolver(config).plan(instance, time_limit_s=2)
    assert result.status == "ok", result.message
    assert result.metrics["backtracks"] >= 1
    assert get_action_space().from_plan(instance, result.plan) == [good, finish]
    repaired = search.complete(instance, [bad], config=config, time_limit_s=2)
    assert repaired.status == "ok", repaired.message
    assert repaired.metrics["prefix_relaxations"] >= 1
    assert repaired.metrics["retained_actions"] == 0
    assert repaired.metrics["discarded_actions"] == 1
    assert get_action_space().from_plan(instance, repaired.plan) == [good, finish]
    no_backtrack = search.ScalableSolver(search.ScalableConfig(
        beam_width=1, branch_factor=2, max_neighborhoods=0, backtrack_limit=0
    )).plan(instance, time_limit_s=2)
    assert no_backtrack.status == "dead_end"


def test_interchangeable_category_uses_topmost_box(search):
    instance = tiny_instance(76, n_load=2, n_other=0, p40=0)
    bottom, top = instance.load_list
    prototype = instance.containers[0].model_copy(update={"category": "same-physical-cargo"})
    instance.containers = [prototype.model_copy(update={"id": cid}) for cid in (bottom, top)]
    stack = instance.yard.stacks[0].model_copy(update={"boxes": [bottom, top]})
    instance.yard.stacks = [stack]
    result = search.ScalableSolver(search.ScalableConfig(beam_width=1, max_neighborhoods=0)).plan(
        instance, time_limit_s=2)
    assert result.status == "ok", result.message
    assert get_action_space().from_plan(instance, result.plan)[0].box == top
    assert apply_plan(instance, result.plan).count.relocations == 0


def test_hard_deadline_during_lns_keeps_complete_incumbent(search, monkeypatch):
    from stowage.solver.scalable_oracle import SearchDeadline
    instance = tiny_instance(72, n_load=10, n_other=0, p40=0)
    calls = []
    def factory(instance, space, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise SearchDeadline([{"function": "test_lns_factory"}])
        return PhysicalOracle(instance, space)
    monkeypatch.setattr(search, "make_feasibility_engine", factory)
    result = search.ScalableSolver(search.ScalableConfig(
        beam_width=1, branch_factor=2, max_neighborhoods=2)).plan(instance, time_limit_s=3)
    assert len(calls) == 2
    assert result.status == "ok", result.message
    assert result.metrics["deadline_enforced"]
    replay = apply_plan(instance, result.plan)
    assert replay.legal and replay.count.loads == 10
    assert result.metrics["objective"] == replay.count.weighted_nonproductive
