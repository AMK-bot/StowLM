"""E6/restricted-booking guidance uses public oracle facts, never new rules."""
from __future__ import annotations

import pytest

import random
import time

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig
from stowage.model import SpecialStowage
from stowage.solver import scalable
from stowage.solver.scalable_oracle import build_engine


@pytest.fixture
def legacy_rules():
    """These assertions specify the frozen legacy class/E6/clearance contract."""
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=False):
        yield


def case(seed=711):
    instance = tiny_instance(seed, n_load=2, n_other=0, p40=0)
    instance.containers = [box.model_copy(update={
        "gross_mass_kg": 25000 if box.id == instance.load_list[0] else 14000,
        "pod": "THBKK", "category": box.id}) for box in instance.containers]
    return instance


def test_e6_guidance_reserves_public_compatible_hold_supports_for_heavy_boxes(legacy_rules):
    instance = case()
    engine = build_engine(instance, get_action_space(), FeasibilityConfig(extra={"global_screen": 0}))
    node = scalable._Node(engine, Expander(instance), classes=scalable._weight_ranks(instance))
    heavy, medium = instance.load_list
    counts = engine.legal_hold_counts(instance.load_list)
    assert counts[heavy] > 0
    assert scalable._e6_preference(node, heavy, False, instance.load_list, node.classes) == -1
    assert scalable._e6_preference(node, heavy, True, instance.load_list, node.classes) == 2
    assert scalable._e6_preference(node, medium, False, instance.load_list, node.classes) == 1
    assert scalable._e6_preference(node, medium, True, instance.load_list, node.classes) == 0


def test_no_current_compatible_hold_choice_never_becomes_a_forbidden_action():
    instance = case(712)
    class NoHold:
        def legal_hold_counts(self, boxes):
            return dict.fromkeys(boxes, 0)
    node = scalable._Node(NoHold(), Expander(instance), classes=scalable._weight_ranks(instance))
    heavy = instance.load_list[0]
    assert scalable._e6_preference(node, heavy, True, instance.load_list, node.classes) == 0


def test_away_from_heat_is_shortlisted_before_flexible_cargo_even_when_buried(legacy_rules):
    instance = case(713)
    heat, flexible = instance.load_list
    instance.containers = [box.model_copy(update={"gross_mass_kg": 14000,
        "special_stowage": [SpecialStowage.AWAY_FROM_HEAT] if box.id == heat else []})
        for box in instance.containers]
    instance.yard.stacks = [instance.yard.stacks[0].model_copy(update={"boxes": [heat, flexible]})]
    class Counts:
        def legal_pair_counts(self, boxes):
            return {heat: 1, flexible: 10}
        def legal_hold_counts(self, boxes):
            return dict.fromkeys(boxes, 0)
        def legal_actions(self):
            raise AssertionError("Index-backed guidance must not materialize legal actions")
    node = scalable._Node(Counts(), Expander(instance), strategy=0)
    candidates = list(scalable._candidates(instance, node, scalable._slots(instance),
        scalable.ScalableConfig(candidate_boxes=1, candidate_slots=1), time.monotonic()+1,
        random.Random(0), scalable._pod_map(instance)))
    assert candidates[0].box == heat
    assert scalable._action_rank(instance, node, Place(heat, "0010182"), scalable._pod_map(instance)) < (
        scalable._action_rank(instance, node, Place(flexible, "0010182"), scalable._pod_map(instance)))


def test_completion_lookahead_is_sampled_and_records_boolean_failures_without_slack():
    class Engine:
        depth = 2
        calls = 0
        def completion_check(self):
            self.calls += 1
            return {"c1f": False, "c1h": False, "c1g": True, "c2": True}
    engine = Engine()
    node = scalable._Node(engine, None)
    metrics = {}
    scalable._completion_lookahead(node, 40, metrics)
    assert engine.calls == 0
    engine.depth = 16
    scalable._completion_lookahead(node, 40, metrics)
    assert engine.calls == 1
    assert node.completion_preference == (1, 1, 0, 2)
    assert metrics["completion_subcheck_failures"] == {"c1f": 1, "c1h": 1}
    engine.depth = 17
    scalable._completion_lookahead(node, 8, metrics)
    assert engine.calls == 2


def test_forced_restow_hatch_access_is_costlier_than_clear_hold_access():
    instance = case(714)
    class Restows:
        def legal_hold_counts(self, boxes):
            return dict.fromkeys(boxes, 1)
        def forces_restow(self, action):
            return action.slot == "0030102"
    node = scalable._Node(Restows(), Expander(instance))
    heavy = instance.load_list[0]
    clear = scalable._action_rank(instance, node, Place(heavy, "0010102"), scalable._pod_map(instance))
    restow = scalable._action_rank(instance, node, Place(heavy, "0030102"), scalable._pod_map(instance))
    assert clear < restow


def test_heavy_restart_prioritizes_h_hold_before_heat_medium_in_shortlist_and_beam(legacy_rules):
    instance = case(715)
    heavy, heat = instance.load_list
    instance.containers = [box.model_copy(update={
        "special_stowage": [SpecialStowage.AWAY_FROM_HEAT] if box.id == heat else []})
        for box in instance.containers]
    class Counts:
        def legal_pair_counts(self, boxes):
            return {heavy: 10, heat: 1}
        def legal_hold_counts(self, boxes):
            return dict.fromkeys(boxes, 1)
    pods = scalable._pod_map(instance)
    for strategy, preferred in ((0, heat), (1, heavy)):
        node = scalable._Node(Counts(), Expander(instance), strategy=strategy,
                              classes=scalable._weight_ranks(instance))
        actions = list(scalable._candidates(instance, node, scalable._slots(instance),
            scalable.ScalableConfig(candidate_boxes=1, candidate_slots=1), time.monotonic()+1,
            random.Random(0), pods))
        assert actions[0].box == preferred
        ranks = {cid: scalable._action_rank(instance, node, Place(cid, "0010102"), pods)
                 for cid in instance.load_list}
        assert min(ranks, key=ranks.get) == preferred
        # Child beam ordering uses this exact shared guidance helper.
        guides = {cid: scalable._placement_guidance(node, cid, False, instance.load_list,
                                                   node.classes) for cid in instance.load_list}
        assert min(guides, key=guides.get) == preferred


def test_completion_lookahead_budget_spans_nodes_and_records_skipped_samples():
    class Engine:
        depth = 16
        calls = 0
        def completion_check(self):
            self.calls += 1
            return {"c1f": True, "c1h": True, "c1g": True, "c2": True}
    engine, metrics = Engine(), {}
    for _ in range(260):
        node = scalable._Node(engine, None)
        scalable._completion_lookahead(node, 40, metrics)
        assert node.completion_preference == (0, 0, 0, 0)
    assert engine.calls == metrics["completion_lookahead_calls"] == 256
    assert metrics["completion_lookahead_budget_skips"] == 4
    assert metrics["completion_subcheck_failures"] == {}
    # Non-sampling depths do not count as budget skips or oracle calls.
    engine.depth = 17
    scalable._completion_lookahead(scalable._Node(engine, None), 40, metrics)
    assert engine.calls == 256
    assert metrics["completion_lookahead_budget_skips"] == 4
