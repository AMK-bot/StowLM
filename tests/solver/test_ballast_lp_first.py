"""The conservative construction LP is attempted before discrete tank search."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.solver import ballast


def _run(monkeypatch, *, status='optimal', trim=None, passing=False):
    import stowage.linear.model as linear
    import stowage.linear.bands as bands
    import stowage.solver.ballast_physics as physics
    import stowage.solver.ballast_pressed as pressed
    import stowage.solver.ballast_proof as proof

    instance = build_instance()
    if trim is not None:
        instance.vessel.hull.max_trim_m = trim
    if passing:
        from stowage.model.voyage import VoyageSpec, VoyageTankPolicy
        instance.vessel.voyage_policy = VoyageTankPolicy(
            groups=[dict(id='fuel', kind='fuel_oil', tank_ids=['FO-1'])], drawdown_order=['FO-1'])
        instance.voyage = VoyageSpec.model_validate(dict(
            departure=dict(id='d', phase='departure', call_index=0, locode='THLCH',
                           environment_id='d', density_id='d', water_density_kg_m3=1025.),
            arrival=dict(id='a', phase='arrival', call_index=1, locode='THBKK',
                         environment_id='a', density_id='a', water_density_kg_m3=1025.)))
    plan = MovePlan(id='lp-order', instance_id=instance.id, call_index=instance.call_index)
    tanks = sorted((t for t in instance.vessel.tanks if t.adjustable), key=lambda t:t.id)
    masses = {t.id: .2*t.capacity_m3*t.content_density_kg_m3 for t in tanks}
    events = []
    monkeypatch.setattr(proof, 'prove_infeasible', lambda *a, **kw: None)
    monkeypatch.setattr(physics, 'ExactContext', lambda *a, **kw: events.append('context'))
    def band(*a, **kw):
        events.append('band')
        return 'band'
    monkeypatch.setattr(bands, 'band_for_instance', band)
    monkeypatch.setattr(linear, 'build_linear_model', lambda *a, **kw: 'model')
    def solve(model, ship, inst, *, direction, margin_scale):
        assert direction == 'conservative'
        assert margin_scale == 1.001
        events.append('lp')
        return SimpleNamespace(status=status, tank_masses_kg=masses)
    monkeypatch.setattr(linear, 'solve_ballast_lp', solve)
    def pressed_search(*args):
        events.append('pressed')
        return []
    monkeypatch.setattr(pressed, 'search', pressed_search)
    def successive(fills, evaluate, *args, **kw):
        events.append('successive')
        if status == 'optimal':
            assert np.allclose(fills[0], .2)
    monkeypatch.setattr(ballast, 'search', successive)
    # Isolate orchestration, so a local LP is never mistaken for certification.
    monkeypatch.setattr(ballast, '_normal_margin', lambda *a: -1.)
    for name in ('stability', 'strength', 'visibility', 'lashing'):
        module = __import__('stowage.checker.'+name, fromlist=['check_'+name])
        from stowage.model import PartReport
        monkeypatch.setattr(module, 'check_'+name,
                            lambda *a, _name=name, **kw: PartReport(
                                name=_name, ran=passing, error=None if passing else 'test unavailable'))
    result = ballast._finish_ballast(instance, plan, time_limit_s=1.)
    return result, events


def test_conservative_lp_precedes_pressed_search_and_supplies_first_seed(monkeypatch):
    result, events = _run(monkeypatch)
    assert events.index('lp') < events.index('pressed'), events
    assert result.status == 'not_found'  # An LP result alone is never approval.


def test_rejected_local_lp_still_runs_exact_search_without_claiming_infeasible(monkeypatch):
    result, events = _run(monkeypatch, status='infeasible')
    assert events.index('lp') < events.index('pressed'), events
    assert 'successive' in events
    assert result.status == 'not_found'


def test_exactly_passing_initial_fill_does_not_pay_for_repair_context(monkeypatch):
    result, events = _run(monkeypatch, status='infeasible', passing=True)
    assert result.status == 'ok'
    assert 'lp' in events
    assert 'context' not in events
    assert 'pressed' not in events
    assert len(result.parts) == 6
