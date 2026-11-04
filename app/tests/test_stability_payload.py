"""Stability display must use the same liquid condition and limits as checking."""
from math import radians
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from app.stability import _criteria, _gz_curve, stability_payload
from stowage.checker.stability_loading import curve_correction, loading_condition
from stowage.model import PartReport, ShipState, Violation
from stowage.vessel.tanks import build_tanks


@pytest.mark.parametrize('method,length,table_used', [(1,.75,False), (1,1.25,True), (2,.75,False), (2,1.25,False)])
def test_gz_reconstruction_preserves_checker_liquid_method_and_exemption(
        replay_case, monkeypatch, method, length, table_used):
    instance, _ = replay_case
    instance.vessel.hull.lightship_mass_kg = 14_400_000.
    raw = dict(id='display-ballast',kind='ballast',content_density_kg_m3=1000.,capacity_m3=24.*length,
        x_aft_m=10.,x_fore_m=10.+length,geometry=dict(x_min_m=10.,x_max_m=10.+length,
        y_min_m=-6.,y_max_m=6.,z_min_m=0.,z_max_m=2.))
    params = {'tanks':[raw]}
    instance.vessel.tanks = build_tanks(params)
    ship = ShipState(vessel_id=instance.vessel.id,tank_masses_kg={'display-ballast':12_000.*length})
    expected = loading_condition(instance,ship,ship_params=params,free_surface_method=method,
                                 precomputed_soundings=True,small_tank_exemption=True)
    geometry = SimpleNamespace(_stability_vessel_hash='test-vessel',_stability_liquid_metadata={'test':'metadata'})
    monkeypatch.setattr('stowage.checker.stability._context',lambda *_:(geometry,params))
    attached = []
    def attach(condition,actual_params,**kwargs):
        assert actual_params is params
        attached.append(kwargs)
        return replace(condition,liquid_tables={'synthetic-table':object()})
    monkeypatch.setattr('stowage.checker.stability_liquid.attach_tables',attach)
    def build(actual_geometry,condition,density,**kwargs):
        assert actual_geometry is geometry
        assert condition.precomputed_soundings is True
        assert condition.omitted_tanks == expected.omitted_tanks
        assert condition.fsc_m == pytest.approx(expected.fsc_m)
        assert kwargs.get('free_surface_method') == method
        assert kwargs['initial_drafts'] == (2.,2.)
        angles=np.array([0.,radians(50.)])
        correction=kwargs['correction_solver'](condition,angles,0.)
        np.testing.assert_allclose(correction,curve_correction(expected,angles,0.,method=method))
        return SimpleNamespace(angles=angles,gz=1.-correction)
    monkeypatch.setattr('stowage.checker.stability_curve.build_stability_curve',build)
    # Replaying the panel after an environment switch must retain the checked method.
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD',str(3-method))
    part=PartReport(name='stability',metrics={'free_surface_method':float(method),
        'liquid_table_used':float(table_used),'draught_aft_m':2.,'draught_fwd_m':2.})
    points=_gz_curve(instance,ship,part)
    assert len(points)==2
    assert bool(attached) is table_used


def test_loading_gm_failure_keeps_separate_code_pass(replay_case):
    instance,_=replay_case
    part=PartReport(name='stability',metrics={'gm_m':.2},margins={'gm_m':-.1},violations=[
        Violation(rule_id='iscode.loading.gm',severity='hard',call_index=0,
                  measured=.2,limit=.3,unit='m',message='Loading GM below vessel minimum.')])
    panel=stability_payload(instance,instance.onboard,part)
    code=next(c for c in panel['criteria'] if c['rule_id']=='iscode.a2_2.initial_gm')
    loading=next(c for c in panel['criteria'] if c['rule_id']=='iscode.loading.gm')
    assert (code['measured'],code['limit'],code['passed'])==(.2,.15,True)
    assert loading['measured']==.2
    assert loading['limit']==pytest.approx(.3)
    assert loading['margin']==-.1
    assert loading['passed'] is False


@pytest.mark.parametrize('prefix',['','containership_'])
def test_downflooding_criterion_uses_checked_coverage_end(prefix):
    part=PartReport(name='stability',metrics={'angle_flooding_rad':radians(50.),
        'curve_max_heel_rad':radians(25.)},margins={prefix+'downflooding_30_margin_rad':radians(-5.)})
    row=next(c for c in _criteria(part) if c['key']==prefix+'downflooding_30_margin_rad')
    assert row['measured']==pytest.approx(radians(25.))
    assert row['limit']==pytest.approx(radians(30.))
    assert row['margin']==pytest.approx(radians(-5.))
    assert row['unit']=='rad'
    assert row['passed'] is False


def test_curve_cannot_substitute_direct_geometry_for_missing_checked_table(replay_case,monkeypatch):
    instance,_=replay_case
    monkeypatch.setattr('stowage.checker.stability._context',lambda *_:(object(),{}))
    monkeypatch.setattr('stowage.checker.stability_tables.cached_geometry',lambda *_:None)
    part=PartReport(name='stability',metrics={'free_surface_method':1.,'geometry_table_used':1.,
        'draught_aft_m':2.,'draught_fwd_m':2.})
    panel=stability_payload(instance,ShipState(vessel_id=instance.vessel.id),part)
    assert panel['gz_curve']==[]
    assert 'Geometry table used by checker is no longer available' in panel['curve_error']
    assert panel['metrics']==part.metrics


def test_curve_cannot_substitute_direct_liquid_for_missing_checked_table(replay_case,monkeypatch):
    instance,_=replay_case
    monkeypatch.setattr('stowage.checker.stability._context',lambda *_:(object(),{}))
    monkeypatch.setattr('stowage.checker.stability_liquid.attach_tables',lambda condition,*_,**__:condition)
    part=PartReport(name='stability',metrics={'free_surface_method':1.,'liquid_table_used':1.,
        'draught_aft_m':2.,'draught_fwd_m':2.})
    panel=stability_payload(instance,ShipState(vessel_id=instance.vessel.id),part)
    assert panel['gz_curve']==[]
    assert 'Liquid tables used by checker are no longer available' in panel['curve_error']


def test_curve_without_checked_method_is_unavailable(replay_case):
    instance,_=replay_case
    part=PartReport(name='stability',metrics={'draught_aft_m':2.,'draught_fwd_m':2.})
    panel=stability_payload(instance,instance.onboard,part)
    assert panel['gz_curve']==[]
    assert 'Checked free-surface method is unavailable' in panel['curve_error']


def test_gz_reconstruction_keeps_recorded_geometry_table(replay_case,monkeypatch):
    instance,_=replay_case
    geometry,accelerated=object(),object()
    monkeypatch.setattr('stowage.checker.stability._context',lambda *_:(geometry,{}))
    monkeypatch.setattr('stowage.checker.stability_tables.cached_geometry',lambda *args:accelerated)
    def build(actual_geometry,condition,density,**kwargs):
        assert actual_geometry is accelerated
        assert kwargs['free_surface_method']==2
        return SimpleNamespace(angles=[0.,.5],gz=[0.,.4])
    monkeypatch.setattr('stowage.checker.stability_curve.build_stability_curve',build)
    part=PartReport(name='stability',metrics={'free_surface_method':2.,'geometry_table_used':1.,
        'draught_aft_m':2.,'draught_fwd_m':2.})
    assert _gz_curve(instance,ShipState(vessel_id=instance.vessel.id),part)==[
        {'heel_rad':0.,'gz_m':0.},{'heel_rad':.5,'gz_m':.4}]


def test_unavailable_stability_has_no_positive_criterion(replay_case):
    instance,_=replay_case
    part=PartReport(name='stability',ran=False,error='Unavailable evidence',metrics={'gm_m':.2},margins={'gm_m':.05})
    panel=stability_payload(instance,instance.onboard,part)
    assert panel['ran'] is False
    assert all(c['passed'] is None for c in panel['criteria'])
    assert panel['gz_curve']==[]


def test_tightened_code_gm_and_loading_gm_keep_independent_limits(replay_case):
    instance,_=replay_case
    instance.rule_overrides['iscode.a2_2.initial_gm']={'minimum':.25}
    part=PartReport(name='stability',metrics={'gm_m':.2},margins={'gm_m':-.1},violations=[
        Violation(rule_id=rule,severity='hard',call_index=0,measured=.2,limit=limit,unit='m',message='Below minimum')
        for rule,limit in [('iscode.a2_2.initial_gm',.25),('iscode.loading.gm',.3)]])
    rows={c['rule_id']:c for c in stability_payload(instance,instance.onboard,part)['criteria']}
    assert rows['iscode.a2_2.initial_gm']['limit']==.25
    assert rows['iscode.a2_2.initial_gm']['passed'] is False
    assert rows['iscode.loading.gm']['limit']==pytest.approx(.3)
    assert rows['iscode.loading.gm']['passed'] is False
