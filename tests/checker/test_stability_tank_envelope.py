"""Whole-range free-surface extrema independently checked against liquid solids."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker.stability_tank_envelope import max_fsm
from stowage.vessel.hull_tanks import SectionTankGeometry
from stowage.vessel.tanks import TankGeometry


def section(polygon):
    p=np.asarray(polygon,dtype=float)
    return SectionTankGeometry(x_min_m=0.,x_max_m=2.,y_min_m=float(p[:,0].min()),
        y_max_m=float(p[:,0].max()),z_min_m=float(p[:,1].min()),z_max_m=float(p[:,1].max()),
        x_m=np.array([1-1/np.sqrt(3),1+1/np.sqrt(3)]),
        integration_weights_m=np.ones(2),sections_yz_m=((p,),(p,)))


def test_rectangular_exact_and_unknown_shape_rejected():
    solid=TankGeometry(0.,2.,-3.,3.,0.,4.)
    assert max_fsm(solid)==pytest.approx((2*6**3/12,solid.capacity_m3/2))
    with pytest.raises(ValueError,match='explicit supported'):
        max_fsm(SimpleNamespace())


def test_tapered_triangle_maximum_is_full_slack_limit_not_half():
    solid=section([[0,0],[4,4],[-4,4]])
    fsm,volume=max_fsm(solid)
    assert fsm==pytest.approx(2*8**3/12,rel=1e-12)
    assert volume==pytest.approx(solid.capacity_m3,rel=1e-12)
    assert fsm>2*solid.sounding(solid.capacity_m3/2).fsm_m4
    assert solid.sounding(solid.capacity_m3).fsm_m4==0


def test_internal_vertex_maximum_and_translation_invariance():
    polygon=np.array([[-1,0],[1,0],[3,2],[1,4],[-1,4],[-3,2]],dtype=float)
    a=section(polygon)
    b=section(polygon+[1000.,200.])
    expected=2*6**3/12
    assert max_fsm(a)==pytest.approx((expected,a.capacity_m3/2),rel=1e-10)
    assert max_fsm(b)==pytest.approx(max_fsm(a),rel=1e-10)


def test_asymmetric_disjoint_sections_match_dense_independent_soundings():
    a=section([[0,0],[3,1],[2,4],[-1,3]])
    fsm,volume=max_fsm(a)
    samples=np.linspace(.001,.999,301)*a.capacity_m3
    values=np.array([a.sounding(v).fsm_m4 for v in samples])
    assert fsm>=values.max()*(1-1e-11)
    assert fsm<=values.max()*1.002
    assert a.sounding(volume).fsm_m4==pytest.approx(fsm,rel=1e-8)


def test_production_hfo_peak_exceeds_half_fill():
    from stowage.vessel.fleet import load_ship_params
    record=next(r for r in load_ship_params('feeder_1700')['tanks'] if r['id']=='FO-F-P')
    solid=TankGeometry.from_params(record)
    fsm,volume=max_fsm(solid)
    assert fsm>=solid.sounding(.9*solid.capacity_m3).fsm_m4
    # The production KCS tank replaced the retired analytic tank whose peak
    # happened to exceed half-fill inertia tenfold. Test the physical contract:
    # this shaped tank's maximum is above, and occurs away from, half fill.
    assert fsm>solid.sounding(.5*solid.capacity_m3).fsm_m4
    assert volume/solid.capacity_m3 != pytest.approx(.5,abs=1e-3)
    # The peak can sit immediately on either side of a horizontal ledge.
    nearby=[solid.sounding(min(solid.capacity_m3*(1-1e-10),max(1e-10,volume+d*solid.capacity_m3))).fsm_m4
            for d in (-1e-8,0,1e-8)]
    assert max(nearby)==pytest.approx(fsm,rel=2e-5)


def test_quartic_stationary_point_between_all_vertex_heights():
    left=np.array([[-5.,0.],[-3.,0.],[-4.,2.]])
    right=np.array([[4.,0.],[5.,2.],[3.,2.]])
    solid=SectionTankGeometry(x_min_m=0.,x_max_m=2.,y_min_m=-5.,y_max_m=5.,
        z_min_m=0.,z_max_m=2.,x_m=np.array([1-1/np.sqrt(3),1+1/np.sqrt(3)]),
        integration_weights_m=np.ones(2),sections_yz_m=((left,right),(left,right)))
    # At z=1 each side has width1, centroid±4; parallel-axis theorem.
    fsm,volume=max_fsm(solid)
    assert fsm==pytest.approx(2*(2/12+32),rel=1e-12)
    assert volume==pytest.approx(solid.capacity_m3/2,rel=1e-12)
    assert solid.sounding(volume).fsm_m4==pytest.approx(fsm,rel=1e-12)


def fuel_condition(fraction):
    from stowage.checker.stability_loading import loading_condition
    from stowage.model import ShipState
    from stowage.model.fixtures import build_instance
    from stowage.vessel.tanks import build_tanks

    solid=section([[0,0],[4,4],[-4,4]])
    geometry=dict(shape='hull_sections',x_min_m=solid.x_min_m,x_max_m=solid.x_max_m,
        y_min_m=solid.y_min_m,y_max_m=solid.y_max_m,z_min_m=solid.z_min_m,z_max_m=solid.z_max_m,
        x_m=solid.x_m.tolist(),integration_weights_m=solid.integration_weights_m.tolist(),
        sections_yz_m=[[p.tolist() for p in sec] for sec in solid.sections_yz_m])
    params={'tanks':[dict(id='tapered-fuel',kind='fuel_oil',content_density_kg_m3=1000.,
        capacity_m3=solid.capacity_m3,x_aft_m=solid.x_min_m,x_fore_m=solid.x_max_m,geometry=geometry)]}
    instance=build_instance()
    instance.vessel.hull.lightship_mass_kg=1e6
    instance.vessel.tanks=[]
    empty=loading_condition(instance,ShipState(vessel_id=instance.vessel.id))
    instance.vessel.tanks=build_tanks(params)
    state=ShipState(vessel_id=instance.vessel.id,
        tank_masses_kg={'tapered-fuel':fraction*solid.capacity_m3*1000.})
    condition=loading_condition(instance,state,ship_params=params)
    return condition,empty,solid


def test_variable_section_loading_preserves_actual_mass_moments_at_half_and_full():
    half,empty,solid=fuel_condition(.5)
    full,_,_=fuel_condition(1.)
    maximum,limiting_volume=max_fsm(solid)
    assert limiting_volume==pytest.approx(solid.capacity_m3)
    assert half.fsm_kgm==pytest.approx(1000*maximum)
    assert full.fsm_kgm==pytest.approx(half.fsm_kgm)
    assert maximum>max(solid.sounding(solid.capacity_m3*f).fsm_m4 for f in (0,.25,.5,.75,1))
    for fraction,condition in ((.5,half),(1.,full)):
        assert not condition.errors
        mass=1000*fraction*solid.capacity_m3
        row=solid.sounding(fraction*solid.capacity_m3)
        assert condition.mass_kg==pytest.approx(empty.mass_kg+mass)
        actual=np.array([condition.lcg_m,condition.tcg_m,condition.kg_m])*condition.mass_kg
        expected=np.array([empty.lcg_m,empty.tcg_m,empty.kg_m])*empty.mass_kg
        expected+=mass*np.array([row.lcg_m,row.tcg_m,row.vcg_m])
        assert actual==pytest.approx(expected,abs=1e-8)
        assert condition.actual_tanks[0].volume_m3==pytest.approx(fraction*solid.capacity_m3)


@pytest.mark.parametrize('heel',[-.4,.4])
def test_variable_section_curve_uses_finite_transfer_envelope_on_both_sides(heel):
    from stowage.checker.stability_loading import curve_correction

    condition,_,solid=fuel_condition(1.)
    assert condition.tanks[0].volume_m3==pytest.approx(solid.capacity_m3)
    trim=.01
    correction=curve_correction(condition,[heel],trim)[0]
    normal=np.array([np.tan(trim)*np.cos(heel),-np.sin(heel),np.cos(heel)])
    normal/=np.linalg.norm(normal)
    along=np.array([1.,0.,-np.tan(trim)])
    along/=np.linalg.norm(along)
    outward=np.cross(normal,along)*np.sign(heel)
    moments=[]
    for volume in np.linspace(0.,solid.capacity_m3,401):
        tilted=np.array(solid.liquid(volume,heel,trim).centroid_m)
        row=solid.sounding(volume)
        upright=np.array([row.lcg_m,row.tcg_m,row.vcg_m])
        moments.append(volume*float((tilted-upright)@outward))
    sampled=max(moments)
    measured=abs(correction)*condition.mass_kg/1000.
    moment_budget=1e-4*condition.mass_kg/1000.
    assert correction*np.sign(heel)>0  # full-volume FSM limit must not zero the transfer
    assert measured>=sampled-1e-9
    # Dense independent fill sweep has smaller spacing error than this margin.
    assert measured<=sampled+moment_budget+.002
