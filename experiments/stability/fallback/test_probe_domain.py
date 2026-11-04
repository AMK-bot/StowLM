"""Probe-only assembly-key bypass never writes caches or accepts changed inputs."""
from hashlib import sha256
import importlib.util
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('fallback_safe_probe',Path(__file__).with_name('probe_domain_case.py'))
probe=importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.fixture
def cache(tmp_path,monkeypatch):
    from stowage.model.fixtures import build_instance
    from stowage.vessel import fleet,fleet_cache
    monkeypatch.setattr(fleet,'_parameter_bytes',lambda name:b'exact-current-parameters')
    monkeypatch.setattr(fleet_cache,'_cache_key',lambda *args:'current-key')
    monkeypatch.setattr(fleet_cache,'_cache_dir',lambda:tmp_path)
    vessel=build_instance().vessel
    source='params/fleet/ship.toml sha256:'+sha256(b'exact-current-parameters').hexdigest()
    vessel.sources=[source]
    return tmp_path,vessel,source


def test_probe_requires_explicit_authorization_for_assembly_key_bypass(cache):
    directory,vessel,source=cache
    old=directory/'ship.full.hull.previous-key.json'
    old.write_text(vessel.model_dump_json())
    with pytest.raises(probe.StopProbe):
        probe.load_cached_only('ship',with_hull=True)


def test_authorized_probe_bypass_binds_exact_params_and_never_writes(cache):
    directory,vessel,source=cache
    old=directory/'ship.full.hull.previous-key.json'
    old.write_text(vessel.model_dump_json())
    before={path.name:path.read_bytes() for path in directory.iterdir()}
    events=[]
    loaded=probe.load_cached_only('ship',with_hull=True,allow_assembly_key_bypass=True,events=events)
    assert loaded==vessel
    assert events[0]['exact_parameter_source']==source
    assert events[0]['sha256']==sha256(before[old.name]).hexdigest()
    assert events[0]['no_cache_write'] is True
    assert {path.name:path.read_bytes() for path in directory.iterdir()}==before


def test_probe_rejects_cached_vessel_with_changed_raw_parameters(cache):
    directory,vessel,_=cache
    vessel.sources=['params/fleet/ship.toml sha256:old-parameters']
    (directory/'ship.full.hull.previous-key.json').write_text(vessel.model_dump_json())
    with pytest.raises(probe.StopProbe):
        probe.load_cached_only('ship',with_hull=True,allow_assembly_key_bypass=True)


def test_current_key_does_not_need_bypass(cache):
    directory,vessel,_=cache
    (directory/'ship.full.hull.current-key.json').write_text(vessel.model_dump_json())
    events=[]
    assert probe.load_cached_only('ship',with_hull=True,events=events)==vessel
    assert not events
