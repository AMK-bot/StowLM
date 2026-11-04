"""True section solids: box equivalence, curved DB geometry and inclined liquid."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import math

import numpy as np
import pytest

from stowage.params import load_param_file
from stowage.vessel.hydrostatics import _moments
from stowage.vessel.tanks import TankGeometry,build_tanks


def prism_record(panels=1):
    polygon=[[-2.,0.],[2.,0.],[2.,4.],[-2.,4.]]
    edges=np.linspace(0.,10.,panels+1)
    x=[];w=[]
    for a,b in zip(edges,edges[1:]):
        x.extend([(a+b)/2-(b-a)/(2*np.sqrt(3)),(a+b)/2+(b-a)/(2*np.sqrt(3))])
        w.extend([(b-a)/2]*2)
    return {'shape':'hull_sections','x_m':x,'integration_weights_m':w,
            'sections_yz_m':[[polygon] for _ in x],
            'x_min_m':0.,'x_max_m':10.,'y_min_m':-2.,'y_max_m':2.,'z_min_m':0.,'z_max_m':4.}


@pytest.mark.parametrize('heel,trim,fraction',[(0.,0.,.5),(.5,0.,.1),(.65,.2,.25),(-.6,-.15,.9),(.3,.02,1e-7)])
def test_constant_sections_reproduce_exact_box_even_at_plane_corner_transitions(heel,trim,fraction):
    box=TankGeometry(0.,10.,-2.,2.,0.,4.)
    sections=TankGeometry.from_params(prism_record())
    volume=box.capacity_m3*fraction
    actual=sections.liquid(volume,heel,trim)
    expected=box.liquid(volume,heel,trim)
    assert sections.shape=='hull_sections'
    assert sections.is_rectangular is False
    assert box.shape=='rectangular_prism' and box.is_rectangular
    assert actual.centroid_m==pytest.approx(expected.centroid_m,abs=2e-10)
    assert actual.free_surface_area_m2==pytest.approx(expected.free_surface_area_m2,rel=1e-9)
    assert actual.fsm_m4==pytest.approx(expected.fsm_m4,rel=1e-8,abs=1e-10)
    xs,weights,areas=sections.section_liquid_areas(volume,heel,trim)
    assert float(areas @ weights)==pytest.approx(volume,rel=2e-9,abs=1e-10)
    assert float((xs*areas) @ weights)/volume==pytest.approx(actual.centroid_m[0],abs=2e-10)


def test_fleet_double_bottom_uses_actual_polygons_not_bounding_box():
    params=load_param_file('fleet','feeder_500')
    record=next(t for t in params['tanks'] if t['id']=='DB-M-P')
    geometry=TankGeometry.from_params(record)
    totals=np.zeros(4)
    for x,w,polys in zip(geometry.x_m,geometry.integration_weights_m,geometry.sections_yz_m):
        moments=sum((_moments(p) for p in polys),np.zeros(3))
        totals+=w*np.array([moments[0],x*moments[0],moments[1],moments[2]])
    full=geometry.liquid(geometry.capacity_m3,.3,.03)
    bbox=(geometry.x_max_m-geometry.x_min_m)*(geometry.y_max_m-geometry.y_min_m)*(geometry.z_max_m-geometry.z_min_m)
    assert geometry.capacity_m3==pytest.approx(record['capacity_m3'],rel=1e-13)
    assert geometry.capacity_m3<bbox*.999
    assert full.centroid_m==pytest.approx(totals[1:]/totals[0],abs=1e-12)
    assert full.fsm_m4==0 and full.free_surface_area_m2==0
    actual=geometry.sounding(geometry.capacity_m3/2)
    assert actual.fsm_m4==pytest.approx(record['table'][2]['fsm_m4'],rel=1e-10)
    assert actual.vcg_m==pytest.approx(record['table'][2]['vcg_m'],rel=1e-10)


def test_fleet_tank_heel_trim_reflection_and_weight_distribution():
    params=load_param_file('fleet','feeder_500')
    port=TankGeometry.from_params(next(t for t in params['tanks'] if t['id']=='DB-M-P'))
    starboard=TankGeometry.from_params(next(t for t in params['tanks'] if t['id']=='DB-M-S'))
    p=port.liquid(port.capacity_m3*.4,.4,.04)
    s=starboard.liquid(starboard.capacity_m3*.4,-.4,.04)
    assert p.centroid_m==pytest.approx((s.centroid_m[0],-s.centroid_m[1],s.centroid_m[2]),rel=1e-11)
    assert p.fsm_m4==pytest.approx(s.fsm_m4,rel=1e-11)
    x,weights,areas=port.section_liquid_areas(port.capacity_m3*.4,.4,.04)
    assert areas @ weights==pytest.approx(port.capacity_m3*.4,rel=1e-10)
    assert (areas*x) @ weights/(areas @ weights)==pytest.approx(p.centroid_m[0],rel=1e-12)


def test_geometry_and_roots_cached_without_mutation_leaks():
    record=prism_record()
    first=TankGeometry.from_params(record)
    second=TankGeometry.from_params(deepcopy(record))
    assert first is second
    with pytest.raises((FrozenInstanceError,AttributeError)):
        first.x_min_m=99.
    for array in (first.x_m,first.integration_weights_m,first.sections_yz_m[0][0]):
        with pytest.raises(ValueError):
            array.flags.writeable=True
    condition=first.liquid(80.,.2,.03)
    assert second.liquid(80.,.2,.03) is condition
    mutable=first.sounding(80.)
    mutable.vcg_m=999.
    assert first.sounding(80.).vcg_m!=999.
    for array in first.section_liquid_areas(80.,.2,.03):
        with pytest.raises(ValueError):
            array.flags.writeable=True


def test_empty_full_and_near_endpoint_slack_conditions():
    geometry=TankGeometry.from_params(prism_record())
    for fraction in (0.,1.):
        condition=geometry.liquid(geometry.capacity_m3*fraction,.3,.02)
        assert condition.fsm_m4==0 and condition.free_surface_area_m2==0
    # Numerical overshoot from mass/density arithmetic is safely clamped.
    assert geometry.sounding(np.nextafter(geometry.capacity_m3,math.inf)).fsm_m4==0
    for fraction in (1e-9,1-1e-9):
        condition=geometry.liquid(geometry.capacity_m3*fraction,.3,.02)
        assert condition.fsm_m4>0 and condition.free_surface_area_m2>0
    for volume in (-1.,geometry.capacity_m3+1.,float('nan')):
        with pytest.raises(ValueError):
            geometry.sounding(volume)


def test_missing_gauss_mates_and_gaps_are_preserved_as_supplied_quadrature():
    record=prism_record(2)
    record['x_m']=record['x_m'][::3]
    record['integration_weights_m']=record['integration_weights_m'][::3]
    record['sections_yz_m']=record['sections_yz_m'][::3]
    geometry=TankGeometry.from_params(record)
    x,w,areas=geometry.section_liquid_areas(geometry.capacity_m3/2,.2,.03)
    np.testing.assert_array_equal(x,record['x_m'])
    np.testing.assert_array_equal(w,record['integration_weights_m'])
    assert areas @ w==pytest.approx(geometry.capacity_m3/2)


def test_invalid_sections_and_capacity_fail_explicitly():
    record=prism_record()
    record['integration_weights_m'][0]=-1
    with pytest.raises(ValueError,match='positive finite weights'):
        TankGeometry.from_params(record)
    record=prism_record()
    record['sections_yz_m'][0][0]=record['sections_yz_m'][0][0][::-1]
    with pytest.raises(ValueError,match='counterclockwise'):
        TankGeometry.from_params(record)
    bounds={key:value for key,value in prism_record().items() if key.endswith('_min_m') or key.endswith('_max_m')}
    with pytest.raises(ValueError,match='ship_params'):
        TankGeometry.from_params(dict(bounds,shape='hull_sections'))
    params=load_param_file('fleet','feeder_500')
    params['tanks'][0]['capacity_m3']*=1.01
    with pytest.raises(ValueError,match='capacity differs'):
        build_tanks(params)


@pytest.mark.parametrize('second',[
    [[0.,0.],[1.,0.],[1.,1.],[0.,1.]],  # Duplicate solid.
    [[.5,0.],[1.5,0.],[1.5,1.],[.5,1.]],  # Shared collinear boundaries.
    [[.2,.2],[.8,.2],[.8,.8],[.2,.8]],  # Strict containment.
    [[.5,-.5],[.75,-.5],[.75,1.5],[.5,1.5]],  # Crosses without contained vertices.
    [[1.-1e-10,0.],[2.,0.],[2.,1.],[1.-1e-10,1.]],  # Narrow genuine overlap.
])
def test_section_components_reject_positive_area_overlap(second):
    record=prism_record()
    record.update(x_m=[.5],integration_weights_m=[1.],x_max_m=1.,
                  y_min_m=0.,y_max_m=2.,z_min_m=-1.,z_max_m=2.,
                  sections_yz_m=[[[[0.,0.],[1.,0.],[1.,1.],[0.,1.]],second]])
    with pytest.raises(ValueError,match='overlap'):
        TankGeometry.from_params(record)


@pytest.mark.parametrize('second',[
    [[1.,0.],[2.,0.],[2.,1.],[1.,1.]],  # Shared edge.
    [[1.,1.],[2.,1.],[2.,2.],[1.,2.]],  # Shared vertex.
    [[1.,np.nextafter(1.,0.)],[2.,1.],[2.,2.],[1.,2.]],  # Clipping roundoff.
])
def test_section_components_allow_boundary_contact(second):
    record=prism_record()
    record.update(x_m=[.5],integration_weights_m=[1.],x_max_m=1.,
                  y_min_m=0.,y_max_m=2.,z_min_m=0.,z_max_m=2.,
                  sections_yz_m=[[[[0.,0.],[1.,0.],[1.,1.],[0.,1.]],second]])
    geometry=TankGeometry.from_params(record)
    assert geometry.capacity_m3==pytest.approx(2.)


@pytest.mark.parametrize('polygons,capacity',[
    ([[[0.,0.],[2.,0.],[0.,2.]],[[2.,0.],[2.,2.],[0.,2.]]],4.),
    ([[[0.,0.],[2.,0.],[2.,1.],[1.,1.],[1.,2.],[0.,2.]],
      [[1.,1.],[2.,1.],[2.,2.],[1.,2.]]],4.),
    ([[[0.,0.],[2.,0.],[2.,1.],[1.,1.],[1.,2.],[0.,2.]],
      [[1.25,1.25],[1.75,1.25],[1.75,1.75],[1.25,1.75]]],3.25),
])
def test_section_components_with_overlapping_bounds_can_be_disjoint(polygons,capacity):
    record=prism_record()
    record.update(x_m=[.5],integration_weights_m=[1.],x_max_m=1.,
                  y_min_m=0.,y_max_m=2.,z_min_m=0.,z_max_m=2.,
                  sections_yz_m=[polygons])
    assert TankGeometry.from_params(record).capacity_m3==pytest.approx(capacity)


def test_concave_section_components_reject_overlap():
    record=prism_record()
    record.update(x_m=[.5],integration_weights_m=[1.],x_max_m=1.,
                  y_min_m=0.,y_max_m=2.,z_min_m=0.,z_max_m=2.,
                  sections_yz_m=[[
                      [[0.,0.],[2.,0.],[2.,1.],[1.,1.],[1.,2.],[0.,2.]],
                      [[.5,.5],[1.5,.5],[1.5,1.5],[.5,1.5]],
                  ]])
    with pytest.raises(ValueError,match='overlap'):
        TankGeometry.from_params(record)


def test_build_tanks_rejects_inconsistent_supplied_sounding_table():
    params=load_param_file('fleet','feeder_500')
    params['tanks'][0]['table'][2]['fsm_m4']+=1.
    with pytest.raises(ValueError,match='supplied sounding differs'):
        build_tanks(params)
    params=load_param_file('fleet','feeder_500')
    params['tanks'][0]['table'][1]['volume_m3']=-1.
    with pytest.raises(ValueError,match='table must ascend'):
        build_tanks(params)
