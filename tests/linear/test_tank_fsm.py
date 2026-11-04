"""Continuous max-slack envelopes checked against independent tank clipping."""
import numpy as np
import pytest
from scipy.optimize import minimize_scalar

from stowage.linear.tank_fsm import max_slack_fsm_m4
from stowage.vessel.hull_tanks import SectionTankGeometry
from stowage.vessel.tanks import TankGeometry
from stowage.vessel.fleet import load_ship_params


def section_tank(polygons):
    x=np.array([1-1/np.sqrt(3),1+1/np.sqrt(3)])
    y=np.concatenate([p[:,0] for p in polygons])
    return SectionTankGeometry(0.,2.,float(y.min()),float(y.max()),0.,4.,x,np.ones(2),
                               tuple((p,) for p in polygons))


def check_independent_max(solid):
    normal=np.array([0.,0.,1.])
    def original(level): return solid._integrate(float(level),normal,surface=True)[2]
    grid=np.linspace(solid.z_min_m+1e-10,solid.z_max_m-1e-10,129)
    values=np.array([original(z) for z in grid])
    expected=float(values.max())
    for j in np.argsort(values)[-3:]:
        result=minimize_scalar(lambda z:-original(z),bounds=(grid[max(j-1,0)],grid[min(j+1,len(grid)-1)]),
                               method='bounded',options={'xatol':1e-12})
        expected=max(expected,float(-result.fun))
    upper=max_slack_fsm_m4(solid)
    assert upper>=values.max()-1e-8
    assert upper>=expected-1e-8
    assert upper-expected<max(1e-6,expected*1e-7)
    return upper


def test_rectangle_keeps_exact_constant_slack_inertia():
    solid=TankGeometry(1.,11.,2.,6.,0.,3.)
    assert max_slack_fsm_m4(solid)==pytest.approx(10*4**3/12,rel=1e-15)


def test_nearly_full_one_sided_surface_is_not_discarded():
    polygon=np.array([[0.,0.],[1.,0.],[4.,4.],[0.,4.]])
    solid=section_tank([polygon,polygon])
    upper=check_independent_max(solid)
    assert upper==pytest.approx(2*4**3/12,rel=1e-8)
    assert solid.sounding(solid.capacity_m3).fsm_m4==0.
    assert upper>max(solid.sounding(solid.capacity_m3*f).fsm_m4 for f in (.25,.5,.75))


def test_interior_vertex_peak_exceeds_quarter_fill_samples():
    polygon=np.array([[0.,0.],[1.,0.],[4.,2.],[2.,4.],[0.,4.]])
    solid=section_tank([polygon,polygon])
    upper=check_independent_max(solid)
    assert upper==pytest.approx(2*4**3/12,rel=1e-8)
    assert upper>max(solid.sounding(solid.capacity_m3*f).fsm_m4 for f in (.25,.5,.75))


def test_asymmetric_varying_sections_use_central_inertia_stationary_root():
    a=np.array([[0.,0.],[1.,0.],[4.,4.],[0.,4.]])
    b=np.array([[7.,0.],[11.,0.],[8.,4.],[7.,4.]])
    check_independent_max(section_tank([a,b]))


def test_one_ulp_height_interval_retains_its_left_hand_surface():
    top=np.nextafter(1.,np.inf)
    polygon=np.array([[0.,0.],[1.,0.],[1.,1.],[2.,top],[0.,top]])
    solid=section_tank([polygon,polygon])
    # The final interval's midpoint rounds to its right endpoint on platforms
    # where longdouble has float64 precision. Its real one-sided width still
    # grows from1 to2; a half-open left selector must retain that interval.
    assert max_slack_fsm_m4(solid)==pytest.approx(2*2**3/12,rel=1e-8)


@pytest.mark.parametrize('name',['feeder_500','feeder_800','feeder_1700'])
def test_production_section_tank_envelope_against_dense_and_polished_clipping(name):
    params=load_ship_params(name)
    raw=next(t for t in params['tanks'] if t['geometry']['shape']=='hull_sections')
    solid=TankGeometry.from_params(raw)
    upper=check_independent_max(solid)
    for fraction in (.01,.1,.25,.5,.75,.9,.95,.99,.999999):
        assert upper>=solid.sounding(solid.capacity_m3*fraction).fsm_m4-1e-8


@pytest.mark.parametrize('tolerance',[0.,-1.,np.inf,np.nan])
def test_invalid_envelope_tolerance_is_rejected(tolerance):
    with pytest.raises(ValueError,match='tolerances'):
        max_slack_fsm_m4(TankGeometry(0.,1.,0.,1.,0.,1.),relative_tolerance=tolerance)
