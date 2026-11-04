"""Seeded tiny counterexamples to unsafe fixed-order and future-stow bounds.

The fixture builder supplies model-valid geometry. We shrink its yard and ship
before enumerating moves, rather than prune the oracle's legal action space.
The corpus checks the implemented static stowage rules at departure. A second
oracle check without those rules demonstrates the stricter relaxed comparison.
"""

from __future__ import annotations

import random
from statistics import mean

import pytest

from stowage.bounds import lower_bound
from stowage.bounds.bruteforce import brute_force_optimum
from stowage.bounds.components import (
    cpmp_blocking_bound, grouped_blocking_bound, partial_order_blocking_bound,
)
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves.engine import count_moves
from stowage.rules.catalogue import rule_freeze_v1_enabled


CORPUS_SEEDS = tuple(range(240))
COMPONENT_WEIGHTS = {
    "relocations": "relocate",
    "restow_lifts": "restow",
    "hatch_cover_lifts": "hatch_cover",
    "future_restow_lifts": "future_restow",
    "future_hatch_cover_lifts": "future_hatch_cover",
}


def tiny_instance(seed: int, *, mode: int | None = None) -> Instance:
    """Eight small families, with seed-controlled identities, PODs and weights."""
    rng = random.Random(seed)
    mode = seed % 8 if mode is None else mode
    data = build_instance(layover=True).model_dump()
    # This is a move/static-rule relaxation, not a full-physics loading condition.
    # Exclude the builder's unrelated placeholder background and supply labelled
    # synthetic individual CSC ratings rather than depend on missing evidence.
    data["vessel"]["fixed_loads"] = []
    data.update(id=f"bounds_tiny_{seed}", seed=seed, stage="B", load_list=[], discharge_list=[])
    # Explicit synthetic policy parameters, not a claim about an actual port.
    data["rule_overrides"] = {
        "expert.e1.heavy_bottom": {"heavy_threshold": 20000},
        "expert.e2.weight_order": {"tolerance": 0},
        "expert.e6.heavy_under_deck": {"heavy_threshold": 20000},
    }
    boxes = data["containers"][:3]
    rng.shuffle(boxes)
    for box in boxes:
        box.update(length_ft=20, iso_type="22G1", height_m=2.591,
                   tare_kg=2200, gross_mass_kg=12000, max_gross_mass_kg=30000, reefer=False,
                   reefer_setpoint_c=None, dg=None, oog=None, special_stowage=[],
                   pod=rng.choice(["THBKK", "THSGZ"]), category=rng.choice([None, "same", "other"]))
    a, b, c = [box["id"] for box in boxes]
    data["containers"] = boxes
    data["onboard"].update(placements=[], covers_off={})
    data["yard"].update(stacks=[], quay_buffer=[])
    # Two physical yard locations, enough to clear a three-high stack legally.
    block = data["port"]["yard"]["blocks"][0]
    block.update(n_bays=2, n_rows=1, allowed_lengths_ft=[20])
    data["port"]["yard"]["blocks"] = [block]
    with_hold = mode in (3, 4, 5)
    stack_ids = {"00101D", "00301D"} | ({"00101H"} if with_hold else set())
    data["vessel"]["stacks"] = [s for s in data["vessel"]["stacks"] if s["id"] in stack_ids]
    for stack in data["vessel"]["stacks"]:
        if stack["on_deck"]:
            stack["hatch_cover_id"] = "HC-02-1" if with_hold and stack["id"] == "00101D" else None
    cell_ids = {cell for stack in data["vessel"]["stacks"] for cell in stack["cell_ids"]}
    data["vessel"]["cells"] = [cell for cell in data["vessel"]["cells"] if cell["id"] in cell_ids]
    covers = data["vessel"]["hatch_covers"][:1] if with_hold else []
    if covers:
        covers[0].update(hold_stack_ids=["00101H"], deck_stack_ids=["00101D"])
    data["vessel"]["hatch_covers"] = covers

    def yard(ids):
        data["yard"]["stacks"] = [dict(key="A-001-01", length_ft=20, boxes=ids)]

    def aboard(*placements):
        data["onboard"]["placements"] = [dict(slot=slot, container_id=cid) for slot, cid in placements]

    if mode in (0, 6):
        yard([a, b, c])
        data["load_list"] = [a, b, c]
        if mode == 6:
            for box in boxes:
                box.update(category="same", pod="THBKK")
    elif mode in (1, 7):
        yard([a, b, c] if mode == 7 else [a, b])
        data["load_list"] = [a]
    elif mode == 2:
        boxes[0]["pod"] = "THLCH"
        data["discharge_list"] = [a]
        aboard(("0010182", a), ("0010184", b))
    elif mode == 3:
        boxes[0]["pod"] = "THLCH"
        data["discharge_list"] = [a]
        aboard(("0010102", a), ("0010182", b))
    elif mode == 4:
        # An arrival-state future inversion can be removed by a direct shift.
        boxes[0]["pod"], boxes[1]["pod"] = "THBKK", "THSGZ"
        aboard(("0010102", a), ("0010104", b))
    elif mode == 5:
        boxes[0]["pod"] = "THLCH"
        data["discharge_list"] = [a]
        aboard(("0010102", a))
        data["onboard"]["covers_off"] = {"HC-02-1": "quay"}
    # Also exercise non-unit and zero objective weights; none are negative.
    for key in ("relocate", "restow", "hatch_cover", "future_restow", "future_hatch_cover"):
        data["objective"][key] = rng.choice([0.0, 0.5, 1.0, 2.0])
    return Instance.model_validate(data)


@pytest.fixture(scope="module")
def weighted_corpus_results():
    """Cache exact seeded weighted runs shared by safety and aggregate checks."""
    return {}


def _weighted_result(seed, results):
    key = (rule_freeze_v1_enabled(), seed)
    if key not in results:
        instance = tiny_instance(seed)
        result = brute_force_optimum(instance, max_nodes=10000, time_limit_s=None,
                                     check_static_rules=True)
        assert result.status == "optimal", (seed, result)
        results[key] = result
    return results[key]


@pytest.mark.parametrize("seed", CORPUS_SEEDS)
def test_seeded_tiny_bounds_never_exceed_exact_optimum(seed, record_property,
                                                       weighted_corpus_results):
    instance = tiny_instance(seed)
    result = _weighted_result(seed, weighted_corpus_results)
    assert result.plan is not None
    bound = lower_bound(instance)
    count = count_moves(instance, result.plan)
    # A per-lift bound must hold for every feasible completion, including the
    # particular completion minimizing the weighted total.
    observed = dict(relocations=count.relocations, restow_lifts=count.restow_lifts,
                    hatch_cover_lifts=count.hatch_cover_lifts,
                    future_restow_lifts=sum(f.restow_lifts for f in count.future),
                    future_hatch_cover_lifts=sum(f.hatch_cover_lifts for f in count.future))
    for name, optimum_plan_count in observed.items():
        assert 0 <= getattr(bound, name) <= optimum_plan_count, (seed, name, bound, count)
    assert 0 <= bound.weighted <= result.objective + 1e-9, (seed, bound, result)
    w = instance.objective
    expected = (w.relocate * bound.relocations + w.restow * bound.restow_lifts
                + w.hatch_cover * bound.hatch_cover_lifts
                + w.future_restow * bound.future_restow_lifts
                + w.future_hatch_cover * bound.future_hatch_cover_lifts)
    assert bound.weighted == pytest.approx(expected)
    # 0/0 is undefined: zero optima are counted separately in the aggregate.
    if result.objective > 0:
        record_property("bound_tightness", bound.weighted / result.objective)
    record_property("exact_objective", result.objective)
    record_property("oracle_nodes", result.expanded_nodes)
    record_property("oracle_wall_time_s", result.wall_time_s)


@pytest.mark.parametrize("seed", CORPUS_SEEDS)
def test_each_component_is_bounded_by_its_independent_minimum(seed):
    instance = tiny_instance(seed)
    bound = lower_bound(instance)
    for component, weight in COMPONENT_WEIGHTS.items():
        isolated = instance.model_copy(deep=True)
        for name in COMPONENT_WEIGHTS.values():
            setattr(isolated.objective, name, float(name == weight))
        result = brute_force_optimum(isolated, max_nodes=10000, time_limit_s=None,
                                     check_static_rules=True)
        assert result.status == "optimal", (seed, component, result)
        # The exact cost now equals the minimum count for this component over
        # *all* legal completions, independently of the weighted witness above.
        assert 0 <= getattr(bound, component) <= result.objective, (seed, component, bound, result)


def _tightness_summary(pairs):
    """Mean of defined bound/optimum ratios; count zero optima separately."""
    positive = [bound / optimum for bound, optimum in pairs if optimum > 0]
    return {
        "positive_optimum_mean": mean(positive) if positive else None,
        "positive_optimum_count": len(positive),
        "zero_optimum_count": sum(optimum == 0 for _, optimum in pairs),
    }


def test_tightness_mean_excludes_undefined_zero_optima():
    assert _tightness_summary([(1, 2), (0, 0), (0, 0)]) == {
        "positive_optimum_mean": 0.5,
        "positive_optimum_count": 1,
        "zero_optimum_count": 2,
    }
    assert _tightness_summary([(0, 0)])["positive_optimum_mean"] is None


def test_seeded_corpus_tightness_aggregate(weighted_corpus_results, record_property):
    """Reproduce with pytest --junitxml=... and inspect these properties.

    Corpus: seeds 0..239, eight families from tiny_instance. Every objective
    weight is drawn by random.Random(seed) from [0, .5, 1, 2] after fixture draws.
    The fixed generator code, seeds and Python RNG make every draw reproducible.
    """
    pairs = [(lower_bound(tiny_instance(seed)).weighted,
              _weighted_result(seed, weighted_corpus_results).objective)
             for seed in CORPUS_SEEDS]
    summary = _tightness_summary(pairs)
    assert summary["positive_optimum_count"] + summary["zero_optimum_count"] == 240
    assert summary["positive_optimum_count"] > 0
    assert 0 <= summary["positive_optimum_mean"] <= 1
    for name, value in summary.items():
        record_property(name, value)
    record_property("corpus_seeds", "0..239; family=seed%8")
    record_property("objective_weights", "Random(seed); relocate,restow,hatch_cover,future_restow,future_hatch_cover in [0,.5,1,2]")


def test_same_category_and_freely_chosen_slots_do_not_impose_id_order():
    instance = tiny_instance(6, mode=6)
    bound = lower_bound(instance)
    assert bound.relocations == 0


@pytest.mark.parametrize("seed", range(8))
def test_bounds_also_hold_when_static_stowage_rules_are_relaxed(seed):
    instance = tiny_instance(seed)
    result = brute_force_optimum(instance, check_static_rules=False,
                                 max_nodes=10000, time_limit_s=None)
    assert result.status == "optimal"
    assert lower_bound(instance).weighted <= result.objective + 1e-9


def test_nonloading_blocker_count_counts_boxes_not_pairs():
    assert lower_bound(tiny_instance(7, mode=7)).relocations == 2


def test_already_open_cover_only_needs_closing_lift():
    assert lower_bound(tiny_instance(5, mode=5)).hatch_cover_lifts <= 1


def test_future_arrival_inversion_is_not_a_fixed_departure_plan():
    instance = tiny_instance(4, mode=4)
    bound = lower_bound(instance)
    assert bound.future_restow_lifts == 0
    # Moving the through-box onto deck can also remove future hold-cover work.
    assert bound.future_hatch_cover_lifts == 0


def test_lower_bound_is_deterministic_and_pure():
    instance = tiny_instance(3, mode=3)
    before = instance.model_dump_json()
    assert lower_bound(instance) == lower_bound(instance)
    assert instance.model_dump_json() == before


def test_grouped_and_cpmp_bounds_count_each_blocker_once_and_ignore_ties():
    stacks = [["early_a", "early_b", "later", "equal_later"]]
    priorities = dict(early_a=0, early_b=0, later=1, equal_later=1)
    assert grouped_blocking_bound(stacks, priorities) == 2
    assert cpmp_blocking_bound(stacks, priorities) == 2
    assert grouped_blocking_bound(stacks, dict.fromkeys(priorities, 0)) == 0
    # A monotone retrieval stack has no misplaced box; counting inversions
    # against the wrong inequality would incorrectly charge every upper box.
    assert grouped_blocking_bound([["later", "early_a", "early_b"]], priorities) == 0


def test_partial_order_requires_guaranteed_pairs_and_deduplicates_witnesses():
    stacks = [["a", "b", "c"], ["d"]]
    assert partial_order_blocking_bound(stacks, []) == 0
    assert partial_order_blocking_bound(stacks, [("a", "c"), ("b", "c")]) == 1
    assert partial_order_blocking_bound(stacks, [("a", "b"), ("a", "c")]) == 2
    assert partial_order_blocking_bound(stacks, [("d", "c"), ("c", "a")]) == 0
