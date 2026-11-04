"""P2: expired or unfinished LPs supply neither candidates nor proofs."""
from types import SimpleNamespace as NS

import numpy as np
import pytest

from . import method, pages, pages_base, pages_fast_dense
from .test_moments import example


MASTERS = (pages_fast_dense, pages_base, pages)


def result(status=0):
    return NS(status=status, message='limit' if status else 'optimal', fun=3.,
              x=np.array([.5, .5, 0., 0.]),
              eqlin=NS(marginals=np.array([1., 2.])),
              ineqlin=NS(marginals=np.array([0., 0.])))


def capture(monkeypatch, module, status=0):
    calls = []
    def solver(*args, **kwargs):
        calls.append(kwargs)
        return result(status)
    monkeypatch.setattr(module, '_linprog' if module is pages_fast_dense else 'linprog', solver)
    return calls


def solve(module, model, **kwargs):
    return module.Model.solve_lp(model, active_boxes=set(model.boxes),
                                 active_stacks=set(model.stacks), hold_full=False, phase=1, **kwargs)


@pytest.mark.parametrize('module', MASTERS)
def test_expired_master_never_starts(monkeypatch, module):
    calls = capture(monkeypatch, module)
    monkeypatch.setattr(module, 'monotonic', lambda: 100.)
    with pytest.raises(TimeoutError, match='unknown'):
        solve(module, example(), deadline=25.)
    assert calls == []


@pytest.mark.parametrize('module', MASTERS)
def test_live_deadline_preserves_optimal_result_and_limits_highs(monkeypatch, module):
    calls = capture(monkeypatch, module)
    monkeypatch.setattr(module, 'monotonic', lambda: 100.)
    uncapped = solve(module, example())
    capped = solve(module, example(), deadline=125.)
    assert calls[0]['options'] == {'threads': 1}
    assert calls[1]['options'] == {'threads': 1, 'time_limit': 25.}
    assert capped[:2] == uncapped[:2]
    np.testing.assert_array_equal(capped[2], uncapped[2])
    assert capped[3:] == uncapped[3:]


@pytest.mark.parametrize('module', MASTERS)
def test_construction_expiry_never_starts_highs(monkeypatch, module):
    calls = capture(monkeypatch, module)
    clock = iter((100., 126.))
    monkeypatch.setattr(module, 'monotonic', lambda: next(clock))
    with pytest.raises(TimeoutError, match='unknown'):
        solve(module, example(), deadline=125.)
    assert calls == []


@pytest.mark.parametrize('module', MASTERS)
def test_highs_timeout_is_unknown_even_with_incumbent_x(monkeypatch, module):
    calls = capture(monkeypatch, module, status=1)
    monkeypatch.setattr(module, 'monotonic', lambda: 100.)
    with pytest.raises(TimeoutError, match='unknown'):
        solve(module, example(), deadline=125.)
    assert len(calls) == 1


def test_review_generate_probe_expired_25_now_100_never_calls_linprog(monkeypatch):
    calls = capture(monkeypatch, pages_fast_dense)
    monkeypatch.setattr(pages_fast_dense, 'monotonic', lambda: 100.)
    model = object.__new__(pages_fast_dense.Model)
    model.__dict__.update(example().__dict__)
    with pytest.raises(TimeoutError, match='unknown'):
        pages_fast_dense.Model.generate(model, active_boxes=set(model.boxes),
                                       active_stacks=set(model.stacks), deadline=25.)
    assert calls == []


def test_cover_first_does_not_dive_after_unknown_master(monkeypatch):
    monkeypatch.setattr(method, 'monotonic', lambda: 100.)
    class Fast:
        boxes, stacks = (0,), (0,)
        def __init__(self, problem):
            pass
        def first_fit(self, *args):
            return None
        def generate(self, **kwargs):
            raise TimeoutError('master LP time limit; unknown')
        def dive(self, **kwargs):
            pytest.fail('unknown master must not supply a fallback candidate')
    assert method.cover_first(None, None, None, 0, runtime=NS(FastPages=Fast), deadline=200.) is None

