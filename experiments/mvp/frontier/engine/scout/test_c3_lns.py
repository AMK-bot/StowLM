# Provenance: copied from experiments/mvp/frontier/lab/c3/c2-layout/test_lns.py.
"""Synthetic regressions: fixed-charge closure, physical stacks and D15 ORs."""
from dataclasses import dataclass
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import lns


@dataclass(frozen=True)
class Pattern:
    page: int
    boxes: tuple
    cost: float = 0.


class Pool:
    def __init__(self, pods, pages, columns):
        self.pod = dict(enumerate(pods))
        self.ports = sorted(set(pods))
        self.boxes = list(range(len(pods)))
        self.pages = [SimpleNamespace(cover=c, deck=d, stacks=tuple(s), slots=tuple(range(n)), kind='20')
                      for c, d, s, n in pages]
        self.columns = [Pattern(p, tuple(b)) for p, b in columns]
        self.pool = {(c.page, c.boxes): i for i, c in enumerate(self.columns)}
        self.banned = set()
        self.layout, self.fixed_moment, self.lcg_target = {}, 0., None
        weights = SimpleNamespace(hatch_cover=1., future_hatch_cover=1.,
                                  future_restow=1., future_restow_lifts_per_box=2)
        self.p = SimpleNamespace(instance=SimpleNamespace(objective=weights))

    def add(self, j, boxes):
        key = j, tuple(boxes)
        if key in self.pool:
            return False
        self.pool[key] = len(self.columns)
        self.columns.append(Pattern(j, tuple(boxes)))
        return True

    def assignment(self, chosen):
        return {b: (self.columns[k].page, i) for k in chosen
                for i, b in enumerate(self.columns[k].boxes)}


def test_close_hold_keeps_deck_and_fixes_other_cover():
    # A early hold box moves to A deck: save all four A hatch lifts.
    m = Pool([1, 2], [('A', False, [0], 1), ('A', True, [1], 1),
                      ('B', False, [2], 1)], [(0, [0]), (2, [1])])
    result = lns.improve(m, [0, 1], ['A'], closed_hold=['A'], pricing_s=0, seconds=2)
    assert result['chosen'] is not None
    assert result['objective_lifts'] == 4
    assert result['assignment'][1] == (2, 0)  # B remains unchanged.
    assert result['assignment'][0] == (1, 0)  # A deck remains available.


def test_shared_stack_patterns_cannot_both_be_chosen():
    m = Pool([1, 1], [('A', True, [0], 1), ('A', True, [0], 1)],
             [(0, [0]), (1, [1])])
    with pytest.raises(ValueError, match='physical stack'):
        lns.improve(m, [0, 1], ['A'], pricing_s=0, seconds=2)


def test_page_and_cover_restow_union_is_not_double_counted():
    # Late deck box is above early deck AND early hold: it is one blocker.
    m = Pool([1, 1, 2], [('A', False, [0], 1), ('A', True, [1], 2)],
             [(0, [0]), (1, [1, 2])])
    assert lns.page_lift_cost(m, [0, 1]) == 6
    result = lns.improve(m, [0, 1], [], pricing_s=0, seconds=2)
    assert result['objective_lifts'] == 6


def test_cover_with_two_ports_costs_six_hatch_lifts():
    m = Pool([1, 2], [('A', False, [0], 2)], [(0, [1, 0])])
    result = lns.improve(m, [0], [], pricing_s=0, seconds=2)
    assert result['objective_lifts'] == 6


def test_multiport_box_blocked_once_at_each_earlier_call():
    m = Pool([1, 2, 3], [('A', False, [0], 2), ('A', True, [1], 1)],
             [(0, [1, 0]), (1, [2])])
    result = lns.improve(m, [0, 1], [], pricing_s=0, seconds=2)
    assert result['objective_lifts'] == 10  # six hatch + two off/on restows.


def test_outside_closure_rejected_and_deadline_is_respected():
    m = Pool([1], [('A', False, [0], 1)], [(0, [0])])
    with pytest.raises(ValueError, match='neighbourhood'):
        lns.improve(m, [0], [], closed_hold=['A'], seconds=0)
    assert lns.improve(m, [0], [], seconds=0)['status'] == 'DEADLINE'


def test_fractional_objective_weights_are_not_rounded():
    m = Pool([1], [('A', False, [0], 1)], [(0, [0])])
    m.p.instance.objective.hatch_cover = .125
    m.p.instance.objective.future_hatch_cover = .025
    result = lns.improve(m, [0], [], pricing_s=0, seconds=2)
    assert result['objective_lifts'] == .3


def test_pricing_releases_layout_and_restores_on_exception():
    m = Pool([1], [('A', False, [0], 1)], [(0, [0])])
    m.layout = {'A': 1}
    m.fixed_moment = 17.

    def fail(**kwargs):
        assert m.layout == {}
        raise RuntimeError('pricing failure')
    m.generate = fail
    with pytest.raises(RuntimeError, match='pricing failure'):
        lns.improve(m, [0], ['A'], seconds=2)
    assert m.layout == {'A': 1}
    assert m.fixed_moment == 17.


def test_hold_only_box_cannot_move_to_deck():
    m = Pool([1], [('A', False, [0], 1), ('A', True, [1], 1)], [(0, [0])])
    result = lns.improve(m, [0], ['A'], hold_only=[0], pricing_s=0, seconds=2)
    assert result['chosen'] is not None
    assert result['assignment'][0] == (0, 0)
    assert result['objective_lifts'] == 4
    # Closing its only available hold makes the restricted pool infeasible;
    # it must never silently disregard the requested hold-only restriction.
    blocked = lns.improve(m, [0], ['A'], closed_hold=['A'], hold_only=[0],
                          pricing_s=0, seconds=2)
    assert blocked['chosen'] is None
    assert blocked['status'] == 'INFEASIBLE'


def test_first_fit_seeds_closed_hold_neighbourhood_and_hints_complete_packing():
    m = Pool([1], [('A', False, [0], 1), ('A', True, [1], 1)], [(0, [0])])
    m.layout = {'A': 1}
    calls = []

    def fit(boxes, stacks, rng=None):
        assert m.layout == {}
        assert boxes == {0} and stacks == {1}
        calls.append(rng.random())
        m.add(1, [0])
        return [m.pool[1, (0,)]]
    m.first_fit = fit
    result = lns.improve(m, [0], ['A'], closed_hold=['A'], pricing_s=0, seconds=2)
    assert result['objective_lifts'] == 0
    assert result['greedy_seed_attempts'] == result['greedy_seed_complete'] == 3
    assert result['greedy_hint'] is True
    assert len(set(calls)) == 3
    assert m.layout == {'A': 1}


def test_exact_pattern_pair_nogoods_and_single_column_ban():
    m = Pool([1, 1], [('A', True, [0], 1), ('A', True, [1], 1)],
             [(0, [0]), (1, [1])])
    blocked = lns.improve(m, [0, 1], [], forbidden_pairs=[(0, 1)],
                          pricing_s=0, seconds=2)
    assert blocked['chosen'] is None and blocked['status'] == 'INFEASIBLE'
    assert blocked['forbidden_pairs_applied'] == 1
    singleton = lns.improve(m, [0, 1], [], forbidden_pairs=[(0, 0), (77, 88)],
                            pricing_s=0, seconds=2)
    assert singleton['chosen'] is None and singleton['status'] == 'INFEASIBLE'
    assert singleton['forbidden_pairs_applied'] == 1
    # Missing columns make a pair irrelevant; they must not ban its other end.
    absent = lns.improve(m, [0, 1], [], forbidden_pairs=[(0, 77)],
                         pricing_s=0, seconds=2)
    assert absent['chosen'] == [0, 1]
    assert absent['forbidden_pairs_applied'] == 0
