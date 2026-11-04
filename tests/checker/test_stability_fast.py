"""Cached-geometry selection and conservative direct-reference fallback."""
import importlib.util
from pathlib import Path

import pytest

from stowage.checker import stability


def test_manifest_canonicalization_is_shared_with_exact_parameter_geometry(monkeypatch):
    import json
    from types import SimpleNamespace
    from stowage.checker import stability_liquid
    calls = []
    monkeypatch.setattr(stability, 'StabilityGeometry', lambda params: SimpleNamespace())
    def metadata(params):
        calls.append(params)
        return {'inputs_hash': params['shape']}
    monkeypatch.setattr(stability_liquid, 'metadata', metadata)
    stability._geometry.cache_clear()
    try:
        first = stability._geometry('test', json.dumps({'shape': 'original'}))
        assert stability._geometry('test', json.dumps({'shape': 'original'})) is first
        assert first._stability_liquid_metadata == {'inputs_hash': 'original'}
        changed = stability._geometry('test', json.dumps({'shape': 'changed'}))
        assert changed._stability_liquid_metadata == {'inputs_hash': 'changed'}
        assert len(calls) == 2
    finally:
        stability._geometry.cache_clear()


@pytest.fixture(scope='module')
def departure():
    path=Path(__file__).resolve().parents[2]/'experiments/stability/kcs_departures.py'
    spec=importlib.util.spec_from_file_location('fast_departures_fixture',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from tests.checker.test_stability_policy_fixtures import supply_loading_policy
    return supply_loading_policy(module.build_departure('feeder_500')[0])


class Delegate:
    """Exact stand-in isolates dispatch/fallback from interpolation accuracy."""
    def __init__(self,direct):
        self.direct=direct
        self.calls=0
    def __getattr__(self,name):
        return getattr(self.direct,name)
    def equilibrium(self,*args,**kwargs):
        self.calls+=1
        return self.direct.equilibrium(*args,**kwargs)


def test_missing_cache_is_direct_without_creating_it(monkeypatch,departure):
    from stowage.checker import stability_tables
    monkeypatch.delenv('STOWAGE_STABILITY_DIRECT',raising=False)
    monkeypatch.setattr(stability_tables,'cached_geometry',lambda *args:None)
    result=stability.check_stability(departure,departure.onboard,call_index=0)
    assert result.ran,result.error
    assert result.metrics['geometry_table_used']==0.


def test_explicit_reference_bypasses_table_loader(monkeypatch,departure):
    from stowage.checker import stability_tables
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT','1')
    def unexpected(*args):
        raise AssertionError('direct reference must not load/build a table')
    monkeypatch.setattr(stability_tables,'cached_geometry',unexpected)
    result=stability.check_stability(departure,departure.onboard,call_index=0)
    assert result.ran,result.error
    assert result.metrics['geometry_table_used']==0.


def test_cached_geometry_dispatch_and_outside_grid_fallback(monkeypatch,departure):
    from stowage.checker import stability_tables
    monkeypatch.delenv('STOWAGE_STABILITY_DIRECT',raising=False)
    adapters=[]
    def load(direct,*args):
        adapters.append(Delegate(direct))
        return adapters[-1]
    monkeypatch.setattr(stability_tables,'cached_geometry',load)
    monkeypatch.setattr(stability,'_near_table_limit',lambda *args:False)
    result=stability.check_stability(departure,departure.onboard,call_index=0)
    assert result.ran,result.error
    assert result.metrics['geometry_table_used']==1.
    assert adapters[0].calls>0
    def outside(self,*args,**kwargs):
        raise ValueError('outside validated interpolation domain')
    monkeypatch.setattr(Delegate,'equilibrium',outside)
    direct=stability.check_stability(departure,departure.onboard,call_index=0)
    assert direct.ran,direct.error
    assert direct.metrics['geometry_table_used']==0.
    for key in ('gm_m','area_0_30_mrad','weather_ratio'):
        assert direct.metrics[key]==pytest.approx(result.metrics[key],abs=1e-10)


def test_near_limit_dispatch_recomputes_whole_report(monkeypatch,departure):
    from stowage.checker import stability_tables
    monkeypatch.delenv('STOWAGE_STABILITY_DIRECT',raising=False)
    monkeypatch.setattr(stability_tables,'cached_geometry',lambda direct,*args:Delegate(direct))
    monkeypatch.setattr(stability,'_near_table_limit',lambda *args:True)
    result=stability.check_stability(departure,departure.onboard,call_index=0)
    assert result.ran,result.error
    assert result.metrics['geometry_table_used']==0.
    assert result.metrics['geometry_table_limit_fallback']==1.


@pytest.mark.parametrize('key,margin',[('gm_m',.005),('containership_area_0_30_mrad',.0001),
    ('containership_gz_max_m',.005),('angle_gz_max_rad',.001),('weather_ratio',.01),
    ('draught_aft_m',.005)])
def test_near_criterion_boundary_requires_direct(key,margin):
    assert stability._near_table_limit({}, {key:margin})


def test_discrete_applicability_flag_does_not_trigger_boundary_guard():
    assert not stability._near_table_limit({}, {'weather_table_applicability':0.,'gm_m':1.})


def test_context_cache_invalidates_same_size_timestamp_edit(monkeypatch,tmp_path):
    """Content identity catches edits that a stat-only cache would miss."""
    import os
    from types import SimpleNamespace
    from stowage.vessel import fleet
    path=tmp_path/'test_feeder.toml'
    path.write_bytes(b'coefficient = 1\n')
    initial=path.stat()
    monkeypatch.setattr(fleet,'params_dir',lambda kind:tmp_path)
    monkeypatch.setattr(fleet,'list_fleet',lambda:['test_feeder'])
    hull=SimpleNamespace(lpp_m=100.,beam_m=20.,depth_m=10.)
    vessel=SimpleNamespace(id='test_feeder',slice_of=None,hull=hull)
    captured=[]
    def geometry(name,payload):
        captured.append(payload)
        return hull
    monkeypatch.setattr(stability,'_geometry',geometry)
    stability._parsed_parameter_payload.cache_clear()
    _,first=stability._context(vessel)
    _,second=stability._context(vessel)
    assert first==second=={'coefficient':1}
    assert stability._parsed_parameter_payload.cache_info().hits==1
    path.write_bytes(b'coefficient = 2\n')
    os.utime(path,ns=(initial.st_atime_ns,initial.st_mtime_ns))
    assert path.stat().st_size==initial.st_size
    assert path.stat().st_mtime_ns==initial.st_mtime_ns
    _,changed=stability._context(vessel)
    assert changed=={'coefficient':2}
    assert captured[-1]!=captured[0]
    changed['coefficient']=999
    assert stability._context(vessel)[1]=={'coefficient':2}
    assert stability._parsed_parameter_payload.cache_info().maxsize==12


def test_near_loading_limit_refines_only_the_floating_state():
    from types import SimpleNamespace
    from tests.checker.test_stability_policy_fixtures import supply_loading_policy
    hull=SimpleNamespace(max_draught_m=8.7,min_draught_fwd_m=1.,max_trim_m=3.,max_list_rad=.1)
    instance=SimpleNamespace(vessel=SimpleNamespace(hull=hull),port=SimpleNamespace(max_draught_m=8.7),
        rule_overrides={'iscode.loading.trim': {'minimum': -2.},
                        'iscode.loading.displacement': {'minimum': 1., 'maximum': 1e9}})
    supply_loading_policy(instance)
    floating=SimpleNamespace(t_aft_m=7.,t_fwd_m=8.686,trim_m=-1.686,heel_rad=0.)
    assert stability._floating_near_limit(instance,floating)
    floating.t_fwd_m=8.5
    assert not stability._floating_near_limit(instance,floating)
    floating.heel_rad=.099
    assert stability._floating_near_limit(instance,floating)
    margins={'draught_fwd_m':.014,'port_draught_m':.014,'gm_m':.005}
    exact_filtered={key:value for key,value in margins.items()
                    if not stability._is_equilibrium_loading_margin(key)}
    assert exact_filtered=={'gm_m':.005}
    assert stability._near_table_limit({},exact_filtered)


@pytest.mark.parametrize('failure_kind',['zip_crc','unreadable'])
def test_corrupt_optional_cache_restarts_direct_report(monkeypatch,departure,failure_kind):
    from zipfile import BadZipFile
    from stowage.checker import stability_tables
    monkeypatch.delenv('STOWAGE_STABILITY_DIRECT',raising=False)
    def corrupt(*args,**kwargs):
        if failure_kind=='zip_crc':
            raise BadZipFile('Bad CRC-32 for cached values.npy')
        raise OSError('optional cache is unreadable')
    monkeypatch.setattr(stability_tables,'cached_geometry',corrupt)
    report=stability.check_stability(departure,departure.onboard,call_index=0)
    assert report.ran,report.error
    assert report.metrics['geometry_table_used']==0.
    assert not any(v.rule_id.startswith('checker.') for v in report.violations)
