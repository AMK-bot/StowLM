"""Necessary counts catch shared/overlapping domains, without claiming packing."""
from types import SimpleNamespace
from itertools import combinations

import numpy as np

from stowage.conventions import cells_of_slot
from stowage.solver.construct_v4_capacity import CapacityPools


def tables(slots, lengths, heights=None, ranks=None):
    cells = sorted({c for s in slots for c in cells_of_slot(s)})
    return SimpleNamespace(slot_ids=slots, cell_index={c: i for i, c in enumerate(cells)},
                           length=np.array(lengths),
                           height=np.array(heights if heights is not None else [2.591]*len(lengths)),
                           rank=np.array(ranks if ranks is not None else [0]*len(lengths)))


def test_overlapping_long_destinations_cannot_hold_two_boxes():
    t = tables(['0020182', '0040182'], [40, 40])
    deficits = CapacityPools(t).evaluate(np.array([0, 1]), np.ones((2, 2), dtype=bool))
    assert any(d.get('disjoint_long_slots') == 1 for d in deficits)


def test_path_matching_uses_two_disjoint_pairs():
    t = tables(['0020182', '0040182', '0060182'], [40, 40])
    pool = CapacityPools(t)
    assert pool.long_capacity(np.ones(3, dtype=bool)) == 2
    assert pool.evaluate(np.array([0, 1]), np.ones((2, 3), dtype=bool)) == []


def test_tall_boxes_competing_for_one_slot_are_detected():
    t = tables(['0010182', '0030182', '0050182'], [20, 20, 20], [2.896, 2.896, 2.591])
    domains = np.array([[1, 0, 0], [1, 0, 0], [0, 1, 1]], dtype=bool)
    deficits = CapacityPools(t).evaluate(np.arange(3), domains)
    assert any(d.get('pool') == 'height>=2.896' and d['available_cell_units'] == 1
               for d in deficits)


def test_independent_rows_and_tiers_do_not_conflict():
    t = tables(['0020182', '0020282', '0020184'], [40]*3)
    pool = CapacityPools(t)
    assert pool.long_capacity(np.ones(3, dtype=bool)) == 3
    assert pool.evaluate(np.arange(3), np.ones((3, 3), dtype=bool)) == []


def test_empty_demand_passes():
    t = tables(['0020182'], [40])
    assert CapacityPools(t).evaluate(np.array([], dtype=int), np.empty((0, 1), dtype=bool)) == []


def test_disjoint_pair_bound_matches_exhaustive_packings_on_a_path():
    slots = [f'{bay:03d}0182' for bay in range(2, 12, 2)]
    t = tables(slots, [40])
    pool = CapacityPools(t)
    for bits in range(1 << len(slots)):
        active = [i for i in range(len(slots)) if bits & (1 << i)]
        best = 0
        for size in range(len(active) + 1):
            for chosen in combinations(active, size):
                footprints = [set(cells_of_slot(slots[i])) for i in chosen]
                if len(set().union(*footprints)) == 2 * size:
                    best = max(best, size)
        allowed = np.array([i in active for i in range(len(slots))])
        assert pool.long_capacity(allowed) == best
