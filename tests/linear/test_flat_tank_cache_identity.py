"""Accepted flat tank solids must never reuse another shape's cached FSM."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.linear import cache,physics
from stowage.linear.tank_fsm import max_slack_fsm_m4
from stowage.vessel.tanks import TankGeometry,build_tanks


def flat_record(**changes):
    return {**dict(id='T',kind='ballast',content_density_kg_m3=1000.,
        capacity_m3=40.,adjustable=True,shape='rectangular_prism',
        x_aft_m=0.,x_fore_m=10.,x_min_m=0.,x_max_m=10.,
        y_min_m=-1.,y_max_m=1.,z_min_m=0.,z_max_m=2.),**changes}


def representation(shape,*,nested=False,wrapped=False):
    record=flat_record()
    if shape is None:record.pop('shape')
    else:record['shape']=shape
    if shape in ('hull_sections','hull_intersection'):
        polygon=[[-1.,0.],[1.,0.],[1.,2.],[-1.,2.]]
        record.update(x_m=[5.-5./np.sqrt(3),5.+5./np.sqrt(3)],
                      integration_weights_m=[5.,5.],sections_yz_m=[[polygon],[polygon]])
    metadata=('id','kind','content_density_kg_m3','capacity_m3','adjustable','x_aft_m','x_fore_m')
    geometry={key:value for key,value in record.items() if key not in metadata}
    if wrapped:
        for key in (f'{axis}_{side}_m' for axis in 'xyz' for side in ('min','max')):
            geometry[key]={'value':geometry[key],'unit':'m','source':'synthetic regression'}
    return {**{key:record[key] for key in metadata},'geometry':geometry} if nested else {
        **{key:record[key] for key in metadata},**geometry}


def test_warm_fsm_cache_remeasures_real_flat_tank_with_same_capacity(monkeypatch):
    first=flat_record()
    second={**first,'y_min_m':-2.,'y_max_m':2.,'z_max_m':1.}
    payloads={}
    monkeypatch.setattr(cache,'load_payload',lambda key,namespace=None:payloads.get((namespace,key)))
    monkeypatch.setattr(cache,'store_payload',lambda key,value,namespace=None:payloads.__setitem__((namespace,key),value))
    observed=[]
    for record in (first,second):
        parameters=dict(id='toy',tanks=[record])
        tanks=build_tanks(parameters)  # Real accepted records, soundings and geometry.
        instance=SimpleNamespace(vessel=SimpleNamespace(id='toy',tanks=tanks))
        expected=max_slack_fsm_m4(TankGeometry.from_params(record))*tanks[0].content_density_kg_m3
        actual=physics._maximum_tank_fsms(instance,parameters,tanks)
        np.testing.assert_allclose(actual,[expected],rtol=1e-14,atol=0.)
        np.testing.assert_array_equal(physics._maximum_tank_fsms(instance,parameters,tanks),actual)
        observed.append(float(actual[0]))
    np.testing.assert_allclose(observed,[6666.666666666667,53333.333333333336],rtol=1e-14)
    assert len(payloads)==2


@pytest.mark.parametrize('shape',[None,'rectangular_prism','hull_sections','hull_intersection'])
@pytest.mark.parametrize('wrapped',[False,True])
def test_real_flat_and_nested_geometry_share_solid_inputs_for_every_cache(shape,wrapped):
    flat=representation(shape,wrapped=wrapped)
    nested=representation(shape,nested=True,wrapped=wrapped)
    parameters=[dict(id='toy',tanks=[record]) for record in (flat,nested)]
    for params in parameters:
        tanks=build_tanks(params)
        assert tanks[0].capacity_m3==40.
        assert TankGeometry.from_params(params['tanks'][0]).capacity_m3==pytest.approx(40.)
    for artifact in ('fsm','fixed_tank','coefficient','calibration','liquid_reference','instance'):
        projected=[cache.physical_parameters(params,artifact) for params in parameters]
        assert projected[0]==projected[1]
        assert projected[0]['tanks'][0]['geometry']


@pytest.mark.parametrize('nested',[False,True])
@pytest.mark.parametrize('field',[f'{axis}_{side}_m' for axis in 'xyz' for side in ('min','max')])
def test_every_rectangular_bound_is_in_all_solid_consuming_projections(nested,field):
    original=dict(id='toy',tanks=[representation('rectangular_prism',nested=nested)])
    changed=deepcopy(original);record=changed['tanks'][0]
    geometry=record['geometry'] if nested else record
    geometry[field]+=.125
    for artifact in ('fsm','fixed_tank','coefficient','calibration','liquid_reference','instance'):
        assert cache.physical_parameters(original,artifact)!=cache.physical_parameters(changed,artifact)


@pytest.mark.parametrize('nested',[False,True])
@pytest.mark.parametrize('field',['x_m','integration_weights_m','sections_yz_m'])
def test_embedded_section_arrays_are_in_all_solid_consuming_projections(nested,field):
    original=dict(id='toy',tanks=[representation('hull_sections',nested=nested)])
    changed=deepcopy(original);record=changed['tanks'][0]
    geometry=record['geometry'] if nested else record
    if field=='sections_yz_m':geometry[field][0][0][0][0]-=.25
    else:geometry[field][0]+=.25
    for artifact in ('fsm','fixed_tank','coefficient','calibration','liquid_reference','instance'):
        assert cache.physical_parameters(original,artifact)!=cache.physical_parameters(changed,artifact)


@pytest.mark.parametrize('shape',['hull_sections','hull_intersection'])
@pytest.mark.parametrize('nested',[False,True])
@pytest.mark.parametrize('field',['nx','nz','inner_skin_m'])
def test_bounds_only_hull_builder_controls_are_not_lost(shape,nested,field):
    record=representation('rectangular_prism',nested=nested)
    geometry=record['geometry'] if nested else record
    geometry.update(shape=shape,nx=48,nz=64,inner_skin_m=.1)
    original=dict(id='toy',hull={'lpp_m':100.},
                  hull_form={'family':'synthetic projection only'},tanks=[record])
    changed=deepcopy(original);record=changed['tanks'][0]
    geometry=record['geometry'] if nested else record
    geometry[field]+=1
    for artifact in ('fsm','fixed_tank','coefficient','calibration','liquid_reference','instance'):
        assert cache.physical_parameters(original,artifact)!=cache.physical_parameters(changed,artifact)
