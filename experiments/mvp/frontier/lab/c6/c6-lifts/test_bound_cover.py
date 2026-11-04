"""Arithmetic and independent end-frame controls for cover tension cuts."""
from fractions import Fraction
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from stowage.checker import lashing

spec = importlib.util.spec_from_file_location('c6_cover_bound_test', Path(__file__).with_name('bound_cover.py'))
bound = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bound)


def test_integer_coefficient_rounds_outward_with_mixed_signs():
    F = Fraction
    for tier in range(6):
        for mass in (2500., 17003.7, 30499.9):
            h, at, wind, lever = 2.896, 4.123, 31600., tier*2.591
            exact = F(mass)*F(at)*(F(lever)+F(.45)*F(h))+F(wind)*(F(lever)+F(h)/2)
            exact -= F(mass)*F(lashing.G)*F(lashing.WIDTH)/2
            if tier < 2:
                exact -= 300000*F(h)
            if tier == 0:
                exact -= 2*F(.85*lashing.LOCK_TENSION_SWL)*F(lashing.WIDTH)
            assert bound.tension_coefficient(mass, at, wind, lever, h, tier) <= exact


def test_chains_with_oog_end_share_and_different_heights_obey_projection():
    """Compare against both real end-response solves, including eccentric CG.

    This tests the new inequality's algebra independently of packing or any
    stored plan. Individual chains need not pass: the inequality must be no
    larger than the sum of their actual signed fitting-tension residuals.
    """
    masses = [17000., 13000., 3500., 3500.]
    heights = [2.591, 2.896, 2.591, 2.896]
    at, wind, dx, length = 4.1, 31600., .35, 12.192
    residual = 0.
    for end in range(2):
        share = .5+(-1 if end == 0 else 1)*dx/length
        trans = [m*at*abs(share) for m in masses]
        winds = [wind*abs(share) for _ in masses]
        _, rod, co, _, anchor = lashing._end_response(trans, winds, heights, 15.7e6,
                                                      cg_heights=[.45*h for h in heights])
        lever = 0.
        moment = 0.
        for t, wt, h in zip(trans, winds, heights):
            moment += t*(lever+.45*h)+wt*(lever+.5*h)
            lever += h
        moment -= min(rod*co, 150000.)*sum(heights[:anchor+1])
        gravity = sum(m*lashing.G*share/2 for m in masses)
        residual += moment-gravity*lashing.WIDTH-.85*lashing.LOCK_TENSION_SWL*lashing.WIDTH
    lower = sum(bound.tension_coefficient(m, at, wind, k*min(heights), h, k)
                for k, (m, h) in enumerate(zip(masses, heights)))
    assert lower <= residual+1.e-6


def test_accepted_normal_securing_columns_are_not_excluded():
    rows_spec = importlib.util.spec_from_file_location('c6_cover_rows_control', Path(__file__).with_name('rows.py'))
    rows = importlib.util.module_from_spec(rows_spec)
    rows_spec.loader.exec_module(rows)
    class Motion:
        def at(self, x, y, z):
            return .5, 4., 1.
    count = 0
    for n in range(2, 7):
        for mass in (3500., 7000., 13000.):
            heights = [2.591]*n
            A, Bt, Bl, _ = rows.stack_rows(Motion(), 0., 0., 10., n, heights)
            import numpy as np
            ratios = A@np.full(n, mass)+Bt@np.full(n, 31600.)+Bl@np.full(n, 6400.)
            if np.max(ratios) <= 1.:
                count += 1
                assert sum(bound.tension_coefficient(mass, 4., 31600., k*2.591, 2.591, k)
                           for k in range(n)) <= 0
    assert count >= 3


def test_witness_cache_is_current_call_only_and_restores_helpers(monkeypatch):
    instance, tables = object(), object()
    calls = []
    def witness(current, current_tables):
        assert current is instance and current_tables is tables
        calls.append(1)
        return {'box': ('cover',)}
    def other_witness(*args):
        raise AssertionError('second proof arm should reuse the fresh witness')
    def fake_old(current, **kwargs):
        assert bound.old.tiers._e6_witness_covers(current, tables) == {'box': ('cover',)}
        assert bound.old.ports.base._e6_witness_covers(current, tables) == {'box': ('cover',)}
        return {'lower_bound': 4.}
    monkeypatch.setattr(bound.old.tiers, '_e6_witness_covers', witness)
    monkeypatch.setattr(bound.old.ports.base, '_e6_witness_covers', other_witness)
    monkeypatch.setattr(bound.old, 'lower_bound', fake_old)
    for _ in range(2):
        row, witnesses = bound.fresh_old_with_witnesses(instance, tables, budget_s=1., seed=0)
        assert row['lower_bound'] == 4. and witnesses == {'box': ('cover',)}
        assert bound.old.tiers._e6_witness_covers is witness
        assert bound.old.ports.base._e6_witness_covers is other_witness
    assert len(calls) == 2


def test_negative_current_restow_weight_rejected_before_loading_tables():
    instance = SimpleNamespace(objective=SimpleNamespace(restow=-1.))
    with pytest.raises(ValueError, match='current-restow'):
        bound.lower_bound(instance, budget_s=1.)
