"""Synthetic objective examples: no map data, stored stow, or sealed input."""
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from objective import Assignment, graph_rows


def example(hold, deck):
    pages = [SimpleNamespace(deck=False, cover=0, slots=tuple(range(len(hold)))),
             SimpleNamespace(deck=True, cover=0, slots=tuple(range(len(hold), len(hold)+len(deck))))]
    pods = dict(enumerate(hold+deck))
    pair = [(b, b) for b in pods]
    slot_page = {q: [(int(q >= len(hold)), q if q < len(hold) else q-len(hold))] for q in pods}
    n = len(pair)
    extra, rows, rhs, names = graph_rows(pair, slot_page, pages, pods,
        sorted(set(pods.values())), [[q] for q in pods], [1]*n, n,
        hatch_now=2, hatch_later=2, restow=2)
    return pair, extra, rows, rhs, names


def fixed_graph_cost(hold, deck):
    pair, extra, rows, rhs, names = example(hold, deck)
    n = len(pair)
    matrix = np.zeros((len(rows), n+len(extra)))
    for i, row in enumerate(rows):
        for j, value in row.items():
            matrix[i, j] = value
    # Graph convex hull at an integral assignment has its exact integral cost.
    result = linprog(np.r_[np.zeros(n), extra], A_ub=matrix, b_ub=rhs,
        bounds=[(1., 1.)]*n + [(0., 1.)]*len(extra), method='highs',
        options={'threads': 1})
    assert result.success, result.message
    return result.fun


def test_nonadjacent_blockers_are_not_lost():
    # At port 1, all three upper boxes move; at port 2, the top box moves.
    assert fixed_graph_cost([], [1, 2, 2, 3]) == 8


def test_cover_and_internal_blocker_is_counted_once_per_port():
    # 2 current + 4 later hatch; 3 deck blockers at p1 and top deck box at p2.
    assert fixed_graph_cost([2, 1], [2, 2, 3]) == 14


def test_hold_internal_blocker_is_charged():
    assert fixed_graph_cost([1, 2], []) == 8  # 6 hatch + 2 restow
    assert fixed_graph_cost([2, 1], []) == 6


def test_box_cover_link_groups_fractional_candidate_slots():
    pages = [SimpleNamespace(deck=False, cover=0, slots=(0, 1))]
    pair = [(0, 0), (0, 1)]
    extra, rows, rhs, names = graph_rows(pair, {0:[(0, 0)], 1:[(0, 1)]}, pages,
        {0:1}, [1], [[0], [1]], [1], 2, hatch_now=2, hatch_later=2, restow=2)
    v = names['cover', 0]
    assert any(row.get(0) == 1. and row.get(1) == 1. and row.get(v) == -1. for row in rows)


def test_tail_respects_activation_of_already_fixed_hold():
    # Fixed hold cargo opens cover 0 at p1. A later deck box should choose cover 1.
    pages = [SimpleNamespace(deck=False, cover=0, slots=(0,)),
             SimpleNamespace(deck=True, cover=0, slots=(1,)),
             SimpleNamespace(deck=True, cover=1, slots=(2,))]
    pair = [(0, 0), (1, 1), (1, 2)]
    slot_page = {0:[(0, 0)], 1:[(1, 0)], 2:[(2, 0)]}
    extra, rows, rhs, names = graph_rows(pair, slot_page, pages, {0:1, 1:2},
        [1, 2], [[0], [1], [2]], [1, 1], 5,
        hatch_now=2, hatch_later=2, restow=2)
    ncols = 5+len(extra)
    ri, rj, data = [], [], []
    for i, row in enumerate(rows):
        for j, a in row.items():
            ri.append(i); rj.append(j); data.append(a)
    aub = coo_matrix((data, (ri, rj)), shape=(len(rows), ncols)).tocsc()
    a = Assignment.__new__(Assignment)
    a.pair, a.n, a.slot_page = pair, 3, slot_page
    a.T = SimpleNamespace(slot_cells=[[0], [1], [2]])
    a.m = SimpleNamespace(boxes=[0, 1])
    a.base_columns, a.hard_zero = 5, set()
    a._built = (np.r_[np.zeros(3), [1000, 1000], extra], aub, rhs, None, None)
    assert a.tail({0:0}, seconds=2.) == {0:0, 1:2}
