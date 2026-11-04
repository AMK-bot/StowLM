"""Fail-closed rule epoch and generator population admission."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent


def module(name):
    sys.path.insert(0, str(HERE))
    try:
        spec = importlib.util.spec_from_file_location('logic_' + name, HERE / (name + '.py'))
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        return value
    finally:
        sys.path.remove(str(HERE))


def test_freeze_refuses_missing_required_rule_ids(tmp_path, monkeypatch):
    freeze = module('freeze')
    monkeypatch.delenv('STOWAGE_VESSEL_CACHE_DIR', raising=False)
    source = tmp_path/'src/stowage'
    (source/'rules').mkdir(parents=True)
    (source/'rules/catalogue.toml').write_text('schema_version=1\n[[rules]]\nid="unrelated"\n')
    (source/'versions.py').write_text('SCHEMA_VERSION="1.9"\n')
    with pytest.raises(ValueError, match='required rule'):
        freeze.create_manifest(tmp_path, ['src'])


def test_medium_regeneration_requires_c2_screen():
    regen = module('regen')
    from eval.splits import load_manifest
    release = load_manifest(regen.V2_MANIFEST)
    entry = next(row for row in release['entries'] if row['spec']['rung']=='medium')
    assert regen._release_config(entry, 1)['c2_screened'] is True


def seed_rule_source(root):
    source=HERE.parents[1]/'src/stowage'
    for relative in ('versions.py','rules/catalogue.toml'):
        target=root/'src/stowage'/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source/relative).read_bytes())


def test_frozen_leader_rule_policy_cannot_change(tmp_path, monkeypatch):
    policy=module('rule_policy')
    seed_rule_source(tmp_path)
    path=tmp_path/'policy.json'
    path.write_bytes(policy.REQUIRED_RULES.read_bytes())
    monkeypatch.setattr(policy,'REQUIRED_RULES',path)
    pin=policy.enforce_rule_policy(tmp_path)
    payload=json.loads(path.read_bytes()); payload['description']='leader updated after freeze'
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='frozen rule epoch'):
        policy.enforce_rule_policy(tmp_path,pin)


def test_rule_policy_requires_current_minimum_schema(tmp_path):
    policy=module('rule_policy')
    seed_rule_source(tmp_path)
    (tmp_path/'src/stowage/versions.py').write_text('SCHEMA_VERSION="1.8"\n')
    with pytest.raises(ValueError,match='below required minimum'):
        policy.enforce_rule_policy(tmp_path)


@pytest.mark.parametrize('fault', ['missing_threshold','unfilled_threshold','missing_yield','low_yield','wrong_epoch','diagnostic'])
def test_g1_approval_rejects_unapproved_or_unmeasured_yield(fault):
    policy=module('rule_policy')
    summary=dict(g1_met=True,epoch_id='e',min_yield={'tiny':.5},solve_yield={'tiny':.75})
    if fault=='missing_threshold':summary.pop('min_yield')
    if fault=='unfilled_threshold':summary['min_yield']['tiny']=None
    if fault=='missing_yield':summary.pop('solve_yield')
    if fault=='low_yield':summary['solve_yield']['tiny']=.25
    if fault=='wrong_epoch':summary['epoch_id']='old'
    if fault=='diagnostic':summary['diagnostic']=True
    with pytest.raises(ValueError):policy.validate_g1_approval(summary,'e',['tiny'])


def post_fixture(monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from stowage.versions import component_versions,RANDOM_YARD_VERSION
    from stowage.generate.instances_v2_random import RANDOM_V2_VERSION
    from stowage.generate.instances_v3 import CAMPAIGN_VERSION
    from stowage.vessel.fleet_conditions import CONDITION_VERSION
    import stowage.generate.background_acceptance as background
    monkeypatch.setattr(background,'requires_background_gate',lambda *a,**k:True)
    versions={**component_versions(),'random_yard':RANDOM_YARD_VERSION,'random_yard_v2':RANDOM_V2_VERSION,
        'slice_condition':CONDITION_VERSION,'instances_campaign':CAMPAIGN_VERSION}
    box=SimpleNamespace(gross_mass_kg=28000)
    instance=SimpleNamespace(provenance=SimpleNamespace(versions=versions),vessel=object(),size_class='medium',containers=[box])
    base=dict(sources={'src/stowage/example.py':'a'*64},components=component_versions(),condition_version=CONDITION_VERSION)
    input_sha=hashlib.sha256(json.dumps(base,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    physics={**base,'sha256':input_sha,'input_sha256':input_sha,'c2_screened':True}
    physics['sha256']=hashlib.sha256(json.dumps(physics,sort_keys=True).encode()).hexdigest()
    audit=dict(instance_versions=dict(versions),physics=physics,c2_screened=True,background_last=dict(accepted=True,physics_sha256=input_sha))
    return instance,audit,dict(generator='random_yard',size='medium',c2_screened=True),dict(files=dict(base['sources']))


@pytest.mark.parametrize('fault',['generator','condition','campaign','mass','certificate','source','c2'])
def test_post_regeneration_audit_rejects_stale_or_unwitnessed_instance(monkeypatch,fault):
    audit_api=module('mix_audit')
    instance,audit,config,frozen=post_fixture(monkeypatch)
    if fault=='generator':instance.provenance.versions['random_yard']='old'
    if fault=='condition':instance.provenance.versions['slice_condition']='old'
    if fault=='campaign':instance.provenance.versions['instances_campaign']='old'
    if fault=='mass':instance.containers[0].gross_mass_kg=40000
    if fault=='certificate':audit['background_last']['physics_sha256']='old'
    if fault=='source':frozen['files']['src/stowage/example.py']='b'*64
    if fault=='c2':config['c2_screened']=False
    with pytest.raises(ValueError):audit_api.audit_instance(instance,audit,config,frozen=frozen)


def test_post_regeneration_audit_accepts_exact_current_binding(monkeypatch):
    audit_api=module('mix_audit')
    instance,audit,config,frozen=post_fixture(monkeypatch)
    assert audit_api.audit_instance(instance,audit,config,frozen=frozen)['passed'] is True


def test_mix_audit_retains_allocated_rejected_missing_and_small_heldout_panel():
    from types import SimpleNamespace
    from eval.splits import load_manifest,spec_key
    regen=module('regen'); mix=module('mix_audit')
    release=load_manifest(regen.V2_MANIFEST)
    entries=[row for row in release['entries'] if row['split']=='test' and row['test_family']=='heldout_slice'][:3]
    assert len(entries)==3
    records=[]
    for i,entry in enumerate(entries[:2]):
        records.append(dict(job=dict(allocated=entry),result=dict(audit=dict(status='accepted' if i==0 else 'rejected',attempts=i+1,rejections={'c1':i}))))
    box=SimpleNamespace(id='box',gross_mass_kg=26000,dg=SimpleNamespace(imdg_class='3',un_number=1263),reefer=False,oog=None,iso_type='22G1')
    instances={spec_key(entries[0]['spec']):SimpleNamespace(containers=[box],load_list=['box'])}
    summary=mix.summarize_mix(dict(entries=entries),records,instances)
    group=next(iter(summary['groups'].values()))
    assert (group['allocated'],group['accepted'],group['rejected'],group['not_generated'])==(3,1,1,1)
    assert group['requested']['mass_mean_kg'] is None
    assert group['emitted']['dg_un_counts']=={'1263':1}
    assert summary['backfill'] is False and summary['warnings']


def test_publication_cannot_pin_replacement_under_returned_producer_claims(tmp_path,monkeypatch):
    regen=module('regen')
    from eval.snapshot_io import FileSnapshot
    import stowage.io
    job=regen.make_jobs(regen.build_release('e'),tmp_path,True,2,1,'e')[0]
    root=Path(job['root'])
    source=root/'.producer/random_yard/tiny/instance.json'
    source.parent.mkdir(parents=True)
    source.write_text('{"schema_version":"1.9","family":{}}')
    source.with_suffix('.audit.json').write_text('{"status":"accepted","diagnostic":true}')
    class Fake:
        def __init__(self,family):self.family=family
        def model_copy(self,*,update):return Fake(update['family'])
    monkeypatch.setattr(FileSnapshot,'model',lambda *a,**k:Fake({}))
    monkeypatch.setattr(stowage.io,'save_instance',lambda instance,path:Path(path).write_text(json.dumps({'family':instance.family})))
    producer=dict(path=str(source),audit=dict(status='accepted',instance_sha256=regen.sha256_file(source)))
    with pytest.raises(ValueError,match='producer audit'):
        regen._publish_bound(job,producer)


def test_population_mix_separates_physical_families_inside_one_heldout_panel():
    from types import SimpleNamespace
    from eval.splits import load_manifest,spec_key
    from stowage.conventions import canonical_vessel_id
    regen=module('regen'); mix=module('mix_audit')
    release=load_manifest(regen.V2_MANIFEST)
    pool=[row for row in release['entries'] if row['split']=='test' and row['test_family']=='heldout_slice']
    def family(row):
        s=row['spec']
        return (s['rung'],canonical_vessel_id(s['vessel']),s['generator'],s['port'],s['blocking_ratio'] if s['generator']=='random_yard' else s['noise'])
    first=pool[0]
    second=next(row for row in pool if row['spec']['rung']==first['spec']['rung'] and family(row)!=family(first))
    entries=[first,second]
    records=[dict(job=dict(allocated=row),result=dict(audit=dict(status='accepted',attempts=1,rejections={}))) for row in entries]
    instances={}
    for entry,mass in zip(entries,(12000,28000)):
        box=SimpleNamespace(id='box',gross_mass_kg=mass,dg=None,reefer=False,oog=None,iso_type='22G1')
        instances[spec_key(entry['spec'])]=SimpleNamespace(containers=[box],load_list=['box'])
    result=mix.summarize_mix(dict(entries=entries),records,instances)
    assert len(result['groups'])==1  # Existing held-out panel rollup remains.
    families=list(result['per_family'].values())
    assert len(families)==2
    assert sorted((g['allocated'],g['accepted'],g['emitted']['mass_mean_kg']) for g in families)==[(1,1,12000),(1,1,28000)]
    assert all(g['requested']['status']=='raw_draw_metrics_unavailable' for g in families)
    assert result['warnings']
