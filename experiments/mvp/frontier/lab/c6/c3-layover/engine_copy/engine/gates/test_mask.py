"""Native differential and explicit unsupported-domain mask regressions."""
import importlib
from types import SimpleNamespace

import pytest

from alns import Problem, _mask_ok
from stowage.actions.space import Place
from stowage.rules.catalogue import rule_freeze_v1
from ..bounds.test_hatch import fixture
from .mask import PrefixMask, bind
from . import mask as module


def _case():
    problem = Problem(fixture())
    actions = tuple(Place(box, slot) for box, slot in zip(problem.instance.load_list,
                        ['0010102', '0010104', '0010182']))
    return problem, actions


def test_cached_mask_matches_native_and_leaves_root_unchanged():
    problem, original = _case()
    oracle = PrefixMask(problem)
    root = problem.engine._cur
    wrong = list(original)
    wrong[-1] = Place(wrong[-1].box, '9999999')
    candidates = [original, tuple(reversed(original)), tuple(wrong),
                  original, tuple(wrong), original[:-1], original+(original[0],)]
    with rule_freeze_v1(enabled=False):
        for actions in candidates:
            assert oracle(actions) == _mask_ok(problem, actions)
            assert problem.engine.depth == 0 and problem.engine._cur is root
            assert oracle.engine.depth == 0
    assert oracle.stats['fallback'] == 2
    assert oracle.stats['prefix_hits'] + oracle.stats['rejected_hits'] > 0


def test_unsupported_context_falls_back_to_native(monkeypatch):
    problem, actions = _case()
    oracle = PrefixMask(problem)
    calls = []
    monkeypatch.setattr(module, '_mask_ok', lambda p, a: calls.append((p, a)) or True)
    with rule_freeze_v1(enabled=True):
        assert not oracle.supported(actions)
        assert oracle(actions)
    assert calls == [(problem, actions)]
    # Config and engine identities are part of the differential-tested domain.
    problem.engine = SimpleNamespace()
    assert not oracle.supported(actions)
    assert oracle(actions)
    assert len(calls) == 2


def test_binding_restores_all_search_aliases_on_exception(monkeypatch):
    native = module._mask_ok
    fake = lambda *a: True
    monkeypatch.setattr(module, 'PrefixMask', lambda *a, **k: fake)
    package = __package__.rsplit('.', 1)[0]
    consumers = [importlib.import_module(name) for name in
                 ['alns', package+'.portfolio', package+'.vlsn.search',
                  package+'.vlsn.lazy_fusion', package+'.vlsn.recombine']]
    originals = [m._mask_ok for m in consumers]
    with pytest.raises(RuntimeError, match='exit'):
        with bind(object()) as oracle:
            assert oracle is fake
            assert all(m._mask_ok is fake for m, old in zip(consumers, originals)
                       if old is native)
            raise RuntimeError('exit')
    assert [m._mask_ok for m in consumers] == originals


@pytest.mark.parametrize('unsupported', ['placements', 'covers_off', 'plan_calls', 'forecasts'])
def test_unmeasured_loading_families_use_native_fallback(monkeypatch, unsupported):
    problem, actions = _case()
    instance = problem.instance
    if unsupported in ('placements', 'covers_off'):
        onboard = instance.onboard.model_copy(update={unsupported:
            [object()] if unsupported == 'placements' else {'cover': 'quay'}})
        problem.instance = instance.model_copy(update={'onboard': onboard})
    else:
        problem.instance = instance.model_copy(update={unsupported: [object()]})
    oracle = PrefixMask(problem)
    calls = []
    monkeypatch.setattr(module, '_mask_ok', lambda p, a: calls.append((p, a)) or True)
    assert not oracle.supported(actions)
    assert oracle(actions)
    assert calls == [(problem, actions)]


@pytest.mark.parametrize('changed', ['instance', 'tables'])
def test_changed_problem_identity_uses_native_fallback(monkeypatch, changed):
    problem, actions = _case()
    oracle = PrefixMask(problem)
    if changed == 'instance':
        problem.instance = problem.instance.model_copy()
    else:
        problem.engine.T = object()
    monkeypatch.setattr(module, '_mask_ok', lambda p, a: True)
    assert not oracle.supported(actions)
    assert oracle(actions)
