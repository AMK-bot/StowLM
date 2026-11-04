"""Sub-ULP liquid depths retain finite centroids and real free surfaces."""
from pathlib import Path
import warnings

import numpy as np
import pytest

from stowage.checker.stability_tank_transfer import _direct_liquid
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.hull_tanks import SectionTankGeometry,_UNDEFINED_LIQUID_VOLUME_M3
from stowage.vessel.tanks import TankGeometry


@pytest.mark.parametrize('ship',['feeder_500','feeder_800','feeder_1700'])
def test_every_fleet_tank_tiny_positive_liquid_is_finite_inside_bounds(ship):
    params=load_ship_params(ship)
    for record in params['tanks']:
        solid=TankGeometry.from_params(record,ship_params=params)
        bounds=np.array([[getattr(solid,f'{a}_min_m'),getattr(solid,f'{a}_max_m')] for a in 'xyz'])
        for volume in (1e-15,1e-14,1e-13,1e-12,1e-11,1e-10,1e-9):
            for heel in (0.,.2,.8):
                with warnings.catch_warnings():
                    warnings.simplefilter('error',RuntimeWarning)
                    condition=solid.liquid(volume,heel)
                    direct=_direct_liquid(solid,volume,heel,0.)[0]
                    reference=_direct_liquid(solid,volume,heel,0.,polynomial=False)[0]
                centres=np.array([condition.centroid_m,direct,reference])
                assert np.isfinite(centres).all(),(ship,record['id'],volume,heel)
                assert (centres>=bounds[:,0]-1e-12).all() and (centres<=bounds[:,1]+1e-12).all()
                assert condition.volume_m3==volume
                assert condition.free_surface_area_m2>0
                assert condition.fsm_m4>=0
                np.testing.assert_array_equal(direct,reference)
                np.testing.assert_array_equal(direct,condition.centroid_m)
                xs,weights,areas=solid.section_liquid_areas(volume,heel)
                assert areas @ weights==pytest.approx(volume,rel=2e-8,abs=1e-28)


def _flat_bottom():
    x=[1.-1./np.sqrt(3),1.+1./np.sqrt(3)]
    return TankGeometry.from_params(dict(shape='hull_sections',x_m=x,integration_weights_m=[1.,1.],
        sections_yz_m=[[[[0.,0.],[2.,0.],[2.,1.],[0.,1.]]]]*2,
        x_min_m=0.,x_max_m=2.,y_min_m=0.,y_max_m=2.,z_min_m=0.,z_max_m=1.))


def test_tiny_flat_bottom_slack_fill_preserves_discontinuous_positive_fsm():
    solid=_flat_bottom()
    assert solid.liquid(0.).fsm_m4==0.
    for volume in (1e-15,1e-12,1e-9):
        condition=solid.liquid(volume)
        assert condition.volume_m3==volume
        assert condition.centroid_m[:2]==pytest.approx((1.,1.),abs=2e-15)
        assert condition.centroid_m[2]==pytest.approx(volume/8,rel=1e-14)
        assert condition.fsm_m4==pytest.approx(2.*2.**3/12,rel=1e-14)


def test_tiny_prismatic_trim_retains_volume_and_finite_centroid():
    solid=_flat_bottom()
    for volume in (1e-15,1e-12,1e-9):
        for heel in (0.,.2,.8):
            for trim in (-.01,.01):
                condition=solid.liquid(volume,heel,trim)
                xs,weights,areas=solid.section_liquid_areas(volume,heel,trim)
                assert areas @ weights==pytest.approx(volume,rel=2e-10,abs=1e-28)
                assert np.isfinite(condition.centroid_m).all()
                assert np.all(np.array(condition.centroid_m)>=0.)
                assert np.all(np.array(condition.centroid_m)<=[2.,2.,1.])


def test_only_unresolved_residue_below_cap_uses_exact_empty_convention(monkeypatch):
    solid=_flat_bottom()
    def unresolved(self,volume,normal,*,surface=False,distribution=False):
        result=(np.zeros(4),0.,0.) if surface else np.zeros(4)
        return result,0.
    monkeypatch.setattr(SectionTankGeometry,'_tiny_liquid',unresolved)
    volume=_UNDEFINED_LIQUID_VOLUME_M3/2
    empty=solid.liquid(0.,.2)
    assert solid.liquid(volume,.2)==empty
    for polynomial in (False,True):
        centroid,level=_direct_liquid(solid,volume,.2,0.,polynomial=polynomial)
        np.testing.assert_array_equal(centroid,empty.centroid_m)
        assert level==empty.plane_level_m
        with pytest.raises(ArithmeticError,match='no resolved positive volume'):
            _direct_liquid(solid,2*_UNDEFINED_LIQUID_VOLUME_M3,.2,0.,polynomial=polynomial)
    with pytest.raises(ArithmeticError,match='no resolved positive volume'):
        solid.liquid(2*_UNDEFINED_LIQUID_VOLUME_M3,.2)
    def inaccurate(self,volume,normal,*,surface=False,distribution=False):
        totals=np.array([volume/2,volume/2,volume/2,volume/4])
        return ((totals,1.,1.) if surface else totals),0.
    monkeypatch.setattr(SectionTankGeometry,'_tiny_liquid',inaccurate)
    for polynomial in (False,True):
        with pytest.raises(ArithmeticError,match='root failed volume tolerance'):
            _direct_liquid(solid,1e-12,.2,0.,polynomial=polynomial)
    with pytest.raises(ArithmeticError,match='root failed volume tolerance'):
        solid.liquid(1e-12,.2)


def test_full_checker_handles_1e_minus_10_kg_ballast_residue():
    import importlib.util
    from stowage.checker.full import check
    from stowage.checker import stability
    from stowage.model import BallastCondition
    root=Path(__file__).resolve().parents[2]
    path=root/'experiments/checker/perf-1028/corpus.py'
    spec=importlib.util.spec_from_file_location('near_empty_manual_corpus',path)
    corpus=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(corpus)
    instance,plan=corpus.manual_case('feeder_500','C1')
    # Setup may populate the conservative public baseline after unrelated source
    # edits invalidate the reviewed projection. The checker's timer is unchanged.
    stability._canonical_fleet_policy(instance.vessel)
    baseline=check(instance,plan)
    assert baseline.passed,[(v.rule_id,v.message) for v in baseline.violations]
    masses=dict(plan.ballast.tank_masses_kg)
    assert masses['APT-P']==0.
    masses['APT-P']=1e-10
    plan=plan.model_copy(update=dict(ballast=BallastCondition(tank_masses_kg=masses,source='planner')))
    report=check(instance,plan)
    assert all(part.ran and part.error is None for part in report.parts.values())
    assert not any(v.rule_id=='checker.internal_error' for v in report.violations)
    assert report.passed,[(v.rule_id,v.message) for v in report.violations]
