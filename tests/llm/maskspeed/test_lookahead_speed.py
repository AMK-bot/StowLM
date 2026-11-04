"""Copied production lookahead prefixes reuse exact results and detach safely."""
import pytest

from llm.lookahead.bounds import BoundResult
from llm.lookahead.mask_filter import LookaheadEngine
from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine


class CountingCapacity:
    """Deterministic test policy; no physical-feasibility claim."""
    def __init__(self):
        self.calls = 0

    def check(self, ex, *, native_contract):
        self.calls += 1
        return BoundResult('admit')


@pytest.fixture
def wrapper():
    instance = tiny_instance(719, n_load=2, n_other=0, p40=0)
    engine = make_feasibility_engine(instance, get_action_space(),
        config=FeasibilityConfig(extra={'global_screen': 0}))
    return LookaheadEngine(engine, capacity=CountingCapacity())


def test_prequery_copies_share_exact_results_and_independent_state(wrapper):
    sibling = wrapper.copy()
    legal = wrapper.legal_actions()
    calls = wrapper.capacity.calls
    assert calls > 1
    assert sibling.legal_actions() == legal and wrapper.capacity.calls == calls
    assert sibling._state_cache is wrapper._state_cache
    assert not sibling.legal_index().flags.writeable
    sibling.push(legal[0])
    assert sibling.depth == 1 and wrapper.depth == 0
    assert sibling._state_cache is not wrapper._state_cache
    sibling.legal_actions()
    assert wrapper.legal_actions() == legal
    sibling.pop()
    assert sibling.legal_actions() == legal
    wrapper.inner.push(legal[-1])
    assert wrapper.legal_actions() != legal
    assert sibling.legal_actions() == legal


def test_copy_reuses_geometry_without_constructor_or_mutable_aliases(wrapper, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('copy reconstructed static lookahead geometry')
    monkeypatch.setattr(LookaheadEngine, '__init__', forbidden)
    sibling = wrapper.copy()
    assert sibling.capacity is wrapper.capacity
    assert sibling._slot_index is wrapper._slot_index
    assert sibling._box_index is wrapper._box_index
    assert sibling.inner is not wrapper.inner and sibling.expander is not wrapper.expander
    sibling.metrics['deferred_reasons']['child'] = 1
    assert 'child' not in wrapper.metrics['deferred_reasons']
