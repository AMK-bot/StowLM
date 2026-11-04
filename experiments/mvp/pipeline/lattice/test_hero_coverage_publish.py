"""Add-only publication tests: real schemas, mocked identity/proof, no native work."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_json
from experiments.mvp.pipeline.lattice import hero_coverage_publish as publish
from experiments.mvp.pipeline.lattice import hero_coverage_queue, store


@pytest.fixture
def sample(tmp_path, monkeypatch):
    fixture=json.loads((publish.HERE/'fixtures/hero-replan.json').read_text())
    engine={name:'a'*64 for name in publish.IDENTITY_FIELDS}
    engine.update(passed=True,source_files=245,native_files=257)
    identity=dict(engine_version='0.13.0',engine_manifest_sha256=engine['manifest_sha256'],engine_manifest=engine,
        binding_source_files={'adapter.py':'b'*64},reader_source_files={'schema.py':'c'*64},
        gate_source_files={'batch.py':'d'*64},adapter_imported_for_identity=False,computed_on_frozen_code=False)
    identity['binding_source_sha256']=sha256_json(identity['binding_source_files'])
    identity['source_identity_sha256']=sha256_json(identity)
    profile=dict(name='standard',budget_s=300.,finalize_s=100.,engine_timeout_s=1800.,seed=0,config={'assignment_tries':1})
    row={name:deepcopy(fixture[name]) for name in ('instance','instance_sha256','plan','canonical_rules','computed_utc')}
    row.update(key=store.entry_key(row['instance_sha256'],row['canonical_rules']),status='checked_plan',
        confirmation_source='batch',engine_version='0.13.0',engine_manifest_sha256=engine['manifest_sha256'],
        binding_source_sha256=identity['binding_source_sha256'],binding_source_after_sha256=identity['binding_source_sha256'],
        profile=profile,binding_evidence=dict(engine_version='0.13.0',engine_manifest_sha256=engine['manifest_sha256'],
            native_manifest_sha256=engine['native_manifest_sha256'],engine_manifest=engine,engine_manifest_after=engine))
    gate=fixture['gate']
    row['gate']=dict(passed=True,instance_sha256=row['instance_sha256'],plan_sha256=sha256_json(row['plan']),
        checker={name:deepcopy(gate['checker'][name]) for name in ('passed','count','provenance','shut_out','violations')},
        rules=deepcopy(gate['rules']),gauges={name:deepcopy(gate['gauges'][name]) for name in
            ('passed','all_frames','outside_limits_steps','unavailable_steps','n_frames')},
        engine_identity=dict(passed=True,engine_version='0.13.0',engine_manifest_sha256=engine['manifest_sha256'],engine_manifest=engine))
    row['gate']['checker']['parts']={}
    stage,destination=tmp_path/'stage',tmp_path/'main'
    stage.mkdir();destination.mkdir()
    monkeypatch.setattr(publish,'HERE',tmp_path)
    monkeypatch.setattr(hero_coverage_queue,'source_identity',lambda:deepcopy(identity))
    def staged(value):
        value=deepcopy(value)
        value['key']=store.entry_key(value['instance_sha256'],value['canonical_rules'])
        raw=(canonical_json(value)+'\n').encode();key=value['key']
        (stage/(key+'.json')).write_bytes(raw)
        (stage/'catalogue.json').write_text(canonical_json({key:hashlib.sha256(raw).hexdigest()})+'\n')
        return key,raw
    key,raw=staged(row)
    def call(**overrides):
        kwargs=dict(staging_store=stage,store_dir=destination,expected_profile=profile,
                    coverage_start_identity=deepcopy(identity),coverage_end_identity=deepcopy(identity))
        kwargs.update(overrides)
        return publish.publish_staged(key,**kwargs)
    return SimpleNamespace(stage=stage,destination=destination,identity=identity,profile=profile,row=row,
        key=key,raw=raw,staged=staged,call=call)


def test_new_key_added_preserving_every_existing_entry_and_staged_bytes(sample):
    old=sample.destination/('f'*64+'.json');old.write_bytes(b'old bytes stay exact\n')
    old_sha=hashlib.sha256(old.read_bytes()).hexdigest()
    (sample.destination/'catalogue.json').write_text(canonical_json({'f'*64:old_sha})+'\n')
    stage_pin=(sample.stage/'catalogue.json').read_bytes()
    result=sample.call()
    assert result['status']=='published' and result['published'] and result['added_to_catalogue']
    assert old.read_bytes()==b'old bytes stay exact\n'
    assert (sample.stage/(sample.key+'.json')).read_bytes()==sample.raw
    assert (sample.stage/'catalogue.json').read_bytes()==stage_pin
    published=(sample.destination/(sample.key+'.json')).read_bytes()
    assert published==Path(result['publication_candidate_path']).read_bytes()
    saved=json.loads(published);provenance=saved.pop('coverage_provenance')
    assert saved==sample.row
    assert provenance['native_gates_repeated'] is False and provenance['computed_on_frozen_code'] is False
    assert provenance['adapter_free_publication']['binding_hooks_active'] is False
    catalogue=json.loads((sample.destination/'catalogue.json').read_text())
    assert catalogue['f'*64]==old_sha and catalogue[sample.key]==result['entry_sha256']


def test_existing_key_never_replaced_even_by_a_different_valid_staged_row(sample):
    old=b'previous authenticated row, deliberately different bytes\n'
    path=sample.destination/(sample.key+'.json');path.write_bytes(old)
    catalogue=canonical_json({sample.key:hashlib.sha256(old).hexdigest()})+'\n'
    (sample.destination/'catalogue.json').write_text(catalogue)
    result=sample.call()
    assert result['status']=='already_present' and not result['published'] and not result['added_to_catalogue']
    assert path.read_bytes()==old and (sample.destination/'catalogue.json').read_text()==catalogue


@pytest.mark.parametrize('bad',['instance','plan','rule','binding','gate_native','original_native','profile','tainted'])
def test_stale_or_changed_acceptance_is_withheld_without_main_writes(sample,bad):
    row=deepcopy(sample.row)
    if bad=='instance':row['instance']['id']='changed'
    elif bad=='plan':row['plan']['id']='changed'
    elif bad=='rule':row['canonical_rules'][0]['constraint']['min_bays']+=1
    elif bad=='binding':row['binding_source_after_sha256']='changed'
    elif bad=='gate_native':row['gate']['engine_identity']['passed']=False
    elif bad=='original_native':row['binding_evidence']['engine_manifest_after']['native_manifest_sha256']='changed'
    elif bad=='profile':row['profile']['config']['assignment_tries']=2
    elif bad=='tainted':row['plan']['provenance']['stubs']={'checker.check':'test taint'}
    key,_=sample.staged(row)
    with pytest.raises((ValueError,TypeError,RuntimeError)):
        publish.publish_staged(key,staging_store=sample.stage,store_dir=sample.destination,expected_profile=sample.profile,
            coverage_start_identity=sample.identity,coverage_end_identity=sample.identity)
    assert not list(sample.destination.glob('*.json'))


def test_source_start_end_and_publication_drift_all_withhold(sample,monkeypatch):
    changed=deepcopy(sample.identity);changed['gate_source_files']['batch.py']='e'*64
    changed.pop('source_identity_sha256');changed['source_identity_sha256']=sha256_json(changed)
    with pytest.raises(ValueError,match='start/end'):
        sample.call(coverage_start_identity=changed)
    with pytest.raises(ValueError,match='precompute'):
        sample.call(coverage_start_identity=None)
    snapshots=iter([sample.identity,changed])
    monkeypatch.setattr(hero_coverage_queue,'source_identity',lambda:next(snapshots))
    with pytest.raises(ValueError,match='during publication'):
        sample.call()
    assert not list(sample.destination.glob('*.json'))


def test_active_binding_hook_blocks_unhooked_publication(sample,monkeypatch):
    monkeypatch.setitem(publish.sys.modules,'experiments.mvp.pipeline.binding.hooks',SimpleNamespace(binding_active=lambda:True))
    with pytest.raises(ValueError,match='hooks must be inactive'):
        sample.call()


def test_staging_and_existing_catalogue_corruption_fail_closed(sample):
    path=sample.stage/(sample.key+'.json');path.write_bytes(sample.raw+b' ')
    with pytest.raises(ValueError,match='catalogue authentication'):
        sample.call()
    path.write_bytes(sample.raw)
    (sample.destination/(sample.key+'.json')).write_bytes(b'wrong')
    (sample.destination/'catalogue.json').write_text(canonical_json({sample.key:'0'*64}))
    with pytest.raises(ValueError,match='existing target entry'):
        sample.call()
    assert (sample.destination/(sample.key+'.json')).read_bytes()==b'wrong'


def test_catalogue_crash_is_recoverable_without_replacing_orphan_bytes(sample,monkeypatch):
    atomic=publish._atomic
    monkeypatch.setattr(publish,'_atomic',lambda *args:(_ for _ in ()).throw(OSError('simulated catalogue crash')))
    with pytest.raises(OSError):sample.call()
    path=sample.destination/(sample.key+'.json');original=path.read_bytes()
    monkeypatch.setattr(publish,'_atomic',atomic)
    result=sample.call()
    assert result['status']=='recovered_orphan' and result['published']
    assert path.read_bytes()==original


def test_concurrent_same_key_publishes_exactly_once(sample):
    with ThreadPoolExecutor(max_workers=2) as workers:
        results=list(workers.map(lambda _:sample.call(),range(2)))
    assert sorted(r['status'] for r in results)==['already_present','published']
    assert sum(r['published'] for r in results)==1


def hatch_row(sample):
    row=deepcopy(sample.row);row.pop('plan');row.pop('gate')
    row.update(status='proved_refusal',canonical_rules=[dict(schema_version='stowlm.reader/2',constraint=dict(type='one_port_per_hatch'))],
               proof=dict(reason='the physical hatch-panel capacity model is infeasible'))
    return row


@pytest.mark.parametrize('proof',[None,dict(proven_impossible=False),dict(proven_impossible=True,reason='allocation: INFEASIBLE')])
def test_hatch_requires_current_necessary_proof_and_unknown_is_withheld(sample,monkeypatch,proof):
    key,_=sample.staged(hatch_row(sample))
    monkeypatch.setattr(store,'_refusal_proof',lambda row:deepcopy(proof))
    kwargs=dict(staging_store=sample.stage,store_dir=sample.destination,expected_profile=sample.profile,
                coverage_start_identity=sample.identity,coverage_end_identity=sample.identity)
    if not proof or not proof.get('proven_impossible'):
        with pytest.raises(ValueError,match='not proved'):publish.publish_staged(key,**kwargs)
        assert not list(sample.destination.glob('*.json'))
    else:
        result=publish.publish_staged(key,**kwargs)
        saved=json.loads(Path(result['path']).read_text())
        assert saved['coverage_provenance']['refusal_validation']['necessary_proof']==proof
        assert saved['coverage_provenance']['refusal_validation']['native_checker_called'] is False


def test_hatch_orphan_retry_preserves_first_bytes_when_proof_clock_changes(sample,monkeypatch):
    key,_=sample.staged(hatch_row(sample))
    monkeypatch.setattr(store,'_refusal_proof',lambda row:dict(proven_impossible=True,reason='allocation: INFEASIBLE'))
    clock=iter([0.,1.,10.,19.]);monkeypatch.setattr(publish.time,'monotonic',lambda:next(clock))
    atomic=publish._atomic
    monkeypatch.setattr(publish,'_atomic',lambda *args:(_ for _ in ()).throw(OSError('catalogue crash')))
    kwargs=dict(staging_store=sample.stage,store_dir=sample.destination,expected_profile=sample.profile,
                coverage_start_identity=sample.identity,coverage_end_identity=sample.identity)
    with pytest.raises(OSError):publish.publish_staged(key,**kwargs)
    path=sample.destination/(key+'.json');first=path.read_bytes()
    monkeypatch.setattr(publish,'_atomic',atomic)
    result=publish.publish_staged(key,**kwargs)
    assert result['status']=='recovered_orphan' and path.read_bytes()==first
    assert json.loads(first)['coverage_provenance']['refusal_validation']['necessary_proof_wall_s']==1.


def test_path_escape_and_wrong_staging_pin_are_rejected(sample,tmp_path):
    with pytest.raises(ValueError,match='inside lattice'):
        sample.call(store_dir=tmp_path.parent/'outside')
    with pytest.raises(ValueError,match='independent pin'):
        sample.call(expected_staging_catalogue_sha256='0'*64)


@pytest.mark.parametrize('wrong_arithmetic',[False,True])
def test_nonhatch_refusal_requires_exact_current_necessary_facts(sample,monkeypatch,wrong_arithmetic):
    from experiments.mvp.reader import refusal_evidence
    row=deepcopy(sample.row);row.pop('plan');row.pop('gate')
    row.update(status='proved_refusal',canonical_rules=[dict(schema_version='stowlm.reader/2',
        constraint=dict(type='max_tiers',bay=None,zone='deck',max_tiers=0))])
    proof=dict(proven_impossible=True,demand_cells=143,open_cells=60,constraint=row['canonical_rules'][0])
    row['proof']=dict(arithmetic=deepcopy(proof))
    if wrong_arithmetic:row['proof']['arithmetic']['open_cells']=61
    monkeypatch.setattr(refusal_evidence,'build_static_proof',lambda *args:deepcopy(proof))
    key,_=sample.staged(row)
    kwargs=dict(staging_store=sample.stage,store_dir=sample.destination,expected_profile=sample.profile,
                coverage_start_identity=sample.identity,coverage_end_identity=sample.identity)
    if wrong_arithmetic:
        with pytest.raises(ValueError,match='exact current'):publish.publish_staged(key,**kwargs)
    else:
        result=publish.publish_staged(key,**kwargs)
        assert json.loads(Path(result['path']).read_text())['coverage_provenance']['refusal_validation']['necessary_proof']==proof
