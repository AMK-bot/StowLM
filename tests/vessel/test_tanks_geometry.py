"""Analytic box checks, including finite-angle transfer and endpoint FSM."""
from copy import deepcopy
from math import atan, cos

import pytest

from stowage.params import load_param_file
from stowage.vessel.tanks import TankGeometry, build_tanks


@pytest.mark.parametrize('name',['feeder_500','feeder_800','feeder_1700'])
def test_fleet_tanks_match_declared_capacity_geometry(name):
    params=load_param_file('fleet',name)
    tanks=build_tanks(params)
    assert len(tanks)==len(params['tanks'])
    records={r['id']:r for r in params['tanks']}
    for tank in tanks:
        record=records[tank.id]
        assert tank.capacity_m3==record['capacity_m3']
        assert tank.heated==record['heated']
        assert tank.table[-1].volume_m3==tank.capacity_m3
        for actual,expected in zip(tank.table,record['table'],strict=True):
            for key,value in expected.items():
                assert getattr(actual,key)==pytest.approx(value,rel=1e-10,abs=1e-10)


def test_upright_slack_fsm_does_not_taper_at_endpoints():
    geometry=TankGeometry(0,10,-2,2,1,5)
    for fraction in (1e-12,.25,.5,.99,1-1e-12):
        sounding=geometry.sounding(160*fraction)
        assert sounding.vcg_m==pytest.approx(1+2*fraction)
        assert sounding.fsm_m4==pytest.approx(10*4**3/12)
    assert geometry.sounding(0).fsm_m4==0
    assert geometry.sounding(160).fsm_m4==0
    for volume in (-1,161,float('nan')):
        with pytest.raises(ValueError):
            geometry.sounding(volume)


def test_finite_heel_box_matches_analytic_wedge():
    # A centred half-full 10x4x4 box: z_liquid=2+y*tan(phi).
    geometry=TankGeometry(0,10,-2,2,0,4)
    angle=atan(.25)
    liquid=geometry.liquid(80,angle)
    assert liquid.centroid_m==pytest.approx((5,1/6,1+1/48),abs=1e-10)
    assert liquid.free_surface_area_m2==pytest.approx(40/cos(angle))
    assert liquid.fsm_m4==pytest.approx(10*(4/cos(angle))**3/12)
    reflected=geometry.liquid(80,-angle)
    assert reflected.centroid_m==pytest.approx((5,-1/6,1+1/48))


def test_heel_trim_complements_fill_and_full_centroid():
    geometry=TankGeometry(-2,8,-3,1,1,5)
    low=geometry.liquid(40,.65,.2)
    high=geometry.liquid(120,.65,.2)
    centre=(3,-1,3)
    # Central inversion maps the lower 1/4 to the dry upper 1/4.
    for a,b,c in zip(low.centroid_m,high.centroid_m,centre):
        assert 40*(2*c-a)+120*b==pytest.approx(160*c,abs=1e-8)
    assert low.free_surface_area_m2==pytest.approx(high.free_surface_area_m2)
    full=geometry.liquid(160,.65,.2)
    assert full.centroid_m==centre
    assert full.fsm_m4==0


def test_reject_capacity_mismatch_and_unsupported_solid():
    params=deepcopy(load_param_file('fleet','feeder_500'))
    params['tanks'][0]['capacity_m3']*=1.01
    with pytest.raises(ValueError,match='capacity differs'):
        build_tanks(params)
    with pytest.raises(ValueError,match='unsupported'):
        TankGeometry.from_params({'shape':'cylinder'})
    with pytest.raises(ValueError,match='positive extents'):
        TankGeometry(1,0,0,1,0,1)
