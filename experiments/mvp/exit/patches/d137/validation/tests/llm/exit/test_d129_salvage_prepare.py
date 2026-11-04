"""CPU-only fresh-run salvage contract and immutable old-run evidence tests."""
import fcntl
import importlib.util
import json
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace

import pytest


@pytest.fixture
def subject(tmp_path,monkeypatch):
    root=Path(__file__).resolve().parents[3]
    source=root/'experiments/mvp/exit/patches/d129/salvage_prepare.py'
    spec=importlib.util.spec_from_file_location('_d129_salvage_prepare_test',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    old,new,parent=tmp_path/'old',tmp_path/'new',tmp_path/'adapter'
    old.mkdir();parent.mkdir();(old/'.runner.lock').touch()
    for name in ('adapters.safetensors','adapters.safetensors.provenance.json','adapter_config.json'):
        (parent/name).write_text(name)
    identity=module.adapter_identity(parent)
    corpus=tmp_path/'corpus';train=corpus/'train/small/ship/map.json'
    module._write(train,{'id':'train'})
    config=dict(corpus=str(corpus),gold=str(tmp_path/'gold'),out=str(old),stop_file=str(old/'deployment.cancel'),
                ballast_s=90,tiny=0,small=1,k=8,population=2,steps=120,audit_list=str(tmp_path/'audit-list.txt'))
    population=[dict(adapter=str(parent),learning_rate=.00005,method='sft')]
    module._write(old/'gen-000/instances.json',dict(population=population,
        adapter_identity={str(parent):identity},instances=[str(train)],source_pins={'old.py':'old'},config=config))
    module._write(old/'schedule.json',dict(generations={'0':[{'path':'train/small/ship/map.json'}]},
                                          used=['already-used']))
    selection=old/'gen-000/cases/000/selection.json'
    module._write(selection,dict(instance_path=str(train),passed=True,
        winner_record=str(old/'gen-000/cases/000/winner.record.json'),candidates=[]))
    module._write(old/'gen-000/cases/000/winner.record.json',dict(plan={'id':'plan'},report={'passed':True}))
    module._write(old/'gen-000/cases/000/decode-0/generation.json',dict(samples=[],candidates=[]))
    maps=[];rows=[]
    for i in range(30):
        path=corpus/f'val/small/ship/{i}.json';module._write(path,dict(id=i))
        maps.append(dict(path=str(path),sha256=module._sha(path)))
        rows.append(dict(instance_path=str(path),passed=i<20,weighted_moves=34.05 if i<20 else None))
    module._write(old/'audit-rule.json',dict(maps=maps,rule={'registered':True}))
    Path(config['audit_list']).write_text('\n'.join(row['path'] for row in maps)+'\n')
    module._write(old/'audit-baseline/summary.json',rows)
    module._write(old/'audit-baseline/baseline-binding.json',dict(adapter=identity,
        baseline_adapter=str(parent),checker_epoch='old',maps=maps))
    module._write(old/'baseline-timing.json',dict(audit_s=698.7))
    module._write(old/'gen-000/phase-status.json',dict(phases=[dict(name='decode',wall_s=100)]))
    state=dict(epoch={'checker.py':'new'},calls=[],drift=False,blocked=False)
    def training(selections,**kw):
        state['calls'].append(('training',selections,kw))
        if state['drift']:state['epoch']['checker.py']='during-check'
        module._write(kw['output_dir']/'receipt.json',{'stub':'checked'})
        return dict(status='blocked_no_current_winners' if state['blocked'] else 'ready',
            selections=[{'fresh':True}],winner_records=['fresh-record'],pairs=[],
            counts=dict(winners_kept=0 if state['blocked'] else 1))
    def audit(rows,**kw):
        state['calls'].append(('audit',rows,kw))
        return dict(rows=rows,original_measurement=dict(pass_count=20,mean_weighted_moves=34.05),
            current_epoch_reverification=dict(pass_count=20,mean_weighted_moves=34.05),
            changed_to_fail=0,changed_to_pass=0,unavailable=0)
    def receipt(*a,**kw):state['calls'].append(('receipt',a,kw))
    monkeypatch.setattr(module,'_services',lambda:dict(source_snapshot=lambda:dict(state['epoch']),
        checker_epoch=lambda:'current-checker',reverify_training_rows=training,
        reverify_audit_rows=audit,assert_training_receipt=receipt))
    return module,old,new,parent,state


def test_prepares_fresh_bound_rows_and_preserves_old_artifacts(subject):
    module,old,new,parent,state=subject
    before={str(p):module._sha(p) for p in old.rglob('*') if p.is_file()}
    marker=module.prepare(old,new,parent)
    assert marker['status']=='ready' and not marker['children_trained']
    assert marker['next_phase']=='inherit_only'
    assert marker['counts']=={'winners_kept':1}
    assert marker['config']['out']==str(new)
    assert marker['config']['stop_file']==str(new/'deployment.cancel')
    assert marker['prepared_sha256']==module._digest({k:v for k,v in marker.items() if k!='prepared_sha256'})
    assert before=={str(p):module._sha(p) for p in old.rglob('*') if p.is_file()}
    assert module._read(new/'schedule.json')==module._read(old/'schedule.json')
    assert module._read(new/'audit-baseline/baseline-binding.json')['checker_epoch']=='current-checker'
    assert module._read(new/'baseline-timing.json')['historical_audit']['audit_s']==698.7
    manifest=module._read(new/'gen-000/instances.json')
    assert manifest['source_pins']=={'checker.py':'new'}
    assert manifest['salvaged_from']['source_pins']=={'old.py':'old'}
    assert not (new/'gen-000/summary.json').exists()
    assert not list(new.glob('gen-000/child*'))
    assert state['calls'][0][2]['model_identity']=={'adapter':module.adapter_identity(parent)}
    assert any(call[0]=='receipt' for call in state['calls'])


@pytest.mark.parametrize('missing', ['gen-000/cases/000/selection.json',
    'gen-000/cases/000/decode-0/generation.json'])
def test_refuses_partial_generation_without_summary_requirement(subject,missing):
    module,old,new,parent,state=subject
    (old/missing).unlink()
    with pytest.raises(FileNotFoundError):module.prepare(old,new,parent)
    assert not state['calls']
    assert not (new/'salvage-prepared.json').exists()


def test_changed_parent_refused_before_cpu_work(subject):
    module,old,new,parent,state=subject
    (parent/'adapters.safetensors').write_text('different')
    with pytest.raises(ValueError,match='parent'):module.prepare(old,new,parent)
    assert not state['calls']


def test_old_runner_lock_refuses_salvage_without_signalling(subject):
    module,old,new,parent,state=subject
    with (old/'.runner.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError,match='old runner still active'):module.prepare(old,new,parent)
    assert not state['calls']


def test_schedule_must_keep_every_old_map_used(subject):
    module,old,new,parent,state=subject
    module._write(old/'schedule.json',dict(generations={'0':[]}))
    with pytest.raises(ValueError,match='reserve all'):module.prepare(old,new,parent)
    assert not state['calls']


def test_changed_frozen_map_refused(subject):
    module,old,new,parent,state=subject
    path=Path(module._read(old/'audit-rule.json')['maps'][0]['path']);path.write_text('{}')
    with pytest.raises(ValueError,match='audit map content'):module.prepare(old,new,parent)
    assert not state['calls']


def test_source_drift_during_recheck_never_publishes_ready_marker(subject):
    module,old,new,parent,state=subject;state['drift']=True
    with pytest.raises(RuntimeError,match='changed during salvage'):module.prepare(old,new,parent)
    assert not (new/'salvage-prepared.json').exists()


def test_blocked_rows_are_persisted_not_fabricated_as_children(subject):
    module,old,new,parent,state=subject;state['blocked']=True
    marker=module.prepare(old,new,parent)
    assert marker['status']=='blocked_no_current_winners'
    assert (new/'salvage-prepared.json').exists()
    assert not any(call[0]=='receipt' for call in state['calls'])


def test_second_prepare_reuses_only_identical_bound_epoch(subject):
    module,old,new,parent,state=subject
    marker=module.prepare(old,new,parent);before=len(state['calls'])
    assert module.prepare(old,new,parent)==marker
    assert len(state['calls'])==before+1 # receipt revalidated, no new checker calls
    state['epoch']['checker.py']='changed'
    with pytest.raises(RuntimeError,match='resume source/parent/epoch'):module.prepare(old,new,parent)


def test_refuses_nonempty_unrelated_output(subject):
    module,old,new,parent,state=subject
    new.mkdir();(new/'runner-output.json').write_text('{}')
    with pytest.raises(ValueError,match='unrelated artifacts'):module.prepare(old,new,parent)


def test_refuses_old_and_new_output_alias(subject):
    module,old,new,parent,state=subject
    with pytest.raises(ValueError,match='separate'):module.prepare(old,old,parent)


def test_tampered_prepared_marker_cannot_be_reused(subject):
    module,old,new,parent,state=subject
    module.prepare(old,new,parent)
    marker=module._read(new/'salvage-prepared.json');marker['counts']={'fabricated':99}
    module._write(new/'salvage-prepared.json',marker)
    with pytest.raises(ValueError,match='marker content'):module.prepare(old,new,parent)


def test_tampered_current_baseline_cannot_be_reused(subject):
    module,old,new,parent,state=subject
    module.prepare(old,new,parent)
    module._write(new/'audit-baseline/summary.json',[{'passed':True}]*30)
    with pytest.raises(ValueError,match='prepared output'):module.prepare(old,new,parent)


def test_cpu_preparation_uses_ledger_without_gpu_lease_and_restores_owner(subject):
    module,old,new,parent,state=subject
    calls=[]
    @contextmanager
    def ledger(out,**kw):
        calls.append((out,kw,resources.OWNER));yield
    def gpu_lock(*a,**kw):pytest.fail('CPU preparation must never take the GPU lease')
    resources=SimpleNamespace(OWNER='original-owner',_reservation=ledger,
                              reservation=gpu_lock,execution_lock=gpu_lock)
    with module._cpu_reservation(new/'salvage-compute',123,resources=resources):
        assert resources.OWNER.startswith('mvp-exit/d129-reverify-')
        marker=module.prepare(old,new,parent)
    assert marker['status']=='ready'
    assert resources.OWNER=='original-owner'
    assert len(calls)==1
    assert calls[0][1]==dict(cores=1,ram_gb=4,gpu=False,estimate_s=123)


def test_cpu_preparation_lock_refuses_duplicate_before_ledger_edit(subject):
    module,old,new,parent,state=subject
    new.mkdir()
    with (new/'.salvage-preparation.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        resources=SimpleNamespace(OWNER='original-owner',_reservation=lambda *a,**kw:
                                  pytest.fail('duplicate preparation touched ledger'))
        with pytest.raises(RuntimeError,match='another CPU salvage'):
            with module._cpu_reservation(new/'salvage-compute',123,resources=resources):pass
    assert resources.OWNER=='original-owner'


def test_cpu_preparation_exception_restores_owner(subject):
    module,old,new,parent,state=subject
    @contextmanager
    def ledger(*a,**kw):yield
    resources=SimpleNamespace(OWNER='original-owner',_reservation=ledger)
    with pytest.raises(ValueError,match='fixture failed'):
        with module._cpu_reservation(new/'salvage-compute',123,resources=resources):
            raise ValueError('fixture failed')
    assert resources.OWNER=='original-owner'


def test_d131_four_workers_defer_baseline_without_claiming_or_running_it(subject):
    module,old,new,parent,state=subject
    marker=module.prepare(old,new,parent,workers=4,defer_baseline=True)
    assert marker['training_rows_ready'] and marker['baseline_pending']
    assert marker['next_phase']=='await_host_baseline_then_inherit'
    assert marker['receipt_path'].endswith('reverification-d131/receipt.json')
    assert marker['baseline_counts']['current'] is None
    assert not (new/'audit-baseline/summary.json').exists()
    assert not (new/'audit-baseline/baseline-binding.json').exists()
    assert (new/'baseline-pending.json').exists()
    assert not any(call[0]=='audit' for call in state['calls'])
    assert state['calls'][0][2]['workers']==4


def test_d131_cpu_reservation_records_four_real_cores_and_eight_gb(subject):
    module,old,new,parent,state=subject;calls=[]
    @contextmanager
    def ledger(out,**kw):calls.append(kw);yield
    resources=SimpleNamespace(OWNER='original-owner',_reservation=ledger)
    with module._cpu_reservation(new/'salvage-compute',123,resources=resources,workers=4):pass
    assert calls==[dict(cores=4,ram_gb=8,gpu=False,estimate_s=123,
                        load_policy='cpu_reverification_d131')]


def _host_ready(subject):
    module,old,new,parent,state=subject
    marker=module.prepare(old,new,parent,workers=4,defer_baseline=True)
    registration=module._read(old/'audit-rule.json')
    rows=module._read(old/'audit-baseline/summary.json')
    # Own host artifacts can exist before or after CPU preparation completes.
    module._write(new/'audit-baseline/summary.json',rows)
    module._write(new/'audit-baseline/baseline-binding.json',dict(adapter=module.adapter_identity(parent),
        baseline_adapter=str(parent),checker_epoch='current-checker',maps=registration['maps']))
    module._write(new/'baseline-timing.json',dict(audit_s=55.0))
    module._write(new/'audit-rule.json',registration)
    spec=dict(parent=str(parent),parent_identity=module.adapter_identity(parent),current_sources=state['epoch'],
        maps=registration['maps'],audit_list_sha256=module._sha(marker['config']['audit_list']),
        recipe={**module.REBASELINE_RECIPE_CORE,'note':'extra explanation is preserved'})
    paths=[new/name for name in ('audit-baseline/summary.json','audit-baseline/baseline-binding.json',
                                 'baseline-timing.json','audit-rule.json')]
    ready=dict(schema='exit-d131-rebaseline-v1',status='ready',specification=spec,
               summary={'pass_count':20},outputs={str(path):module._sha(path) for path in paths})
    ready['ready_sha256']=module._digest(ready)
    module._write(new/'rebaseline-ready.json',ready)
    return marker,ready


def test_attach_host_baseline_joins_only_bound_ready_artifacts(subject):
    module,old,new,parent,state=subject
    marker,ready=_host_ready(subject)
    merged=module.attach_rebaseline(new)
    assert merged['status']=='ready' and not merged['baseline_pending']
    assert merged['next_phase']=='inherit_only'
    assert merged['baseline_counts']['original']['pass_count']==20
    assert merged['baseline_counts']['current']['pass_count']==20
    assert merged['baseline_host']==ready
    assert merged['prepared_sha256']==module._digest({k:v for k,v in merged.items() if k!='prepared_sha256'})


@pytest.mark.parametrize('damage',['source','parent','maps','output','marker_hash','audit_list'])
def test_attach_refuses_mismatched_host_baseline(subject,damage):
    module,old,new,parent,state=subject
    marker,ready=_host_ready(subject)
    if damage=='source':ready['specification']['current_sources']={'different':'epoch'}
    elif damage=='parent':ready['specification']['parent_identity']={'wrong':'adapter'}
    elif damage=='maps':ready['specification']['maps']=[]
    elif damage=='output':module._write(new/'audit-baseline/summary.json',[{'passed':True}])
    elif damage=='marker_hash':ready['ready_sha256']='wrong'
    else:Path(marker['config']['audit_list']).write_text('different\n')
    if damage!='marker_hash':
        ready.pop('ready_sha256',None);ready['ready_sha256']=module._digest(ready)
    module._write(new/'rebaseline-ready.json',ready)
    with pytest.raises(ValueError):module.attach_rebaseline(new)
    assert module._read(new/'salvage-prepared.json')['baseline_pending']


def test_deferred_preparation_does_not_overwrite_earlier_host_artifacts(subject):
    module,old,new,parent,state=subject
    new.mkdir()
    host={}
    for name in ('audit-baseline/summary.json','audit-baseline/baseline-binding.json',
                 'baseline-timing.json','rebaseline-ready.json','audit-rule.json'):
        module._write(new/name,{'host_owns':name});host[name]=(new/name).read_bytes()
    (new/'RESUME-HOST.sh').write_text('#!/bin/zsh\n# wait for verified receipts\n')
    host['RESUME-HOST.sh']=(new/'RESUME-HOST.sh').read_bytes()
    marker=module.prepare(old,new,parent,workers=4,defer_baseline=True)
    assert marker['training_rows_ready']
    assert all((new/name).read_bytes()==value for name,value in host.items())


@pytest.mark.parametrize('key,value',[
    ('batch_size',8),('temperature',.8),('samples',4),('layers',['production','securing']),
    ('ballast_s',15.),('checker_watchdog_s',30.),('backtracking',True),('greedy',1),
    ('max_tokens',10000),('solver_cargo_fallback',True),('lns',True),('batched',False)])
def test_attach_refuses_recipe_core_drift(subject,key,value):
    module,old,new,parent,state=subject
    marker,ready=_host_ready(subject)
    ready['specification']['recipe'][key]=value
    ready.pop('ready_sha256');ready['ready_sha256']=module._digest(ready)
    module._write(new/'rebaseline-ready.json',ready)
    with pytest.raises(ValueError,match='recipe core mismatch'):module.attach_rebaseline(new)
    assert module._read(new/'salvage-prepared.json')['baseline_pending']


def test_attach_requires_all_core_fields_but_allows_extra_explanation(subject):
    module,old,new,parent,state=subject
    marker,ready=_host_ready(subject)
    ready['specification']['decoded_source_pins']={'old-operational.py':'reviewed-old'}
    ready['specification']['compatibility_receipt']={'reason':'operational-only edits'}
    ready.pop('ready_sha256');ready['ready_sha256']=module._digest(ready)
    module._write(new/'rebaseline-ready.json',ready)
    assert module.attach_rebaseline(new)['status']=='ready'
    ready['specification']['recipe'].pop('batch_size')
    ready.pop('ready_sha256');ready['ready_sha256']=module._digest(ready)
    module._write(new/'rebaseline-ready.json',ready)
    # The prior attachment now correctly also sees its bound host file changed.
    with pytest.raises(ValueError):module.attach_rebaseline(new)


def test_attach_refuses_missing_recipe_core_field_before_any_merge(subject):
    module,old,new,parent,state=subject
    marker,ready=_host_ready(subject)
    ready['specification']['recipe'].pop('batch_size')
    ready.pop('ready_sha256');ready['ready_sha256']=module._digest(ready)
    module._write(new/'rebaseline-ready.json',ready)
    with pytest.raises(ValueError,match='recipe core mismatch: batch_size'):
        module.attach_rebaseline(new)


def test_attach_cli_is_independent_and_never_takes_gpu_or_heavy_cpu_lease(subject,monkeypatch,capsys):
    module,old,new,parent,state=subject
    _host_ready(subject)
    monkeypatch.setattr(module,'_cpu_reservation',lambda *a,**kw:
                        pytest.fail('quick attach must not reserve a heavy/GPU job'))
    assert module.main(['--attach-rebaseline',str(new)])==0
    assert json.loads(capsys.readouterr().out)['status']=='ready'


def test_prepare_cli_still_requires_all_preparation_inputs(subject):
    module,old,new,parent,state=subject
    with pytest.raises(SystemExit) as error:module.main(['--source-out',str(old)])
    assert error.value.code==2


def _close_original_source(module,old):
    (old/'.deployment.lock').touch()
    (old/'deployment.cancel').write_text('do not launch\n')
    module._write(old/'deployment-status.json',dict(state='failed',returncode=1))
    module._write(old/'compute.json',dict(status='failed',actual_wall_s=2993.6))


def test_d131_terminal_source_allows_another_read_only_reader_and_preserves_inputs(subject):
    module,old,new,parent,state=subject
    _close_original_source(module,old)
    before={str(p):module._sha(p) for p in old.rglob('*') if p.is_file()}
    with (old/'.runner.lock').open('a+') as reader:
        fcntl.flock(reader,fcntl.LOCK_EX|fcntl.LOCK_NB)
        marker=module.prepare(old,new,parent,workers=3,defer_baseline=True)
    assert marker['training_rows_ready']
    assert marker['closed_source_read_lease']['deployment_state']=='failed'
    assert marker['workers']==3
    assert '3 new CPU workers' in marker['capacity_choice']
    assert marker['output_sha256'][str(old/'deployment-status.json')]==module._sha(old/'deployment-status.json')
    assert before=={str(p):module._sha(p) for p in old.rglob('*') if p.is_file()}


@pytest.mark.parametrize('unsafe',['cancel_missing','deployment_running','compute_running','controller_held','not_deferred'])
def test_d131_busy_source_refuses_without_complete_terminal_proof(subject,unsafe):
    module,old,new,parent,state=subject
    _close_original_source(module,old)
    if unsafe=='cancel_missing':(old/'deployment.cancel').unlink()
    elif unsafe=='deployment_running':module._write(old/'deployment-status.json',dict(state='running'))
    elif unsafe=='compute_running':module._write(old/'compute.json',dict(status='running'))
    with (old/'.runner.lock').open('a+') as reader,(old/'.deployment.lock').open('a+') as controller:
        fcntl.flock(reader,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if unsafe=='controller_held':fcntl.flock(controller,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError):
            module.prepare(old,new,parent,workers=3,defer_baseline=unsafe!='not_deferred')
    assert not state['calls']
    assert not (new/'salvage-prepared.json').exists()


def test_d131_terminal_source_metadata_change_invalidates_read_only_handoff(subject,monkeypatch):
    module,old,new,parent,state=subject
    _close_original_source(module,old)
    services=module._services();original=services['reverify_training_rows']
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        module._write(old/'deployment-status.json',dict(state='completed',returncode=0))
        return result
    services['reverify_training_rows']=changed
    monkeypatch.setattr(module,'_services',lambda:services)
    with (old/'.runner.lock').open('a+') as reader:
        fcntl.flock(reader,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError,match='changed during salvage'):
            module.prepare(old,new,parent,workers=3,defer_baseline=True)
    assert not (new/'salvage-prepared.json').exists()
