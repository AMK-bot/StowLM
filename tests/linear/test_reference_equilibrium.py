"""Coupled roots retain the public finite-liquid equilibrium equations."""
import math

import numpy as np
import pytest

from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker.stability_loading import CorrectionTank, LoadingCondition, liquid_cg
from stowage.linear.reference_equilibrium import liquid_equilibrium
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.tanks import TankGeometry


@pytest.mark.parametrize('upright', [False, True])
def test_coupled_section_tank_matches_independent_public_fixed_point(upright):
    params = load_ship_params('feeder_500')
    geometry = StabilityGeometry(params)
    raw = next(t for t in params['tanks'] if t['geometry']['shape'] == 'hull_sections')
    tank = TankGeometry.from_params(raw)
    assert not tank.is_rectangular
    row = geometry.full_submerged(geometry.design_draught_m, geometry.design_draught_m, 0., 1025.)
    condition = LoadingCondition(row.displacement_kg, row.centroid_m[0], .001, 3., 0., 0.,
        actual_tanks=(CorrectionTank(raw['id'], 1025., .5*tank.capacity_m3, 0., tank),))
    coupled = liquid_equilibrium(geometry, condition, 1025., upright=upright)
    cg = np.array([condition.lcg_m, 0. if upright else condition.tcg_m, condition.kg_m])
    for _ in range(20):
        reference = (geometry._solve(condition.mass_kg, cg[0], 0., cg[2], 1025., 0.)
                     if upright else geometry.equilibrium(condition.mass_kg, *cg, 1025.))
        moved = np.asarray(liquid_cg(condition, reference.heel_rad,
                                    math.atan(reference.trim_m/geometry.lpp_m)))
        selection = [0, 2] if upright else [0, 1, 2]
        if np.linalg.norm((moved-cg)[selection]) < 1e-8:
            break
        cg = moved
    else:
        pytest.fail('independent public liquid iteration did not converge')
    np.testing.assert_allclose(
        [coupled.t_aft_m, coupled.t_fwd_m, coupled.heel_rad],
        [reference.t_aft_m, reference.t_fwd_m, reference.heel_rad], atol=2e-7, rtol=0.)
    assert coupled.residual_norm < 2e-8


@pytest.mark.parametrize('method',[1,2])
def test_exact_reference_separates_regulatory_and_physical_attitudes(monkeypatch,method):
    """Method2 changes stability KG, never the physical strength liquid loads."""
    from types import SimpleNamespace as NS
    import stowage.linear.physics as physics
    import stowage.checker.stability_loading as loading
    import stowage.checker.stability_liquid as transfer
    import stowage.checker.strength as strength
    import stowage.linear.reference_equilibrium as reference

    class Copy(NS):
        def model_copy(self,*,update):
            return Copy(**(vars(self)|update))

    def floating(aft,fore,heel):
        return NS(t_aft_m=aft,t_fwd_m=fore,heel_rad=heel,
                  trim_m=aft-fore,mean_draught_m=(aft+fore)/2)
    virtual=floating(2.6,2.,.07)
    physical=floating(2.2,2.,.02)
    upright=floating(2.4,2.,0.)
    calls={'loading':[],'liquid':[],'submerged':[],'segments':[]}
    normal=np.array([.1,0.,np.sqrt(.99)])
    class Geometry:
        lpp_m=10.
        def equilibrium(self,*args):
            assert args==(100.,4.,.1,2.25,1025.)
            return virtual
        def _solve(self,*args):
            assert args==(100.,4.,0.,2.25,1025.,0.)
            return upright
        def full_submerged(self,aft,fore,heel,rho):
            calls['submerged'].append((aft,fore,heel))
            return NS(centroid_m=(4.1,0.,1.),bm_t_m=3.,x_m=np.array([1.,2.]),
                      integration_weights_m=np.ones(2),sectional_areas_m2=np.ones(2))
        def _axes(self,*args):
            return normal,None,None
    fast=Geometry()
    condition=NS(mass_kg=100.,lcg_m=4.,tcg_m=.1,kg_m=2.,fsc_m=.25,errors=(),
                 liquid_tables=None,liquid_usage={})
    def load(*args,free_surface_method,**kwargs):
        calls['loading'].append(free_surface_method)
        if len(calls['loading'])==1:
            assert kwargs['precomputed_soundings'] is True
            assert kwargs['small_tank_exemption'] is True
        return condition
    def liquid(geometry,cond,rho,*,upright=False,initial=None):
        calls['liquid'].append(upright)
        return globals_upright if upright else physical
    globals_upright=upright
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD',str(method))
    monkeypatch.delenv('STOWAGE_STABILITY_LIQUID_DIRECT',raising=False)
    monkeypatch.setattr(loading,'loading_condition',load)
    monkeypatch.setattr(transfer,'attach_tables',lambda condition,*args,**kwargs:condition)
    monkeypatch.setattr(reference,'liquid_equilibrium',liquid)
    monkeypatch.setitem(physics._EXACT_CONTEXT,('exact_geometry','test-method'),fast)
    solid=NS(capacity_m3=1.,is_rectangular=True)
    monkeypatch.setattr(TankGeometry,'from_params',lambda *a,**kw:solid)
    monkeypatch.setattr(strength,'_tank_liquid',lambda *a:None)
    def segments(s,l,heel,trim,rho):
        calls['segments'].append((heel,trim))
        return []
    monkeypatch.setattr(strength,'_tank_segments',segments)
    def integrate(*args,**kwargs):
        assert calls['submerged'][0]==(2.2,2.,.02)
        return NS(shear_n=np.array([7.]),bending_nm=np.array([8.]))
    monkeypatch.setattr(strength,'integrate_strength',integrate)
    tank=NS(id='T',adjustable=True,content_density_kg_m3=1000.)
    vessel=Copy(id='test',fixed_loads=[],tanks=[tank],
                hull=NS(lightship_distribution=[(0.,10.,99.)]))
    instance=Copy(vessel=vessel,onboard=NS(tank_masses_kg={}))
    model=NS(_reference_params={'tanks':[{'id':'T'}]},_geometry_fingerprint='test-method',
        cell_vcg_m=np.array([]),cell_lcg_m=np.array([]),cell_tcg_m=np.array([]),
        water_density_kg_m3=1025.,station_x_m=np.array([5.]))
    result=physics.exact_condition(model,instance,np.array([]),np.array([1.]))
    expected=virtual if method==2 else physical
    assert (result['trim_m'],result['list_rad'],result['draught_m'])==(
        expected.trim_m,expected.heel_rad,expected.mean_draught_m)
    assert result['gm_m']==pytest.approx(np.dot(np.array([.1,0.,-1.]),normal)+3.-.25)
    assert calls['loading']==([2,1] if method==2 else [1])
    assert calls['liquid']==([False] if method==2 else [False,True])
    np.testing.assert_allclose(calls['segments'],[(.02,math.atan(.2/10.))],atol=1e-15)
    np.testing.assert_equal(result['sf_n'],[7.])


@pytest.mark.parametrize('method',['0','3','invalid'])
def test_exact_reference_rejects_invalid_free_surface_policy_before_geometry(monkeypatch,method):
    from stowage.linear.physics import exact_condition
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD',method)
    with pytest.raises(ValueError,match='free-surface method must be 1 or 2'):
        exact_condition(None,None,None,None)
