"""Moment balance and section-quadrature integration tests, independent of tables."""
import math

import numpy as np
import pytest

from stowage.params import load_param_file
from stowage.vessel.hydrostatics import HullGeometry


@pytest.fixture(scope='module')
def geometry():
    return HullGeometry.from_params(load_param_file('fleet','feeder_500'))


def test_section_quadrature_integrates_displacement(geometry):
    s=geometry.submerged(6.,5.,math.radians(20))
    assert np.dot(s.sectional_areas_m2,geometry.integration_weights_m)==pytest.approx(s.volume_m3)
    assert s.lcb_m < geometry.lpp_m/2  # aft-heavy immersion, x points forward
    assert s.tcb_m > 0  # positive heel immerses port


def test_equilibrium_recovers_a_known_geometric_condition(geometry):
    # Choose G on the vertical line through B, guaranteeing exact force/moment
    # equilibrium without using the solver to construct the expected attitude.
    ta,tf,phi=5.5,4.5,math.radians(4.)
    s=geometry.submerged(ta,tf,phi)
    n,_,_=geometry._axes(ta,tf,phi)
    g=np.array(s.centroid_m)+n*2
    solved=geometry.equilibrium(s.displacement_kg,*g)
    assert solved.t_aft_m==pytest.approx(ta,abs=1e-4)
    assert solved.t_fwd_m==pytest.approx(tf,abs=1e-4)
    assert solved.heel_rad==pytest.approx(phi,abs=1e-5)


def test_kn_is_symmetric_and_small_angle_matches_metacentre(geometry):
    row=geometry.hydrostatic_row(5.)
    phi=math.radians(.1)
    # Match the even-keel row's LCB; midships LCG would introduce trim in KCS.
    assert geometry.kn(0.,row.displacement_kg,lcg_m=row.lcb_m)==pytest.approx(0,abs=1e-9)
    arm=geometry.kn(phi,row.displacement_kg,lcg_m=row.lcb_m)
    assert arm==pytest.approx(row.kmt_m*math.sin(phi),rel=2e-3)
    assert geometry.kn(-phi,row.displacement_kg,lcg_m=row.lcb_m)==pytest.approx(-arm,abs=1e-9)


def test_heeling_credits_coamings_but_not_deckhouse():
    p=load_param_file('fleet','feeder_500')
    full=HullGeometry.from_params(p)
    p['envelope']['hatch_coamings']=[]
    bare=HullGeometry.from_params(p)
    s=full.submerged(7,7,math.radians(50))
    b=bare.submerged(7,7,math.radians(50))
    assert s.volume_m3>b.volume_m3
    p['envelope']['count_deckhouse']=True
    with pytest.raises(ValueError,match='deckhouse'):
        HullGeometry.from_params(p)


def test_impossible_mass_does_not_return_spurious_equilibrium(geometry):
    with pytest.raises(ValueError,match='equilibrium'):
        geometry.equilibrium(1e10,50,0,4)


def test_large_heel_light_load_and_free_trim(geometry):
    row=geometry.hydrostatic_row(2.)
    phi=math.radians(60.)
    # Regression: a positive-draught initial guess can become dry during Newton
    # iteration at light load/high heel. The volume bracket must find the wet root.
    assert math.isfinite(geometry.kn(phi,row.displacement_kg))
    lcg=geometry.lpp_m*.46
    condition=geometry._solve(row.displacement_kg,lcg,0.,0.,geometry.density_kg_m3,phi)
    assert abs(condition.trim_m)>.01
    n=geometry.waterplane_normal(condition.t_aft_m,condition.t_fwd_m,phi)
    buoyancy=np.asarray(condition.submerged.centroid_m)
    longitudinal=np.array([1,0,(condition.t_fwd_m-condition.t_aft_m)/geometry.lpp_m])
    assert np.dot(buoyancy-[lcg,0,0],longitudinal)==pytest.approx(0,abs=1e-4)
    assert condition.submerged.displacement_kg==pytest.approx(row.displacement_kg,rel=1e-6)
    assert np.linalg.norm(n)==pytest.approx(1.)
