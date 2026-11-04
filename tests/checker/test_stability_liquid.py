"""Vessel qualification gates actual-transfer acceleration, never geometry truth."""
from dataclasses import replace
import json
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import stability_liquid as liquid
from stowage.checker import stability_loading as loading
from tests.checker.test_stability_policy_fixtures import build_instance


@pytest.fixture
def dispatch(monkeypatch,tmp_path):
    monkeypatch.setattr(liquid,'CACHE_DIRECTORY',tmp_path)
    from stowage.checker import stability_liquid_batch
    monkeypatch.setattr(stability_liquid_batch,'prepare',lambda condition:None)
    liquid._read_manifest.cache_clear()
    solid = SimpleNamespace(shape='hull_sections')
    tank = loading.CorrectionTank('tank',1000.,5.,100.,solid)
    condition = loading.LoadingCondition(1e6,50.,0.,5.,.1,1e5,
                                        tanks=(tank,),actual_tanks=(tank,))
    table = object()
    calls = []
    def lookup(solid):
        calls.append(solid)
        return table
    monkeypatch.setattr(liquid.stability_liquid_tables,'lookup',lookup)
    monkeypatch.setattr(liquid.stability_liquid_tables,'_identity',lambda s:'table-key')
    from stowage.vessel.tanks import TankGeometry
    monkeypatch.setattr(TankGeometry,'from_params',staticmethod(lambda raw,**kwargs:solid))
    params = {'vessel':'synthetic','tanks':[{'id':'tank'}]}
    return SimpleNamespace(condition=condition,table=table,params=params,calls=calls)


def manifest(d, **updates):
    value = dict(metadata=liquid.metadata(d.params),
                 validation=dict(samples=4,gz=.1,area=.1,gm=.1),
                 tank_table_keys={'tank':'table-key'})
    value.update(updates)
    path = liquid.manifest_path(d.params)
    path.write_text(json.dumps(value))
    liquid._read_manifest.cache_clear()
    return value


def test_trusted_cached_metadata_avoids_rewalking_canonical_inputs(dispatch,monkeypatch):
    manifest(dispatch)
    value=liquid.metadata(dispatch.params)
    original_path=liquid.manifest_path(dispatch.params)
    def forbidden(*args,**kwargs):
        pytest.fail('cached geometry metadata must avoid the canonical parameter walk')
    monkeypatch.setattr(liquid,'metadata',forbidden)
    result=liquid.attach_tables(dispatch.condition,dispatch.params,_metadata_value=value)
    assert result.liquid_tables == {'tank':dispatch.table}
    assert liquid._manifest_path(value)==original_path


def test_missing_manifest_retains_direct_geometry_without_loading_tables(dispatch):
    result = liquid.attach_tables(dispatch.condition,dispatch.params)
    assert result is dispatch.condition
    assert not dispatch.calls


@pytest.mark.parametrize('validation', [
    dict(samples=3,gz=.1,area=.1,gm=.1),
    dict(samples=4,gz=.20001,area=.1,gm=.1),
    dict(samples=4,gz=.1,area=-.1,gm=.1),
    dict(samples=4,gz=.1,area=.1,gm=float('nan')),
    dict(samples=float('nan'),gz=.1,area=.1,gm=.1),
    dict(samples=float('inf'),gz=.1,area=.1,gm=.1),
    {},None,
])
def test_unqualified_manifest_retains_direct_geometry(dispatch,validation):
    manifest(dispatch,validation=validation)
    assert liquid.attach_tables(dispatch.condition,dispatch.params) is dispatch.condition
    assert not dispatch.calls


@pytest.mark.parametrize('content',['not json','[]','null','{}'])
def test_malformed_manifest_retains_direct_geometry(dispatch,content):
    liquid.manifest_path(dispatch.params).write_text(content)
    assert liquid.attach_tables(dispatch.condition,dispatch.params) is dispatch.condition


def test_qualified_manifest_attaches_each_distinct_tank_once(dispatch):
    manifest(dispatch)
    result = liquid.attach_tables(dispatch.condition,dispatch.params)
    assert result.liquid_tables == {'tank':dispatch.table}
    assert len(dispatch.calls) == 1
    assert result.mass_kg == dispatch.condition.mass_kg
    assert result.actual_tanks == dispatch.condition.actual_tanks


def test_offline_hook_still_requires_individual_tank_qualification(dispatch,monkeypatch):
    result = liquid.attach_tables(dispatch.condition,dispatch.params,require_manifest=False)
    assert result.liquid_tables == {'tank':dispatch.table}
    monkeypatch.setattr(liquid.stability_liquid_tables,'lookup',lambda s:None)
    result = liquid.attach_tables(dispatch.condition,dispatch.params,require_manifest=False)
    assert result.liquid_tables is None


def test_manifest_tank_identity_mismatch_retains_direct_tank(dispatch):
    manifest(dispatch,tank_table_keys={'tank':'obsolete-key'})
    result = liquid.attach_tables(dispatch.condition,dispatch.params)
    assert result.liquid_tables is None


@pytest.mark.parametrize('keys',[None,[], 'table-key'])
def test_malformed_manifest_tank_keys_retain_direct_geometry(dispatch,keys):
    manifest(dispatch,tank_table_keys=keys)
    result = liquid.attach_tables(dispatch.condition,dispatch.params)
    assert result.liquid_tables is None


def test_qualify_vessel_writes_bound_manifest_after_tank_qualification(dispatch):
    path = liquid.qualify_vessel(dispatch.params,dispatch.condition,
                                 dict(samples=4,gz=.1,area=.1,gm=.1))
    saved = json.loads(path.read_text())
    assert saved['metadata'] == liquid.metadata(dispatch.params)
    assert saved['tank_table_keys'] == {'tank':'table-key'}
    result = liquid.attach_tables(dispatch.condition,dispatch.params)
    assert result.liquid_tables == {'tank':dispatch.table}


def test_qualify_vessel_rejects_unqualified_individual_tank(dispatch,monkeypatch):
    monkeypatch.setattr(liquid.stability_liquid_tables,'lookup',lambda s:None)
    with pytest.raises(ValueError,match='not independently qualified'):
        liquid.qualify_vessel(dispatch.params,dispatch.condition,
                              dict(samples=4,gz=.1,area=.1,gm=.1))


def test_qualification_covers_inactive_declared_tanks(dispatch):
    dispatch.params['tanks'].append({'id':'inactive-tank'})
    condition = replace(dispatch.condition,tanks=(),actual_tanks=())
    path = liquid.qualify_vessel(dispatch.params,condition,
                                 dict(samples=4,gz=.1,area=.1,gm=.1))
    assert json.loads(path.read_text())['tank_table_keys'] == {
        'tank':'table-key','inactive-tank':'table-key'}


def test_manifest_metadata_mismatch_retains_direct_geometry(dispatch):
    manifest(dispatch,metadata={'vessel_sha256':'obsolete'})
    assert liquid.attach_tables(dispatch.condition,dispatch.params) is dispatch.condition


def test_only_physical_inputs_and_builder_invalidate_manifest(dispatch,monkeypatch):
    from stowage import versions
    original=liquid.manifest_path(dispatch.params)
    monkeypatch.setattr(versions,'component_versions',lambda:{'checker':'changed'})
    assert liquid.manifest_path(dispatch.params)==original
    assert liquid.manifest_path(dict(dispatch.params,fixed_loads=[{'mass_kg':100.}]))==original
    assert liquid.manifest_path(dispatch.params,vessel_hash='irrelevant-full-vessel-hash')==original
    assert liquid.manifest_path(dict(dispatch.params,tanks=[{'id':'different'}])) != original
    monkeypatch.setattr(liquid,'STABILITY_LIQUID_MANIFEST_VERSION',2)
    assert liquid.manifest_path(dispatch.params) != original


@pytest.mark.parametrize('field', ['offsets_sha256','family','reference_lpp_m'])
def test_hull_shape_changes_invalidate_loaded_liquid_certificate(dispatch,field):
    params=dict(dispatch.params,hull_form={'offsets_sha256':'abc','family':'offsets','reference_lpp_m':230.})
    first=liquid.manifest_path(params)
    changed=dict(params,hull_form=dict(params['hull_form'],**{field:'changed'}))
    assert liquid.manifest_path(changed) != first


def test_buoyant_coaming_change_invalidates_manifest(dispatch):
    envelope={'deck_z_m':9.,'hatch_coamings':[{'id':'one','z_max_m':10.}]}
    params=dict(dispatch.params,envelope=envelope)
    changed=dict(params,envelope=dict(envelope,hatch_coamings=[{'id':'one','z_max_m':11.}]))
    assert liquid.manifest_path(changed) != liquid.manifest_path(params)


def test_prose_and_nonbuoyant_annotations_do_not_invalidate_manifest(dispatch):
    params=dict(dispatch.params,hull={'depth_m':9.,'provenance':{'source':'old'}},
                hull_form={'family':'offsets','offsets_sha256':'abc','offsets_path':'old'},
                envelope={'deck_z_m':9.,'hatch_coamings':[{'id':'one','z_max_m':10.}],
                          'openings':[{'z_m':15.}]})
    changed=dict(params,hull={'depth_m':9.,'provenance':{'source':'new'},'assumption':'explanation'},
                 hull_form=dict(params['hull_form'],offsets_path='moved',source='updated citation'),
                 envelope={'deck_z_m':9.,'hatch_coamings':[{'id':'renamed','z_max_m':10.,'confidence':'high'}],
                           'openings':[{'z_m':20.}],'assumption':'new wording'},
                 tanks=[{'id':'tank','source':'updated','provenance':{'confidence':'high'}}],
                 fixed_loads=[{'mass_kg':999.}])
    assert liquid.manifest_path(changed)==liquid.manifest_path(params)


@pytest.mark.parametrize('outside',[False,True])
def test_actual_centroid_queries_use_table_or_direct_without_extrapolation(dispatch,monkeypatch,outside):
    row = SimpleNamespace(lcg_m=50.,tcg_m=0.,vcg_m=1.)
    solid = dispatch.condition.actual_tanks[0].geometry
    solid.sounding = lambda volume:row
    calls = []
    expected = np.array([51.,.2,1.1])
    def centroid(volume,heel,trim):
        calls.append(('table',volume,heel,trim))
        if outside:
            raise ValueError('outside certified grid')
        return expected
    def direct(solid,volume,heel,trim):
        calls.append(('direct',volume,heel,trim))
        return expected
    monkeypatch.setattr(loading,'liquid_centroid',direct)
    condition = replace(dispatch.condition,liquid_tables={'tank':SimpleNamespace(centroid=centroid)})
    actual = loading.liquid_cg(condition,.5,.02)
    shift = 1000.*5./condition.mass_kg*(expected-[50.,0.,1.])
    assert actual == pytest.approx(np.array([50.,0.,5.])+shift)
    assert [call[0] for call in calls] == (['table','direct'] if outside else ['table'])
    assert condition.liquid_usage == ({'direct':1} if outside else {'table':1})


@pytest.mark.parametrize('outside',[False,True])
def test_consumable_transfer_envelope_uses_qualified_table_or_direct(dispatch,monkeypatch,outside):
    tank = replace(dispatch.condition.tanks[0],variable=True)
    calls = []
    def table_transfer(heel,trim,*,tolerance_m4):
        calls.append('table')
        if outside:
            raise ValueError('outside certified grid')
        return 12.
    def direct_transfer(*args,**kwargs):
        calls.append('direct')
        return 12.
    from stowage.checker import stability_tank_transfer
    monkeypatch.setattr(stability_tank_transfer,'max_transfer',direct_transfer)
    condition = replace(dispatch.condition,tanks=(tank,),
        liquid_tables={'tank':SimpleNamespace(max_transfer=table_transfer)})
    result = loading.curve_correction(condition,[-.4],.02,method=1)
    assert result == pytest.approx([-.012])
    assert calls == (['table','direct'] if outside else ['table'])


@pytest.mark.parametrize('method,direct,liquid_direct,attached',[
    (None,False,False,True),('1',False,False,True),('2',False,False,False),
    ('1',True,False,False),('1',False,True,False),
])
def test_production_booklet_selection_and_table_dispatch(monkeypatch,method,direct,liquid_direct,attached):
    from stowage.checker import stability,stability_tables
    instance = build_instance()
    if method is None:
        monkeypatch.delenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD',raising=False)
    else:
        monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD',method)
    monkeypatch.setenv('STOWAGE_STABILITY_LIQUID_DIRECT','1' if liquid_direct else '0')
    calls=[]
    def stop(*args,**kwargs):
        raise RuntimeError('test stops after table dispatch')
    geometry=SimpleNamespace(equilibrium=stop)
    monkeypatch.setattr(stability,'_context',lambda v:(geometry,{}))
    monkeypatch.setattr(stability_tables,'cached_geometry',lambda *a:None)
    def load(*a,**kwargs):
        calls.append(('method',kwargs['free_surface_method']))
        return loading.LoadingCondition(1e6,50.,0.,5.,0.,0.)
    def attach(condition,*a,**kwargs):
        calls.append(('attach',None))
        return condition
    monkeypatch.setattr(stability,'loading_condition',load)
    monkeypatch.setattr(liquid,'attach_tables',attach)
    report=stability._check_stability(instance,instance.onboard,call_index=0,_force_direct=direct)
    assert calls == [('method',int(method or '1'))]+([('attach',None)] if attached else [])
    assert not report.ran
    assert 'test stops after table dispatch' in report.error


@pytest.mark.parametrize('builder',['manifest','tank'])
def test_legacy_manifest_cannot_cross_future_builder_version(dispatch,monkeypatch,builder):
    if builder=='manifest':
        monkeypatch.setattr(liquid,'STABILITY_LIQUID_MANIFEST_VERSION',2)
    else:
        monkeypatch.setattr(liquid.stability_liquid_tables,'STABILITY_LIQUID_TABLES_VERSION',2)
    assert liquid.migrate_legacy(dispatch.params)=='missing'
