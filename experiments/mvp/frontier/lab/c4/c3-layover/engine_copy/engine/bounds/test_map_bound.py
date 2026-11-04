"""Only certified bounds compete; a stronger old certificate is retained."""
from importlib import import_module

import pytest

from . import hatch, hatch_restow
from . import map_bound
bound_module = import_module(__package__)


def _result(cost, **kw):
    return hatch.BoundResult(cost, cost, 0, 'optimal', {'b': str(cost)}, .1, 0, **kw)


@pytest.mark.parametrize('old,new,expected', [(8., 9., 9.), (9., 8., 9.)])
def test_map_bound_retains_stronger_certificate(monkeypatch, old, new, expected):
    calls = []
    def solve(cost, label):
        def run(instance, **kw):
            calls.append((label, kw['budget_s']))
            return _result(cost)
        return run
    monkeypatch.setattr(bound_module, 'lower_bound', solve(old, 'hatch'))
    monkeypatch.setattr(hatch_restow, 'lower_bound', solve(new, 'restow'))
    cost, record = map_bound(object(), budget_s=8.)
    assert cost == expected
    assert record['relaxation_assignment'] == {'b': str(expected)}
    assert calls == [('hatch', 2.), ('restow', 6.)]
    assert record['hatch_only_lower_bound'] == old
    assert record['hatch_restow_lower_bound'] == new
    assert record['scope'] == 'map-wide weighted lifts'


def test_restow_ablation_runs_unchanged_hatch_bound(monkeypatch):
    calls = []
    monkeypatch.setattr(bound_module, 'lower_bound',
                        lambda *a, **kw: calls.append(kw['budget_s']) or _result(3.))
    def forbidden(*a, **kw):
        raise AssertionError('restow stage disabled')
    monkeypatch.setattr(hatch_restow, 'lower_bound', forbidden)
    cost, record = map_bound(object(), budget_s=4., restows=False)
    assert cost == 3. and calls == [4.]
    assert record['restow_closure'] is False
    assert 'hatch_restow_lower_bound' not in record
