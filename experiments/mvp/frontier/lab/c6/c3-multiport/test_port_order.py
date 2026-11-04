"""Proof regressions for integral D15 union and conditional circle inequalities."""
from itertools import combinations, permutations, product
from collections import defaultdict
from pathlib import Path
import random
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent))
from port_order import (PortAssignment, build_port_rows, exact_closure,
                        forced_circle_cliques)


def solve_graph(pair, calls, support, tier, cover, deck, *, assignment=None,
                fractional=None, cliques=(), weights=(1., 1., 1., 2.)):
    import numpy as np
    from scipy.optimize import linprog
    from scipy.sparse import coo_matrix
    graph = build_port_rows(pair, calls, support, tier, cover, deck, start=len(pair),
                            hatch_now=weights[0], hatch_later=weights[1],
                            restow=weights[2], lifts_per_box=weights[3], cliques=cliques)
    c = np.zeros(graph.stop)
    for v, a in graph.cost.items():
        c[v] = a
    rr, cc, vv = [], [], []
    for i, (terms, _, _) in enumerate(graph.rows):
        for v, a in terms:
            rr.append(i); cc.append(v); vv.append(a)
    A = coo_matrix((vv, (rr, cc)), shape=(len(graph.rows), graph.stop)).tocsc()
    fixed = fractional if fractional is not None else [float(assignment[b] == q) for b, q in pair]
    bounds = [(z, z) for z in fixed]+[(0., 1.)]*(graph.stop-len(pair))
    result = linprog(c, A_ub=A, b_ub=[r[1] for r in graph.rows], bounds=bounds,
                     method='highs', options={'threads': 1})
    assert result.success, result.message
    return result, graph


def weighted(account, weights=(1., 1., 1., 2.)):
    return (weights[0]*account['hatch_now']+weights[1]*account['hatch_later']
            +weights[2]*weights[3]*account['restow_boxes'])


def test_same_call_targets_share_one_blocker_but_later_calls_repeat_it():
    calls = {0: 1, 1: 1, 2: 2, 3: 4}
    assignment = {b: b for b in calls}
    support = {b: (0,) for b in calls}
    tier = {b: 82+2*b for b in calls}
    result = exact_closure(calls, assignment, support, tier, [None], [True])
    assert result['restow_boxes'] == 3
    assert result['calls'][0]['blockers'] == [2, 3]
    assert result['calls'][1]['blockers'] == [3]
    lp, _ = solve_graph(list(assignment.items()), calls, support, tier, [None], [True], assignment=assignment)
    assert abs(lp.fun-6.) < 1e-8


def bridge_case():
    calls = {0: 1, 1: 2, 2: 4, 3: 3, 4: 4}
    assignment = {b: b for b in calls}
    support = {0: (0,), 1: (1,), 2: (2, 3), 3: (3,), 4: (4,)}
    tier = {0: 2, 1: 2, 2: 82, 3: 84, 4: 82}
    cover, deck = ['A', 'B', 'A', 'B', 'B'], [False, False, True, True, True]
    return calls, assignment, support, tier, cover, deck


def test_cross_cover_long_bridge_propagates_without_opening_unneeded_cover():
    calls, assignment, support, tier, cover, deck = bridge_case()
    result = exact_closure(calls, assignment, support, tier, cover, deck)
    assert result['calls'][0]['opened'] == ['A']
    assert result['calls'][0]['blockers'] == [2, 3]
    assert result['calls'][1]['blockers'] == [2, 3, 4]
    assert result['restow_boxes'] == 5
    lp, _ = solve_graph(list(assignment.items()), calls, support, tier, cover, deck, assignment=assignment)
    assert abs(lp.fun-weighted(result)) < 1e-8


def test_hold_long_target_opens_both_covers_and_deck_long_counts_once():
    calls, assignment = {0: 1, 1: 4}, {0: 0, 1: 1}
    support, tier = {0: (0, 1), 1: (2, 3)}, {0: 2, 1: 82}
    cover, deck = ['A', 'B', 'A', 'B'], [False, False, True, True]
    result = exact_closure(calls, assignment, support, tier, cover, deck)
    assert result['hatch_now'] == 4
    assert result['hatch_later'] == 4
    assert result['restow_boxes'] == 1
    lp, _ = solve_graph(list(assignment.items()), calls, support, tier, cover, deck, assignment=assignment)
    assert abs(lp.fun-10.) < 1e-8


def test_departed_long_box_stops_bridging_columns():
    calls = {0: 2, 1: 1, 2: 3}
    assignment = {b: b for b in calls}
    support, tier = {0: (0,), 1: (0, 1), 2: (1,)}, {0: 82, 1: 84, 2: 86}
    result = exact_closure(calls, assignment, support, tier, [None, None], [True, True])
    assert result['calls'][0]['blockers'] == [2]
    assert result['calls'][1]['blockers'] == []
    lp, _ = solve_graph(list(assignment.items()), calls, support, tier, [None, None], [True, True], assignment=assignment)
    assert abs(lp.fun-2.) < 1e-8


def test_integral_lp_equals_closure_exhaustively_with_nonunit_weights():
    support = {q: (0,) for q in range(3)}
    tier = {q: 82+2*q for q in range(3)}
    pair = list(product(range(3), range(3)))
    weights = (.7, 1.3, 2.1, 3.)
    for destinations in product((1, 2, 3), repeat=3):
        calls = dict(enumerate(destinations))
        for positions in permutations(range(3)):
            assignment = dict(enumerate(positions))
            expected = exact_closure(calls, assignment, support, tier, [None], [True])
            lp, _ = solve_graph(pair, calls, support, tier, [None], [True], assignment=assignment, weights=weights)
            assert abs(lp.fun-weighted(expected, weights)) < 1e-7


def test_circles_require_actual_strict_rank_gap_not_raw_mass_or_ties():
    boxes, calls = [0, 1, 2], {0: 1, 1: 2, 2: 3}
    assert forced_circle_cliques(boxes, {0: 1, 1: 1, 2: 1}, calls) == []
    assert forced_circle_cliques(boxes, {0: 3, 1: 2, 2: 1}, calls, tolerance=1.) == [(0, 2)]
    assert forced_circle_cliques(boxes, {0: 3, 1: 2, 2: 1}, calls, tolerance=0.) == [(0, 1, 2)]


def test_circle_clique_size_matches_bruteforce_strict_conflict_subsets():
    rng = random.Random(6173)
    boxes = list(range(6))
    for _ in range(30):
        rank = {b: rng.randrange(4) for b in boxes}
        calls = {b: rng.randrange(1, 6) for b in boxes}
        tol = rng.choice((0., 1.))
        expected = 1
        for n in range(2, len(boxes)+1):
            for subset in combinations(boxes, n):
                ordered = sorted(subset, key=lambda b: -rank[b])
                if all(rank[a] > rank[b]+tol and calls[a] < calls[b] for a, b in combinations(ordered, 2)):
                    expected = max(expected, n)
        found = forced_circle_cliques(boxes, rank, calls, tol)
        assert max(map(len, found), default=1) == expected


def test_circle_clique_cuts_weak_fractional_or_point_without_changing_integer_cost():
    calls, support, tier = {0: 1, 1: 2}, {0: (0,), 1: (0,)}, {0: 82, 1: 84}
    pair = list(product(range(2), range(2)))
    fractional = [.5]*4
    uncut, _ = solve_graph(pair, calls, support, tier, [None], [True], fractional=fractional)
    cut, _ = solve_graph(pair, calls, support, tier, [None], [True], fractional=fractional, cliques=[(0, 1)])
    assert abs(uncut.fun) < 1e-8
    assert abs(cut.fun-2.) < 1e-8
    integer, _ = solve_graph(pair, calls, support, tier, [None], [True], assignment={0: 0, 1: 1}, cliques=[(0, 1)])
    assert abs(integer.fun-2.) < 1e-8


def test_free_single_load_port_sequence_has_zero_restows_when_sorted():
    calls = {b: b+1 for b in range(6)}
    support, tier = {q: (0,) for q in range(6)}, {q: 82+2*q for q in range(6)}
    assignment = {b: 5-b for b in range(6)}
    assert exact_closure(calls, assignment, support, tier, [None], [True])['restow_boxes'] == 0
    assert forced_circle_cliques(list(calls), {b: 0 for b in calls}, calls) == []


def test_tail_keeps_fixed_only_and_auxiliary_dependencies():
    import numpy as np
    from scipy.sparse import coo_matrix
    solver = PortAssignment.__new__(PortAssignment)
    solver.n, solver.pair, solver.base_columns = 1, [(0, 0)], 2
    solver.m = NS(boxes=[0])
    solver.T = NS(slot_cells=np.array([[0, -1]]))
    graph = build_port_rows([(0, 0)], {0: 1}, {0: (0,)}, {0: 82}, [None], [True], start=2)
    solver.port_graph = graph
    r = graph.required[0, 1]
    rows = list(graph.rows)+[(((r, 1.),), 0., 'deliberate_aux_contradiction')]
    rr, cc, vv = [], [], []
    for i, (terms, _, _) in enumerate(rows):
        for v, a in terms:
            rr.append(i); cc.append(v); vv.append(a)
    A = coo_matrix((vv, (rr, cc)), shape=(len(rows), graph.stop)).tocsc()
    c = np.zeros(graph.stop)
    for v, a in graph.cost.items():
        c[v] = a
    solver._built = (c, A, np.array([r[1] for r in rows]), None, None)
    assert solver.tail({0: 0}, seconds=.2) is None


def test_independent_closure_matches_retained_c3_exact_graph():
    import importlib.util
    path = Path(__file__).resolve().parents[2] / 'c3/c3-multiport/graph_model.py'
    spec = importlib.util.spec_from_file_location('c3_closure_reference', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls, assignment, support, tier, cover, deck = bridge_case()
    on_stack = defaultdict(list)
    for b, q in assignment.items():
        for j in support[q]:
            on_stack[j].append((tier[q], b))
    stacks = {j: [b for _, b in sorted(items)] for j, items in on_stack.items()}
    old = module.closure_counts(calls, stacks,
                                {j: h for j, h in enumerate(cover) if not deck[j]},
                                {j: h for j, h in enumerate(cover) if deck[j]})
    new = exact_closure(calls, assignment, support, tier, cover, deck)
    assert [(r['call'], r['blockers'], r['covers']) for r in old] == [
        (r['call'], r['blockers'], r['opened']) for r in new['calls']]
