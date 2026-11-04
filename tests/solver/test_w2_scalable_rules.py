"""Activated scalable search follows shared rules and D11's lexicographic order."""
from __future__ import annotations

import random

import pytest
from time import monotonic
from types import SimpleNamespace

from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.rules.catalogue import rule_freeze_v1
from stowage.solver import scalable


def test_activated_weight_guidance_uses_exact_mass_legacy_keeps_classes():
    instance = tiny_instance(71, n_load=2, n_other=0, p40=0)
    instance.containers[0].gross_mass_kg = 21001
    instance.containers[1].gross_mass_kg = 21999
    with rule_freeze_v1(enabled=False):
        assert scalable._weight_ranks(instance)[instance.containers[0].id] == 2
    with rule_freeze_v1(enabled=True):
        ranks = scalable._weight_ranks(instance)
        assert ranks[instance.containers[0].id] == 21001
        assert ranks[instance.containers[1].id] == 21999


def test_activated_e6_has_no_independent_hold_gate_or_preference():
    instance = tiny_instance(71, n_load=2, n_other=0, p40=0)
    class NoHoldProbe:
        def legal_hold_counts(self, boxes):
            raise AssertionError("E6 is a terminal level-four metric")
    with rule_freeze_v1(enabled=True):
        node = scalable._Node(NoHoldProbe(), Expander(instance))
        assert scalable._e6_preference(node, instance.load_list[0], True,
                                      instance.load_list, scalable._weight_ranks(instance)) == 0


def test_activated_beam_order_puts_moves_before_soft_metrics_and_guidance():
    instance = tiny_instance(71, n_load=1, n_other=0, p40=0)
    ex = Expander(instance)
    cheap = scalable._Node(object(), ex, score=0, guidance=(99,), completion_preference=(1,))
    costly = scalable._Node(object(), ex.copy(), score=1, guidance=(-99,), completion_preference=(0,))
    with rule_freeze_v1(enabled=True):
        assert scalable._beam_rank(instance, cheap) < scalable._beam_rank(instance, costly)


@pytest.mark.parametrize("side", ["port", "starboard"])
def test_activated_same_bay_candidate_order_is_far_rows_first(side):
    instance = tiny_instance(71, n_load=1, n_other=0, p40=0)
    instance.containers[0].gross_mass_kg = 10000
    instance.port.berthing_side = side
    with rule_freeze_v1(enabled=True):
        node = scalable._Node(SimpleNamespace(), Expander(instance))
        node.legal_counts = {instance.load_list[0]: 2}
        choices = list(scalable._candidates(instance, node, {20: ['0010182', '0010282']},
            scalable.ScalableConfig(), monotonic()+2, random.Random(0), scalable._pod_map(instance)))
        first = next(action for action in choices if isinstance(action, Place))
        cells = {cell.id: cell for cell in instance.vessel.cells}
        expected = (min if side == "port" else max)(['0010182', '0010282'],
            key=lambda slot: cells[slot].tcg_m)
        assert first.slot == expected


def test_exact_window_accepts_equal_moves_only_when_level_four_improves():
    from stowage.solver.scalable_exact import _improves
    instance = tiny_instance(71, n_load=1, n_other=0, p40=0)
    instance.containers[0].gross_mass_kg = 24000
    from stowage.model import Placement
    empty = instance.onboard.model_copy(update={"placements": []})
    deck = empty.model_copy(update={"placements": [Placement(container_id=instance.load_list[0], slot="0010182")]})
    hold = empty.model_copy(update={"placements": [Placement(container_id=instance.load_list[0], slot="0010102")]})
    with rule_freeze_v1(enabled=False):
        assert not _improves(instance, 0, hold, 0, deck)
    with rule_freeze_v1(enabled=True):
        assert _improves(instance, 0, hold, 0, deck)
        assert not _improves(instance, 1, hold, 0, deck)
        assert not _improves(instance, 0, deck, 0, hold)


def test_activated_solver_discards_same_class_heavier_upper_prefix():
    from stowage.feasibility import FeasibilityConfig
    from stowage.actions import get_action_space
    from stowage.moves import apply_plan
    instance = tiny_instance(91, n_load=2, n_other=0, p40=0)
    for box, mass in zip(instance.containers, (11000, 19000)):
        box.gross_mass_kg, box.pod = mass, "THBKK"
    config = scalable.ScalableConfig(max_neighborhoods=0, feasibility_config=FeasibilityConfig(
        extra={"completion_screens": 0, "global_screen": 0}))
    prefix = [Place(instance.load_list[0], "0010102"), Place(instance.load_list[1], "0010104")]
    with rule_freeze_v1(enabled=True):
        result = scalable.complete(instance, prefix, config=config, time_limit_s=3)
        assert result.status == "ok", result.message
        assert result.metrics["retained_actions"] == 1
        assert result.metrics["discarded_actions"] == 1
        actions = get_action_space().from_plan(instance, result.plan)
        assert prefix[1] not in actions
        assert apply_plan(instance, result.plan).legal
        assert all(move.kind != "restow_shift" for move in result.plan.moves)


def test_activated_solver_retains_heavy_deck_prefix_with_empty_hold():
    from stowage.feasibility import FeasibilityConfig
    from stowage.moves import apply_plan
    instance = tiny_instance(91, n_load=1, n_other=0, p40=0)
    instance.containers[0].gross_mass_kg = 24000
    config = scalable.ScalableConfig(max_neighborhoods=0, feasibility_config=FeasibilityConfig(
        extra={"completion_screens": 0, "global_screen": 0}))
    with rule_freeze_v1(enabled=True):
        result = scalable.complete(instance, [Place(instance.load_list[0], "0010182")],
                                   config=config, time_limit_s=3)
        assert result.status == "ok", result.message
        assert result.metrics["retained_actions"] == 1
        assert result.metrics["discarded_actions"] == 0
        assert result.metrics["heavy_on_deck_count"] == 1
        assert apply_plan(instance, result.plan).legal


def test_activated_solver_rejects_killed_upper_slot():
    from stowage.feasibility import FeasibilityConfig
    from stowage.actions import get_action_space
    from stowage.model import OutOfGauge
    from stowage.moves import apply_plan
    instance = tiny_instance(91, n_load=2, n_other=0, p40=0)
    for box, mass in zip(instance.containers, (15000, 10000)):
        box.gross_mass_kg, box.pod = mass, "THBKK"
    instance.containers[0].oog = OutOfGauge(over_height_m=.1)
    config = scalable.ScalableConfig(max_neighborhoods=0, feasibility_config=FeasibilityConfig(
        extra={"completion_screens": 0, "global_screen": 0}))
    prefix = [Place(instance.load_list[0], "0010182"), Place(instance.load_list[1], "0010184")]
    with rule_freeze_v1(enabled=True):
        result = scalable.complete(instance, prefix, config=config, time_limit_s=3)
        assert result.status == "ok", result.message
        assert result.metrics["retained_actions"] == 1
        assert result.metrics["discarded_actions"] == 1
        assert result.metrics["killed_slots"] > 0
        assert prefix[1] not in get_action_space().from_plan(instance, result.plan)
        assert apply_plan(instance, result.plan).legal


@pytest.mark.parametrize("enabled", [False, True])
def test_candidate_mass_normalization_scans_ranks_once_and_preserves_order(enabled):
    class CountedRanks(dict):
        scans = 0

        def values(self):
            self.scans += 1
            return super().values()

    instance = tiny_instance(71, n_load=3, n_other=0, p40=0)
    cfg = scalable.ScalableConfig(mrv_tail_boxes=0)
    slots = {20: ["0010182", "0010282", "0030182", "0030282"]}
    with rule_freeze_v1(enabled=enabled):
        ranks = scalable._weight_ranks(instance)
        counted = CountedRanks(ranks)

        def candidates(weights):
            node = scalable._Node(SimpleNamespace(), Expander(instance), classes=weights)
            return list(scalable._candidates(instance, node, slots, cfg, monotonic()+2,
                                            random.Random(0), scalable._pod_map(instance)))

        expected = candidates(ranks)
        assert candidates(counted) == expected
        assert counted.scans == int(enabled)
