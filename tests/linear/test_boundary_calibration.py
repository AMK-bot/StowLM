"""Fixed-list moment capacities solve the independent full balance equations."""
from dataclasses import replace
import math
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker.stability_loading import CorrectionTank, LoadingCondition
from stowage.linear.boundary_calibration import boundary_capacity
from stowage.linear.reference_equilibrium import liquid_equilibrium
from stowage.vessel.hydrostatics import HullGeometry


class AlgebraGeometry:
    lpp_m = 20.
    beam_m = 6.
    max_z_m = 10.
    _axes = HullGeometry._axes

    def submerged(self, aft, fore, heel, density):
        mean = (aft+fore)/2
        return SimpleNamespace(displacement_kg=density*100*mean,
                               centroid_m=(10.+2*(fore-aft),3*math.sin(heel),mean/2))


def test_algebra_boundary_and_input_caches_remain_unchanged():
    geometry = AlgebraGeometry()
    condition = LoadingCondition(205000.,10.,.03,1.,0.,0.,
        cg_cache={(0.,0.):(10.,.03,1.)},upright_cache={'sentinel':'untouched'},
        liquid_usage={'table':11})
    for heel in (-.03,0.,.03):
        result = boundary_capacity(geometry,condition,1025.,heel)
        assert result.capacity_kgm == pytest.approx(condition.mass_kg*3*math.sin(heel),abs=1e-6)
        assert result.tcg_shift_m == pytest.approx(3*math.sin(heel)-condition.tcg_m,abs=1e-12)
        assert result.transverse_moment_shift_kgm == pytest.approx(
            result.capacity_kgm-condition.mass_kg*condition.tcg_m,abs=1e-6)
        # A nominal-arm affine coordinate differs from the actual upright
        # liquid coordinate by this known baseline offset. Transfer the added
        # moment, preserving that offset at the boundary (do not substitute the
        # raw actual-coordinate capacity for the affine-coordinate bound).
        baseline_offset_kgm=1234.
        current_affine_my=condition.mass_kg*condition.tcg_m-baseline_offset_kgm
        affine_boundary=current_affine_my+result.transverse_moment_shift_kgm
        assert affine_boundary == pytest.approx(result.capacity_kgm-baseline_offset_kgm,abs=1e-6)
        assert result.attitude.t_aft_m == pytest.approx(2.,abs=1e-12)
        assert result.attitude.t_fwd_m == pytest.approx(2.,abs=1e-12)
        assert result.residual_norm < 2e-8
    assert condition.cg_cache == {(0.,0.):(10.,.03,1.)}
    assert condition.upright_cache == {'sentinel':'untouched'}
    assert condition.liquid_usage == {'table':11}


@pytest.mark.parametrize('heel',[np.nan,np.inf,math.pi/2,-math.pi/2])
def test_invalid_angles_fail_closed(heel):
    with pytest.raises(ValueError,match='valid heel'):
        boundary_capacity(AlgebraGeometry(),LoadingCondition(205000.,10.,0.,1.,0.,0.),1025.,heel)


def test_invalid_mass_and_failed_balance_fail_closed():
    condition=LoadingCondition(0.,10.,0.,1.,0.,0.)
    with pytest.raises(ValueError,match='positive mass'):
        boundary_capacity(AlgebraGeometry(),condition,1025.,.03)
    with pytest.raises(ValueError,match='valid loading'):
        boundary_capacity(AlgebraGeometry(),replace(condition,mass_kg=205000.,errors=('bad',)),1025.,.03)
    class NoBalance(AlgebraGeometry):
        def submerged(self,aft,fore,heel,density):
            state=super().submerged(aft,fore,heel,density)
            state.centroid_m=(100.,0.,1.)
            return state
    with pytest.raises(ValueError,match='did not converge'):
        boundary_capacity(NoBalance(),replace(condition,mass_kg=205000.),1025.,.03)


def test_production_hull_tank_boundary_matches_full_equilibrium_and_crossing():
    from stowage.linear.physics import _geometry_from_params
    from stowage.vessel.fleet import load_ship_params
    from stowage.vessel.tanks import TankGeometry
    params=load_ship_params('feeder_500')
    geometry=_geometry_from_params(params)
    raw=next(t for t in params['tanks'] if t['geometry']['shape']=='hull_sections')
    tank=TankGeometry.from_params(raw,ship_params=params)
    row=geometry.submerged(geometry.design_draught_m,geometry.design_draught_m,0.,1025.)
    condition=LoadingCondition(row.displacement_kg,row.lcb_m,.001,3.,0.,0.,
        actual_tanks=(CorrectionTank(raw['id'],1025.,tank.capacity_m3*.5,0.,tank),))
    # Low KG provides a stable branch. At each sign, adding moment across the
    # total upright capacity must cross the prescribed angle in the same order.
    for heel in (-math.radians(2.),math.radians(2.)):
        result=boundary_capacity(geometry,condition,1025.,heel)
        assert result.residual_norm < 2e-8
        def independent(offset):
            candidate=replace(condition,tcg_m=result.capacity_kgm/condition.mass_kg+offset,
                              cg_cache={},upright_cache={},liquid_usage={})
            return liquid_equilibrium(geometry,candidate,1025.,initial=result.attitude)
        exact=independent(0.)
        np.testing.assert_allclose([exact.t_aft_m,exact.t_fwd_m,exact.heel_rad],
            [result.attitude.t_aft_m,result.attitude.t_fwd_m,heel],atol=2e-7,rtol=0.)
        below,above=independent(-1e-4),independent(1e-4)
        assert below.heel_rad < heel < above.heel_rad
    assert condition.cg_cache == {}
    assert condition.upright_cache == {}
    assert condition.liquid_usage == {}


def test_nonfinite_liquid_state_and_degenerate_transverse_axis_fail(monkeypatch):
    import stowage.linear.boundary_calibration as module
    condition=LoadingCondition(205000.,10.,0.,1.,0.,0.)
    monkeypatch.setattr(module,'liquid_cg',lambda *args:(10.,np.nan,1.))
    with pytest.raises(ValueError,match='nonfinite'):
        boundary_capacity(AlgebraGeometry(),condition,1025.,.03)
    monkeypatch.setattr(module,'liquid_cg',lambda *args:(10.,0.,1.))
    class Degenerate(AlgebraGeometry):
        def _axes(self,*args):
            return np.array([0.,0.,1.]),np.array([1.,0.,0.]),np.array([0.,0.,1.])
    with pytest.raises(ValueError,match='zero y component'):
        boundary_capacity(Degenerate(),condition,1025.,.03)
