"""Prepared parameter caches preserve content keys and mutation detection."""
from copy import deepcopy
from types import SimpleNamespace as NS

import numpy as np
import pytest

from stowage.linear import cache, physics


def test_prepared_parameters_keep_exact_keys_and_caller_isolation():
    original={'id':'synthetic','hull':{'beam_m':{'value':4.,'unit':'m'}},
              'tanks':[{'id':'tank','geometry':{'shape':'rectangular_prism','width_m':2.}}]}
    pristine=deepcopy(original)
    prepared=cache.prepare_parameters(original)
    assert cache._canonical(prepared)==cache._canonical(original)
    for artifact in ('geometry','integration','fsm','coefficient','calibration','instance'):
        assert cache.physical_parameters(prepared,artifact)==cache.physical_parameters(original,artifact)
    def key(params):
        return cache.cache_key('coefficient_family',vessel_id='synthetic',params=params,
                               inputs={},source_hash='isolated')
    assert key(prepared)==key(original)
    original['tanks'][0]['geometry']['width_m']=3.
    assert cache._jsonable(prepared)==pristine
    assert key(prepared)!=key(original)
    with pytest.raises(TypeError):prepared['tanks'][0]['geometry']['width_m']=4.
    with pytest.raises(TypeError):prepared['tanks'].append({})
    with pytest.raises(TypeError):prepared.update({'id':'changed'})
    with pytest.raises(TypeError):prepared._canonical_bytes=b'{}'
    projected=cache.physical_parameters(prepared)
    projected['tanks'][0]['geometry']['width_m']=9.
    assert cache.physical_parameters(prepared)==cache.physical_parameters(pristine)


def test_internal_resolver_reads_bytes_and_freezes_only_private_snapshot(monkeypatch):
    from stowage.vessel import fleet
    raw={'data':b'id="prepared-test"\n[tank]\nvalues=[1,2]\n'}
    reads=[]
    def read(name):
        reads.append(name)
        return raw['data']
    monkeypatch.setattr(physics,'list_fleet',lambda:['prepared-test'])
    monkeypatch.setattr(fleet,'_parameter_bytes',read)
    instance=NS(vessel=NS(id='prepared-test',slice_of=None,sources=[]))
    first=physics._prepared_params_for(instance)[1]
    assert physics._prepared_params_for(instance)[1] is first
    public=physics._params_for(instance)[1]
    public['tank']['values'][0]=99
    assert first['tank']['values']==[1,2]
    raw['data']=raw['data'].replace(b'[1,2]',b'[9,2]')
    changed=physics._prepared_params_for(instance)[1]
    assert changed is not first and changed['tank']['values']==[9,2]
    assert len(reads)==4


def test_response_identity_checks_actual_array_contents(monkeypatch):
    from stowage.linear import dependencies
    monkeypatch.setattr(dependencies,'dependency_fingerprint',lambda artifact:'isolated')
    monkeypatch.setattr(cache,'response_calibration_inputs',lambda family:{'response':family.array})
    family=NS(parameters=cache.prepare_parameters({'id':'test'}),
              template=NS(band=None),array=np.array([1.,2.]))
    first=cache.response_calibration_identity(family)
    assert cache.response_calibration_identity(family)==first
    family.array.flags.writeable=False
    family.array.flags.writeable=True
    family.array[0]=9.
    assert cache.response_calibration_identity(family)!=first


def test_fixed_tank_integrals_cache_depends_on_volume_stations_and_geometry(monkeypatch,tmp_path):
    from stowage.linear import reusable, tank_weights
    from stowage.vessel.tanks import TankGeometry
    monkeypatch.setattr(cache,'_DEFAULT_ROOT',tmp_path)
    reusable._FIXED_TANK_MEMORY.clear()
    calls=[]
    def solid(record,ship_params):return NS(width=record['geometry']['width_m'])
    def integrate(shape,volume,stations):
        calls.append((shape.width,volume,stations.copy()))
        return np.asarray(stations)*shape.width+volume,np.asarray(stations)+volume
    monkeypatch.setattr(TankGeometry,'from_params',solid)
    monkeypatch.setattr(tank_weights,'unit_tank_weight_integrals',integrate)
    parameters={'id':'fixed-tank-test','tanks':[{'id':'tank','geometry':{'width_m':2.}}]}
    stations=np.array([0.,1.,2.])
    first=reusable._fixed_tank_integrals(parameters,'tank',3.,stations)
    assert np.array_equal(first[0],stations*2+3)
    assert reusable._fixed_tank_integrals(parameters,'tank',3.,stations) is first
    assert len(calls)==1
    with pytest.raises(ValueError):first[0].flags.writeable=True
    reusable._FIXED_TANK_MEMORY.clear()
    disk=reusable._fixed_tank_integrals(parameters,'tank',3.,stations)
    assert np.array_equal(disk[0],first[0]) and len(calls)==1
    reusable._fixed_tank_integrals(parameters,'tank',4.,stations)
    stations[1]=1.5
    reusable._fixed_tank_integrals(parameters,'tank',4.,stations)
    parameters['tanks'][0]['geometry']['width_m']=4.
    changed=reusable._fixed_tank_integrals(parameters,'tank',4.,stations)
    assert len(calls)==4
    assert np.array_equal(changed[0],stations*4+4)
    # At a supplied volume, density and declared capacity do not enter the
    # actual solid's weight integration, nor does an initial-GM rule.
    parameters['tanks'][0].update(kind='fresh_water',content_density_kg_m3=1000.,capacity_m3=99.)
    from stowage.rules import catalogue
    def forbidden(*args,**kwargs):raise AssertionError('irrelevant GM rule lookup')
    monkeypatch.setattr(catalogue,'get_rule',forbidden)
    assert reusable._fixed_tank_integrals(parameters,'tank',4.,stations) is changed
    assert len(calls)==4
    reusable._FIXED_TANK_MEMORY.clear()


def test_prepared_reference_paths_refresh_runtime_directory(monkeypatch,tmp_path):
    from stowage.linear import family_calibration as calibration
    from stowage.checker import stability_liquid
    monkeypatch.setattr(calibration,'_reference_solids',lambda *args:())
    canonical=cache._canonical({'id':'runtime-directory-test'})
    first=tmp_path/'first';second=tmp_path/'other'
    monkeypatch.setattr(stability_liquid,'CACHE_DIRECTORY',first)
    before=calibration._prepared_reference_paths(canonical,'isolated-source')
    monkeypatch.setattr(stability_liquid,'CACHE_DIRECTORY',second)
    after=calibration._prepared_reference_paths(canonical,'isolated-source')
    assert before[0].parent==first and after[0].parent==second


def test_vessel_projection_selects_hull_fields_before_dumping(monkeypatch):
    from stowage.model import Vessel,HullModel
    hull=HullModel.model_construct(lpp_m=10.,loa_m=11.,beam_m=2.,depth_m=3.,
        design_draught_m=2.,scantling_draught_m=2.5,fp_x_m=10.,
        lightship_mass_kg=1000.,lightship_lcg_m=5.,lightship_vcg_m=1.,
        lightship_distribution=[(0.,10.,1000.)],hydrostatics=[],cross_curves=[],stations=[],
        windage_area_m2=10.,windage_lever_m=2.,max_draught_m=2.5,max_trim_m=.5,max_list_rad=.1)
    vessel=Vessel.model_construct(id='projection-test',name='Fixture',nominal_teu=0,
        cells=[],stacks=[],hatch_covers=[],tanks=[],hull=hull)
    original=cache.vessel_physics(vessel)
    # The consumed fields are accessible directly; huge irrelevant table rows
    # must not be serialized through the HullModel during each engine build.
    original_dump=HullModel.model_dump
    def prohibit(self,*args,**kwargs):
        if self is hull:raise AssertionError('projected entire hydrostatic/KN hull')
        return original_dump(self,*args,**kwargs)
    monkeypatch.setattr(HullModel,'model_dump',prohibit)
    assert cache.vessel_physics(vessel)==original
