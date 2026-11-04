"""Exact securing-cache and reviewed-source regressions (CPU only)."""
from types import SimpleNamespace

import pytest

from llm.lookahead.bounds import BoundResult
from llm.lookahead.mask_filter import LookaheadEngine
from llm.securing.mask_filter import SecuringEngine
from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.solver.securing_screen import SecuringScreenResult


class CountingScreen:
    def __init__(self):
        self.metrics = {}
        self.placements = 0
        self.terminals = 0

    def check_placement(self, expander, action):
        self.placements += 1
        return SecuringScreenResult(True, 0.)

    def check_state(self, expander):
        self.terminals += 1
        return SecuringScreenResult(True, 0.)


@pytest.fixture
def base():
    return make_feasibility_engine(tiny_instance(719, n_load=3, n_other=0, p40=0),
        get_action_space('boxslot'), config=FeasibilityConfig(extra={'global_screen': 0}))


def test_copies_share_first_lazy_query_and_detach_on_push(base):
    screen = CountingScreen()
    root = SecuringEngine(base, screen=screen)
    siblings = [root.copy() for _ in range(7)]
    expected = root.legal_actions()
    queries = screen.placements
    assert queries > 0
    for child in siblings:
        assert child.legal_actions() == expected
        assert child.legal_index() is root.legal_index()
    assert screen.placements == queries
    child = siblings[0]
    child.push(expected[0])
    assert expected[0] not in child.legal_actions()
    assert root.legal_actions() == expected
    assert all(sibling.legal_actions() == expected for sibling in siblings[1:])
    assert not root.legal_index().flags.writeable


def test_pop_restores_prefix_without_expander_replay(base, monkeypatch):
    root = SecuringEngine(base, screen=CountingScreen())
    initial = root.legal_index()
    initial_where = dict(root.expander.where)
    first = root.legal_actions()[0]
    root.push(first)
    root.legal_actions()
    child = root.copy()

    def no_replay(actions):
        pytest.fail('owned pop replayed an already saved prefix')

    monkeypatch.setattr(root, '_replay', no_replay)
    assert root.pop() == first
    assert root.expander.where == initial_where
    assert root.legal_index() is initial
    assert child.depth == 1 and root.depth == 0
    assert child.pop() == first
    assert child.legal_index() is initial


def test_external_inner_mutation_detaches_shared_lazy_cache(base):
    root = SecuringEngine(base, screen=CountingScreen())
    sibling = root.copy()
    first = root.legal_actions()[0]
    base.push(first)
    assert first not in root.legal_actions()
    assert first in sibling.legal_actions()
    assert root.depth == 1 and sibling.depth == 0
    assert root._cache is not sibling._cache


def test_custom_proxy_still_intersects_secondary_interfaces(base):
    denied = base.legal_actions()[0]

    class Proxy:
        def __getattr__(self, name):
            return getattr(base, name)

        def is_legal(self, action):
            return action != denied and base.is_legal(action)

    wrapped = SecuringEngine(Proxy(), screen=CountingScreen())
    assert denied not in wrapped.legal_actions()
    assert not wrapped.is_legal(denied)


def test_reviewed_lookahead_source_avoids_per_candidate_sync(base, monkeypatch):
    capacity = SimpleNamespace(check=lambda ex, **kwargs: BoundResult('unknown', 'test'),
                               boxes={b.id: b for b in base.instance.containers})
    inner = LookaheadEngine(base, capacity=capacity)
    expected = inner.legal_actions()

    def no_member_recheck(action):
        pytest.fail('consistent LookaheadEngine source rechecked each member')

    monkeypatch.setattr(inner, 'is_legal', no_member_recheck)
    assert SecuringEngine(inner, screen=CountingScreen()).legal_actions() == expected


def test_lookahead_subclass_retains_defensive_member_check(base):
    denied = base.legal_actions()[0]
    capacity = SimpleNamespace(check=lambda ex, **kwargs: BoundResult('unknown', 'test'),
                               boxes={b.id: b for b in base.instance.containers})

    class RestrictedLookahead(LookaheadEngine):
        def is_legal(self, action):
            return action != denied and super().is_legal(action)

    inner = RestrictedLookahead(base, capacity=capacity)
    assert denied not in SecuringEngine(inner, screen=CountingScreen()).legal_actions()


def test_terminal_screen_result_shared_by_lazy_copies(base):
    while not base.is_complete():
        base.push(base.legal_actions()[0])
    screen = CountingScreen()
    wrapped = SecuringEngine(base, screen=screen)
    child = wrapped.copy()
    assert wrapped.is_complete() and child.is_complete()
    assert screen.terminals == 1

