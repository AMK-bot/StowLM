"""Bounded retry behavior on physical synthetic fixtures; no gold certification."""
from __future__ import annotations

import pytest

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.samples import legal_slots, tiny_instance
from stowage.actions.space import Place
from stowage.feasibility import DeadEnd
from stowage.moves import apply_move, apply_plan, initial_state
from stowage.solver import scalable


class TrapOracle:
    """All initial moves legal; placing L first has no permitted continuation."""

    def __init__(self, instance, space, **kwargs):
        self.instance, self.space = instance, space
        self.ex, self.state, self.history = Expander(instance), initial_state(instance), []

    @property
    def depth(self):
        return len(self.history)

    def actions(self):
        return list(self.history)

    def is_complete(self):
        return all(self.ex.location(c)[0] == "ship" for c in self.instance.load_list)

    def dead_end(self):
        if self.history and self.history[0].box == self.instance.load_list[1]:
            return DeadEnd(self.depth, "Synthetic E2 completion trap", ("expert.e2.weight_order",))
        return None

    def is_legal(self, action):
        if self.dead_end() is not None or not isinstance(action, Place):
            return False
        if self.ex.location(action.box)[0] == "ship":
            return False
        try:
            ex, state = self.ex.copy(), self.state
            for move in ex.step(action):
                state = apply_move(self.instance, state, move)
            return True
        except ValueError:
            return False

    def legal_actions(self):
        return [Place(c, s) for c in self.instance.load_list
                if self.ex.location(c)[0] != "ship"
                for s in legal_slots(self.instance, self.ex, c) if self.is_legal(Place(c, s))]

    def push(self, action):
        assert self.is_legal(action)
        for move in self.ex.step(action):
            self.state = apply_move(self.instance, self.state, move)
        self.history.append(action)

    def copy(self):
        other = type(self)(self.instance, self.space)
        other.ex, other.state, other.history = self.ex.copy(), self.state, list(self.history)
        return other

    def to_plan(self):
        return self.space.expand(self.instance, self.history)


@pytest.fixture
def legacy_rules():
    """These assertions specify the frozen legacy class/E6/clearance contract."""
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=False):
        yield


def trap_instance():
    instance = tiny_instance(318, n_load=2, n_other=0, p40=0)
    heavy, light = instance.load_list
    instance.containers = [b.model_copy(update={"gross_mass_kg": 25000 if b.id == heavy else 9000,
                                               "pod": "THBKK"}) for b in instance.containers]
    instance.yard.stacks = [instance.yard.stacks[0].model_copy(update={"boxes": [heavy, light]})]
    return instance


def test_weight_rule_steers_restart_out_of_accessibility_trap(monkeypatch):
    monkeypatch.setattr(scalable, "make_feasibility_engine", TrapOracle)
    instance = trap_instance()
    config = scalable.ScalableConfig(beam_width=1, branch_factor=1, candidate_boxes=1,
                                    candidate_slots=2, backtrack_limit=2, max_neighborhoods=0)
    result = scalable.ScalableSolver(config).plan(instance, seed=7, time_limit_s=2)
    assert result.status == "ok", result.message
    assert result.metrics["restarts"] == 1
    assert result.metrics["restart_strategies"] == [0, 1]
    assert result.metrics["dead_end_rule_ids"]["expert.e2.weight_order"] > 0
    assert [a["outcome"] for a in result.metrics["construction_attempts"]] == ["exhausted", "complete"]
    assert result.metrics["deepest_depth"] == 2
    assert get_action_space().from_plan(instance, result.plan)[0].box == instance.load_list[0]
    assert apply_plan(instance, result.plan).legal


def test_root_necessary_dead_end_never_probes_actions_or_restarts(monkeypatch):
    class Impossible(TrapOracle):
        def dead_end(self):
            return DeadEnd(0, "Missing global data", ("checker.missing_vessel_data",))

        def is_legal(self, action):
            raise AssertionError("Root dead end must short-circuit probing")

    monkeypatch.setattr(scalable, "make_feasibility_engine", Impossible)
    result = scalable.ScalableSolver().plan(trap_instance(), time_limit_s=1)
    assert result.status == "dead_end"
    assert result.metrics["candidate_probes"] == result.metrics["restarts"] == 0
    assert result.metrics["construction_attempts"][0]["outcome"] == "prefix_dead_end"


def test_early_beam_is_wider_but_bounded_by_branch_budget(monkeypatch):
    # Use the synthetic oracle without the trap: the only purpose here is to
    # check that an actual shallow expansion retains more than the base width.
    class Open(TrapOracle):
        def dead_end(self):
            return None

    monkeypatch.setattr(scalable, "make_feasibility_engine", Open)
    config = scalable.ScalableConfig(beam_width=1, branch_factor=5, early_beam_width=4,
                                    early_beam_depth=30, max_neighborhoods=0)
    result = scalable.ScalableSolver(config).plan(trap_instance(), time_limit_s=2)
    assert result.status == "ok", result.message
    assert result.metrics["max_beam_width"] == 4


def test_invalid_restart_and_early_limits_rejected():
    import pytest
    for kwargs in ({"max_restarts": -1}, {"early_beam_width": 0}, {"early_beam_depth": -1}):
        with pytest.raises(ValueError):
            scalable.ScalableConfig(**kwargs)


def test_pending_heavy_cargo_keeps_hatch_decks_clear_even_with_spare_deck_capacity(legacy_rules):
    instance = tiny_instance(319, n_load=2, n_other=0, p40=0)
    heavy, medium = instance.load_list
    instance.containers = [b.model_copy(update={"gross_mass_kg": 25000 if b.id == heavy else 14000,
                                               "pod": "THBKK"}) for b in instance.containers]
    ex = Expander(instance)
    node = scalable._Node(object(), ex)
    pods = scalable._pod_map(instance)
    # The fixture has eight deck cells for only two boxes. Former scoring
    # charged hold opening=1 but deck sealing=0 and put the medium box on deck.
    assert scalable._needs_hold_access(ex, instance.load_list, scalable._weight_ranks(instance))
    hold = scalable._action_rank(instance, node, Place(medium, "0010102"), pods)
    deck = scalable._action_rank(instance, node, Place(medium, "0010182"), pods)
    assert hold < deck


def test_all_medium_cargo_with_spare_deck_capacity_does_not_force_hold_preference():
    instance = tiny_instance(320, n_load=2, n_other=0, p40=0)
    instance.containers = [b.model_copy(update={"gross_mass_kg": 14000, "pod": "THBKK"})
                           for b in instance.containers]
    ex = Expander(instance)
    assert not scalable._needs_hold_access(ex, instance.load_list, scalable._weight_ranks(instance))
    node, box, pods = scalable._Node(object(), ex), instance.load_list[0], scalable._pod_map(instance)
    assert scalable._action_rank(instance, node, Place(box, "0010182"), pods) < scalable._action_rank(
        instance, node, Place(box, "0010102"), pods)


@pytest.mark.usefixtures("legacy_rules")
def test_dominance_prunes_physical_restow_reload_cycle_and_recovers_sibling(monkeypatch):
    from stowage.actions.space import Relocate, RestowOff
    from stowage.model import Placement
    instance = tiny_instance(321, n_load=2, n_other=0, p40=0)
    aboard, to_load = instance.load_list
    instance.load_list = [to_load]
    instance.onboard.placements = [Placement(container_id=aboard, slot="0010182")]
    for stack in instance.yard.stacks:
        stack.boxes = [b for b in stack.boxes if b != aboard]
    instance.yard.stacks = [s for s in instance.yard.stacks if s.boxes]
    ex = Expander(instance)
    source = ex.where[to_load][1]
    target = ex.yard_target(to_load, source)
    off, restore = RestowOff(aboard), Place(aboard, "0010182")
    progress, finish = Relocate(to_load, target), Place(to_load, "0010282")

    class CycleOracle(TrapOracle):
        def dead_end(self):
            return None

        def is_complete(self):
            return all(self.ex.where.get(c, (None,))[0] == "ship" for c in (aboard, to_load))

        def legal_actions(self):
            if self.is_complete():
                return []
            if self.ex.where[aboard][0] == "quay":
                return [restore]
            return [finish] if self.ex.where[to_load][1] == target else [off, progress]

        def is_legal(self, action):
            return action in self.legal_actions()

    monkeypatch.setattr(scalable, "make_feasibility_engine", CycleOracle)
    rank = scalable._action_rank
    monkeypatch.setattr(scalable, "_action_rank", lambda inst, node, action, pods:
                        (0, -10, 0, 0, 0, "") if action == off else rank(inst, node, action, pods))
    config = scalable.ScalableConfig(beam_width=1, branch_factor=2, early_beam_width=1,
                                    max_restarts=0, max_neighborhoods=0, backtrack_limit=4)
    result = scalable.ScalableSolver(config).plan(instance, time_limit_s=2)
    assert result.status == "ok", result.message
    assert result.metrics["dominance_prunes"] >= 1
    assert result.metrics["backtracks"] >= 1
    assert get_action_space().from_plan(instance, result.plan) == [progress, finish]
    assert apply_plan(instance, result.plan).legal


def test_exact_state_dominance_accepts_cheaper_history_and_bounds_memory():
    ex = Expander(tiny_instance(322, n_load=2, n_other=0, p40=0))
    seen, metrics = {}, {}
    assert scalable._admit_state(ex, 5, seen, 2, metrics)
    assert not scalable._admit_state(ex.copy(), 5, seen, 2, metrics)
    assert not scalable._admit_state(ex.copy(), 6, seen, 2, metrics)
    assert scalable._admit_state(ex.copy(), 4, seen, 2, metrics)
    first_key = next(iter(seen))
    ex.covers_off["synthetic-cover"] = "quay"
    assert scalable._admit_state(ex, 4, seen, 2, metrics)
    ex.covers_off["synthetic-cover"] = "other-cover"
    assert scalable._admit_state(ex, 4, seen, 2, metrics)
    assert len(seen) == metrics["dominance_cache_peak"] == 2
    assert metrics["dominance_evictions"] == 1
    assert first_key not in seen


def test_soft_moment_preference_chooses_opposite_end_and_side_for_remaining_cargo():
    from stowage.model import Placement
    instance = tiny_instance(323, n_load=2, n_other=0, p40=0)
    first, remaining = instance.load_list
    instance.containers = [b.model_copy(update={"gross_mass_kg": 14000}) for b in instance.containers]
    instance.vessel.cells = [c.model_copy(update={
        "lcg_m": 0 if c.id.startswith("001") else 100,
        "tcg_m": -5 if c.id[3:5] == "01" else 5}) for c in instance.vessel.cells]
    instance.onboard.placements = [Placement(container_id=first, slot="0010182")]
    for stack in instance.yard.stacks:
        stack.boxes = [b for b in stack.boxes if b != first]
    instance.yard.stacks = [s for s in instance.yard.stacks if s.boxes]
    node = scalable._Node(object(), Expander(instance))
    opposite = scalable._moment_preference(node, remaining, ("0030282",))
    same_end = scalable._moment_preference(node, remaining, ("0010282",))
    same_side = scalable._moment_preference(node, remaining, ("0030182",))
    assert opposite == 0
    assert opposite < same_end and opposite < same_side


def test_long_box_moment_counts_mass_once_at_both_cells_average():
    from stowage.model import Placement
    instance = tiny_instance(324, n_load=1, n_other=0, p40=1)
    box = instance.load_list[0]
    instance.onboard.placements = [Placement(container_id=box, slot="0020182")]
    instance.yard.stacks = []
    node = scalable._Node(object(), Expander(instance))
    mass, mx, my, *_ = scalable._balance_context(node)
    weight = instance.containers[0].gross_mass_kg
    cells = node.ex.cells
    assert mass == weight
    assert mx == weight * (cells["0010182"].lcg_m + cells["0030182"].lcg_m) / 2
    assert my == weight * (cells["0010182"].tcg_m + cells["0030182"].tcg_m) / 2


def test_interrupted_attempt_keeps_incremental_maximum_progress(monkeypatch):
    from stowage.solver.scalable_oracle import SearchDeadline

    class Interrupted(TrapOracle):
        def dead_end(self):
            return None

        def is_legal(self, action):
            if self.depth:
                raise SearchDeadline([{"function": "synthetic_slot_lp"}])
            return super().is_legal(action)

    monkeypatch.setattr(scalable, "make_feasibility_engine", Interrupted)
    result = scalable.ScalableSolver(scalable.ScalableConfig(max_neighborhoods=0)).plan(
        trap_instance(), time_limit_s=2)
    assert result.status == "timeout"
    assert result.metrics["deadline_enforced"]
    assert result.metrics["deepest_depth"] == 1
    assert result.metrics["partial_loaded"] == result.metrics["partial_depth"] == 1
    assert len(get_action_space().from_plan(trap_instance(), result.plan)) == 1
    assert apply_plan(trap_instance(), result.plan).count.loads == 1


def test_exact_state_key_is_independent_of_equal_string_object_sharing():
    from stowage.model import Placement
    instance = tiny_instance(325, n_load=1, n_other=0, p40=1)
    instance.onboard.placements = [Placement(container_id=instance.load_list[0], slot="0020182")]
    instance.yard.stacks = []
    ex = Expander(instance)
    other = ex.copy()
    other.occ = {cell.encode().decode(): box.encode().decode() for cell, box in ex.occ.items()}
    assert ex.occ == other.occ
    assert scalable._state_key(ex) == scalable._state_key(other)


def test_mrv_public_counts_puts_zero_choices_last_and_reuses_cached_counts():
    import random
    import time
    instance = tiny_instance(326, n_load=3, n_other=0, p40=0)
    rare, plenty, waiting = instance.load_list
    instance.containers = [b.model_copy(update={"category": b.id}) for b in instance.containers]

    class Counts:
        calls = 0

        def legal_actions(self):
            self.calls += 1
            return [Place(rare, "0010182"), Place(plenty, "0010182"), Place(plenty, "0030282")]

    engine = Counts()
    node = scalable._Node(engine, Expander(instance), strategy=3)
    counts = scalable._legal_pair_counts(node, instance.load_list)
    assert counts == {rare: 1, plenty: 2, waiting: 0}
    assert scalable._legal_pair_counts(node, instance.load_list) is counts
    cfg = scalable.ScalableConfig(candidate_slots=1)
    candidates = list(scalable._candidates(instance, node, scalable._slots(instance), cfg,
                                         time.monotonic() + 1, random.Random(0), scalable._pod_map(instance)))
    first_boxes = list(dict.fromkeys(a.box for a in candidates))
    assert first_boxes == [rare, plenty, waiting]
    assert engine.calls == 1


def test_mrv_retains_scarce_box_before_cheaper_flexible_box(monkeypatch):
    instance = tiny_instance(327, n_load=2, n_other=0, p40=0)
    rare, plenty = instance.load_list
    instance.containers = [b.model_copy(update={"gross_mass_kg": 14000, "pod": "THBKK",
                                               "category": b.id}) for b in instance.containers]
    prototype = instance.yard.stacks[0]
    instance.yard.stacks = [prototype.model_copy(update={"key": "A-001-01", "boxes": [rare]}),
                           prototype.model_copy(update={"key": "A-003-01", "boxes": [plenty]})]
    required_slot = "0010182"

    class ScarceOracle(TrapOracle):
        def dead_end(self):
            return None

        def legal_actions(self):
            return [Place(c, s) for c in self.instance.load_list
                    if self.ex.where[c][0] != "ship"
                    for s in legal_slots(self.instance, self.ex, c)
                    if c != rare or s == required_slot]

        def is_legal(self, action):
            return action in self.legal_actions()

    monkeypatch.setattr(scalable, "make_feasibility_engine", ScarceOracle)
    rank = scalable._action_rank
    monkeypatch.setattr(scalable, "_action_rank", lambda inst, node, action, pods:
                        (0, -100, 0, 0, 0, "") if action == Place(plenty, required_slot)
                        else rank(inst, node, action, pods))
    kwargs = dict(beam_width=1, early_beam_width=1, branch_factor=2, backtrack_limit=0,
                  max_restarts=0, max_neighborhoods=0)
    no_mrv = scalable.ScalableSolver(scalable.ScalableConfig(mrv_tail_boxes=0, **kwargs)).plan(
        instance, time_limit_s=2)
    assert no_mrv.status == "dead_end"
    result = scalable.ScalableSolver(scalable.ScalableConfig(**kwargs)).plan(instance, time_limit_s=2)
    assert result.status == "ok", result.message
    assert get_action_space().from_plan(instance, result.plan)[0].box == rare
    assert apply_plan(instance, result.plan).legal


def test_deadline_partial_prefers_loaded_manifest_and_cheaper_history_over_raw_depth(monkeypatch):
    from stowage.actions.space import RestowOff
    from stowage.solver.scalable_oracle import SearchDeadline
    instance = tiny_instance(328, n_load=3, n_other=0, p40=0)
    first, second, _ = instance.load_list
    path = [Place(first, "0010182"), Place(second, "0030282"),
            RestowOff(first), Place(first, "0010182")]

    class ReloadThenInterrupt(TrapOracle):
        def dead_end(self):
            return None

        def legal_actions(self):
            if self.depth == len(path):
                raise SearchDeadline([{"function": "after_reload"}])
            return [path[self.depth]]

        def is_legal(self, action):
            return action in self.legal_actions()

    monkeypatch.setattr(scalable, "make_feasibility_engine", ReloadThenInterrupt)
    result = scalable.ScalableSolver(scalable.ScalableConfig(
        max_neighborhoods=0, dominance_cache_size=0)).plan(instance, time_limit_s=2)
    assert result.status == "timeout"
    assert result.metrics["deepest_depth"] == 4
    assert result.metrics["partial_loaded"] == 2
    assert result.metrics["partial_depth"] == 2
    assert get_action_space().from_plan(instance, result.plan) == path[:2]
    replay = apply_plan(instance, result.plan)
    assert replay.legal and replay.count.loads == 2 and replay.count.restow_lifts == 0
