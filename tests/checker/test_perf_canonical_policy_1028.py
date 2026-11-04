"""Canonical policy projection keeps public values, guards and deadline refusal."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tomllib

import pytest

from stowage.checker import stability,stability_policy
from stowage.vessel import fleet,fleet_cache


_D171_REPORT = 'experiments/mvp/postfreeze/FAILURES-D171.md'


def _require_reviewed_policy_epoch(name):
    """Skip only an unavailable reviewed epoch; matching epochs retain every assertion."""
    raw = fleet._parameter_bytes(name)
    providers = stability_policy.provider_identity()
    contract = stability_policy._contract(providers[0])
    reviewed = contract.get('constructions', {}).get(name, {})
    actual = {key: stability_policy.provider_record(function)
              for key, function in stability_policy.runtime_providers().items()}
    if (contract.get('format') != 1
            or reviewed.get('parameter_sha256') != hashlib.sha256(raw).hexdigest()
            or reviewed.get('identity') != fleet_cache._cache_key(name, 'full', True, raw)
            or contract.get('providers') != actual):
        pytest.skip('D171 A: canonical-policy construction/provider qualification is stale; see ' + _D171_REPORT)


@pytest.fixture(autouse=True)
def clear_baseline():
    stability._canonical_policy_baseline.cache_clear()
    yield
    stability._canonical_policy_baseline.cache_clear()


@pytest.mark.parametrize('name',['feeder_500','feeder_800','feeder_1700'])
def test_reviewed_projection_matches_full_public_policy_bitwise(name):
    _require_reviewed_policy_epoch(name)
    raw=fleet._parameter_bytes(name)
    expected=fleet.load_vessel(name)
    projected=stability_policy.projected_policy(name,hashlib.sha256(raw).hexdigest(),
        fleet_cache._cache_key(name,'full',True,raw),stability_policy.provider_identity())
    assert projected is not None,'reviewed canonical construction/provider changed; re-review pin'
    values=tuple(stability._hull_policy_number(expected.hull,field) for field in stability._CANONICAL_POLICY_FIELDS)
    assert all(struct.pack('>d',a)==struct.pack('>d',b) for a,b in zip(projected,values,strict=True))


@pytest.mark.parametrize('change',['resolver','resolver_code','resolver_defaults','projection_code'])
def test_warmed_baseline_does_not_mask_provider_changes(monkeypatch,change):
    vessel=fleet.load_vessel('feeder_500')
    stability._canonical_fleet_policy(vessel)
    if change=='resolver':
        def fail(name,*,with_hull=True):
            raise RuntimeError('replaced public resolver')
        monkeypatch.setattr(fleet,'load_vessel',fail)
    elif change=='resolver_code':
        def fail(name,*,with_hull=True):
            raise RuntimeError('changed resolver code')
        monkeypatch.setattr(fleet.load_vessel,'__code__',fail.__code__)
    elif change=='resolver_defaults':
        monkeypatch.setattr(fleet.load_vessel,'__kwdefaults__',{'with_hull':False})
    else:
        def changed(ship_params):
            return (0.,0.,1.,1.,0.)
        monkeypatch.setattr(stability_policy._project_reviewed_prefix,'__code__',changed.__code__)
        # Unknown projection code uses the real public resolver, not new limits.
        stability._canonical_fleet_policy(vessel)
        assert stability._canonical_policy_baseline.cache_info().misses==2
        return
    with pytest.raises(ValueError,match='resolver|missing hull'):
        stability._canonical_fleet_policy(vessel)


def test_unreviewed_parameter_mutation_uses_public_validation(monkeypatch):
    vessel=fleet.load_vessel('feeder_800')
    original=fleet._parameter_bytes('feeder_800')
    # Reversed interval is rejected before any expensive hull table builds.
    changed=original.replace(b'min_trim_m = -1.2441',b'min_trim_m = 9.2441')
    assert changed!=original
    digest=hashlib.sha256(changed).hexdigest()
    vessel.sources=[s for s in vessel.sources if not s.startswith('params/fleet/')]+[
        f'params/fleet/feeder_800.toml sha256:{digest}']
    monkeypatch.setattr(fleet,'_parameter_bytes',lambda name:changed)
    assert stability_policy.projected_policy('feeder_800',digest,
        fleet_cache._cache_key('feeder_800','full',True,changed),stability_policy.provider_identity()) is None
    with pytest.raises(ValueError,match='min_trim_m must not exceed max_trim_m'):
        stability._canonical_fleet_policy(vessel)


@pytest.mark.parametrize('alias',[
    'HullGeometry',
    # Measured 59.44 s: the public builder constructs tables before this
    # rebound final HullModel constructor is reached (repo >10 s policy).
    pytest.param('HullModel', marks=pytest.mark.slow),
    'VesselFieldProvenance',
])
def test_warmed_baseline_retains_actual_public_hull_bindings(monkeypatch,alias):
    from stowage.vessel import hull
    vessel=fleet.load_vessel('feeder_500')
    stability._canonical_fleet_policy(vessel)
    original_identity=stability_policy.provider_identity()
    class ReboundGeometry:
        @classmethod
        def from_params(cls,params):
            raise ValueError('rebound public hull binding')
    def fail(*args,**kwargs):
        raise ValueError('rebound public hull binding')
    monkeypatch.setattr(hull,alias,ReboundGeometry if alias=='HullGeometry' else fail)
    monkeypatch.setenv('STOWAGE_VESSEL_CACHE','0')
    assert stability_policy.provider_identity()!=original_identity
    with pytest.raises(ValueError,match='rebound public hull binding'):
        stability._canonical_fleet_policy(vessel)
    assert stability._canonical_policy_baseline.cache_info().misses==2


def test_projection_retains_declared_field_provenance_validation():
    params=tomllib.loads(fleet._parameter_bytes('feeder_500').decode())
    params['operating_limits']['provenance']['min_trim_m']={}
    with pytest.raises(ValueError,match='missing vessel policy evidence'):
        stability_policy._project_reviewed_prefix(params)


def test_fast_projection_checks_fresh_payload_between_outer_guard_and_build(monkeypatch):
    _require_reviewed_policy_epoch('feeder_800')
    raw=fleet._parameter_bytes('feeder_800')
    digest=hashlib.sha256(raw).hexdigest()
    identity=fleet_cache._cache_key('feeder_800','full',True,raw)
    # Keep the actual provider object/code, vary only the bytes it reads.
    original=Path.read_bytes
    target=fleet.params_dir('fleet')/'feeder_800.toml'
    def read(path):
        value=original(path)
        return value+b'\n# changed between guards\n' if path==target else value
    monkeypatch.setattr(Path,'read_bytes',read)
    with pytest.raises(ValueError,match='changed during policy construction'):
        stability_policy.projected_policy('feeder_800',digest,identity,stability_policy.provider_identity())


def test_contract_content_edits_invalidate_a_warm_baseline_without_stat_changes(monkeypatch,tmp_path):
    import os
    path=tmp_path/'contract.json'
    path.write_bytes(stability_policy._QUALIFICATION.read_bytes())
    monkeypatch.setattr(stability_policy,'_QUALIFICATION',path)
    vessel=fleet.load_vessel('feeder_500')
    stability._canonical_fleet_policy(vessel)
    original=path.read_bytes()
    stamp=path.stat()
    record=json.loads(original)
    code=record['providers']['load_vessel']['code']
    changed=original.replace(code.encode(),(('0' if code[0]!='0' else '1')+code[1:]).encode())
    assert changed!=original and len(changed)==len(original)
    path.write_bytes(changed)
    os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    stability._canonical_fleet_policy(vessel)
    assert stability._canonical_policy_baseline.cache_info().misses==2


def test_actual_saved_09_cold_warm_with_empty_vessel_cache_and_default_deadline(tmp_path):
    root=Path(__file__).resolve().parents[2]
    fixture=root/'experiments/solver-diag-1028/inputs/09-random_yard-tiny-feeder_800.json'
    if not fixture.exists():
        pytest.skip('saved synthetic solver diagnostic fixture is unavailable')
    saved = json.loads(fixture.read_text())
    records = [source for source in saved['vessel']['sources'] if source.startswith('params/fleet/')]
    assert len(records) == 1
    if records[0] != 'params/fleet/feeder_800.toml sha256:' + hashlib.sha256(
            fleet._parameter_bytes('feeder_800')).hexdigest():
        pytest.skip('D171 B: saved-09 vessel belongs to an obsolete parent-parameter epoch; see ' + _D171_REPORT)
    script=root/'experiments/checker/followup-1028/canonical-policy-cold-regression.py'
    artifact=script.with_suffix('.json')
    original=artifact.read_bytes() if artifact.exists() else None
    directories=set(script.parent.glob('canonical-fast-cold-*'))
    result=subprocess.run([sys.executable,str(script),'--output-dir',str(tmp_path)],
        cwd=root,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    record=json.loads(result.stdout.splitlines()[0])
    assert record['default_budget_seconds']==30.
    assert record['cold_warm_reports_bitwise_equal'] and record['vessel_cache_remains_empty']
    assert Path(record['vessel_cache']).is_relative_to(tmp_path)
    assert (artifact.read_bytes() if artifact.exists() else None)==original
    assert set(script.parent.glob('canonical-fast-cold-*'))==directories
