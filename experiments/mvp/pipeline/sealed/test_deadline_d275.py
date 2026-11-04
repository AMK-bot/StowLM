"""D275 staged deadline semantics; canonical study modules stay unchanged."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.sealed.common import atomic_json, object_hash, read_json, sha256
from experiments.mvp.pipeline.sealed.test_instructions import facts
from experiments.mvp.pipeline.sealed.instructions import select_rule
from experiments.mvp.pipeline.sealed.test_worker import audit


def staged(name):
    full_name = 'experiments.mvp.pipeline.sealed.d275_'+name
    spec = importlib.util.spec_from_file_location(full_name, Path(__file__).parent/'fixtures/d275'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def modules():
    return staged('driver'), staged('worker')


def job(**updates):
    return dict(map_id='synthetic', cohort='tiny15', arm='pipeline', mode='dry-run',
                sha256='a'*64, path='not-opened', **updates)


def metrics(**updates):
    return dict(child_pid=os.getpid()+10000, child_pgid=os.getpid()+10000,
        child_timed_out=True, child_cleanup_failed=False, child_timeout_s=10.,
        child_elapsed_s=10.1, timeout_s=10., **updates)


def checked_event(plan=None, *, lifts=7., checked=True):
    plan = plan or {'id':'published-plan'}
    return dict(stage='engine', kind='incumbent', t_s=.01,
        data=dict(checked=checked, plan=plan, plan_sha256=object_hash(plan), lifts=lifts))


def test_clean_planning_deadline_is_scientific_no_plan_without_error(modules, monkeypatch, tmp_path):
    driver, worker = modules
    def expired(*args, **kwargs):
        raise driver.ChildProcessFailure('wall elapsed', metrics())
    monkeypatch.setattr(driver,'_owned_process',expired)
    row = driver.run_child(job(),tmp_path,timeout_s=10.)
    assert row['status']=='deadline_exceeded' and row['message']=='no plan within the time budget'
    assert row['harness_error'] is False and row['passed'] is False
    assert row['plan'] is None and row['deadline_with_incumbent'] is False
    assert row['proved_infeasible'] is False and 'error' not in row
    assert read_json(tmp_path/'deadline-row.json')==row


@pytest.mark.parametrize('kind',['cleanup_failed','crash','prepass'])
def test_cleanup_crash_and_prepass_deadline_remain_harness_errors(modules, monkeypatch, tmp_path,kind):
    driver, worker=modules
    evidence=metrics()
    if kind=='cleanup_failed':
        evidence['child_cleanup_failed']=True
    elif kind=='crash':
        evidence['child_timed_out']=False
    def expired(*args,**kwargs):
        raise driver.ChildProcessFailure('cannot complete',evidence)
    monkeypatch.setattr(driver,'_owned_process',expired)
    with pytest.raises(driver.ChildProcessFailure):
        driver.run_child(job(),tmp_path,prepare=kind=='prepass')
    assert not (tmp_path/'deadline-row.json').exists()


def test_only_complete_parent_observed_checked_publications_inside_wall_are_selected(modules,tmp_path):
    driver,worker=modules
    (tmp_path/'pipeline').mkdir()
    first=checked_event(lifts=9.)
    unchecked=checked_event({'id':'unchecked'},lifts=1.,checked=False)
    late=checked_event({'id':'late'},lifts=0.)
    events=tmp_path/'pipeline/events.jsonl'
    events.write_text(json.dumps(first)+'\n'+json.dumps(unchecked)+'\n{"partial":')
    observed={}
    driver._observe_incumbents(job(),tmp_path,8.,10.,observed)
    events.write_text(json.dumps(first)+'\n'+json.dumps(late)+'\n')
    driver._observe_incumbents(job(),tmp_path,10.01,10.,observed)
    row=driver._deadline_result(job(),tmp_path,metrics(),observed)
    assert row['plan']==first['data']['plan']
    assert row['deadline_with_incumbent'] is True and row['passed'] is False
    assert row['first_checked_wall_s']==8.
    assert len(observed)==1


def test_parent_wall_overrides_optimistic_runner_timestamp(modules,monkeypatch,tmp_path):
    driver,worker=modules
    (tmp_path/'pipeline').mkdir()
    (tmp_path/'pipeline/events.jsonl').write_text(json.dumps(checked_event())+'\n')
    monkeypatch.setattr(driver.time,'monotonic',lambda:100.1)
    observed={}
    driver._observe_incumbents(job(),tmp_path,9.,10.,observed,parent_started=90.)
    assert observed=={}


def recovery_setup(modules,monkeypatch,tmp_path,*,with_plan=True):
    driver,worker=modules
    raw=facts()
    raw['load_list']=['BOX1']
    instance=SimpleNamespace(id='synthetic',model_dump=lambda **kw:raw)
    rules=[select_rule(raw,i) for i in range(4)]
    request=dict(job(),expected_rule=rules[0],calibration_rules=rules,deadline_recovery=True)
    publication=dict(plan={'id':'published-plan'},lifts=7.,published_wall_s=8.,
                     publication_sha256=object_hash(checked_event()),validation_source='runner_checked_incumbent')
    (tmp_path/'pipeline').mkdir()
    (tmp_path/'pipeline/events.jsonl').write_text(json.dumps(checked_event())+'\n')
    observed={'candidate':publication} if with_plan else {}
    row=driver._deadline_result(request,tmp_path,metrics(),observed)
    calls=[]
    def load(job,*,phase):
        calls.append(('load',phase))
        return instance
    def fresh(instance,plan,expected):
        calls.append(('native_audit',plan))
        return dict(audit(),final_cells={'BOX1':['0010182']})
    def counter(instance,plan,rule):
        return dict(counterfactual_rule=rule,counterfactual_rule_sha256=object_hash(rule),
            counterfactual_instruction_recheck=rule['constraint']['type']!='max_tiers',
            counterfactual_instruction_violations=['cap binds'] if rule['constraint']['type']=='max_tiers' else [])
    monkeypatch.setattr(worker,'_load',load)
    monkeypatch.setattr(worker,'_gate',lambda *a:dict(in_envelope=True))
    monkeypatch.setattr(worker,'_audit',fresh)
    monkeypatch.setattr(worker,'_counterfactual',counter)
    monkeypatch.setattr(worker,'_pipeline',lambda *a:pytest.fail('deadline recovery reran a planner'))
    monkeypatch.setattr(worker,'_twostep',lambda *a:pytest.fail('deadline recovery reran a planner'))
    target=tmp_path/'deadline-row.json'
    request.update(target=str(target),target_sha256=sha256(target))
    return request,row,calls


def test_existing_incumbent_gets_one_fresh_audit_and_all_rechecks_without_new_planning(modules,monkeypatch,tmp_path):
    driver,worker=modules
    request,row,calls=recovery_setup(modules,monkeypatch,tmp_path)
    verified=worker.audit_job(request)
    assert verified['status']=='deadline_exceeded' and verified['passed'] is True
    assert verified['deadline_with_incumbent'] is True and verified['deadline_incumbent_verified'] is True
    assert verified['audit_verified'] and verified['audit_fresh_process']
    assert verified['audit_pid']!=row['planning_pid']
    assert verified['audited_target_sha256']==request['target_sha256']
    assert verified['full_checker_pass'] and verified['level']==0
    assert verified['calibration_rechecks']['max_tiers']['counterfactual_instruction_recheck'] is False
    assert calls==[('load','audit'),('native_audit',row['plan'])]


def test_no_incumbent_audits_accounting_without_fabricating_checker_pass(modules,monkeypatch,tmp_path):
    driver,worker=modules
    request,row,calls=recovery_setup(modules,monkeypatch,tmp_path,with_plan=False)
    verified=worker.audit_job(request)
    assert verified['status']=='deadline_exceeded' and verified['passed'] is False
    assert verified['full_checker_pass'] is False and verified['deadline_with_incumbent'] is False
    assert verified['audit_scope']=='deadline_no_plan'
    assert verified['audit_verified'] and calls==[('load','audit')]


def test_recovery_rejects_failed_fresh_audit_and_changed_target(modules,monkeypatch,tmp_path):
    driver,worker=modules
    request,row,calls=recovery_setup(modules,monkeypatch,tmp_path)
    monkeypatch.setattr(worker,'_audit',lambda *a:dict(audit(passed=False),level=None))
    with pytest.raises(ValueError,match='fresh L0 audit'):
        worker.audit_job(request)
    atomic_json(tmp_path/'deadline-row.json',dict(row,plan={'id':'changed'}))
    with pytest.raises(ValueError,match='target bytes changed'):
        worker.audit_job(request)


def test_driver_hashes_exact_deadline_target_and_checks_independent_audit_pid(modules,monkeypatch,tmp_path):
    driver,worker=modules
    request,row,calls=recovery_setup(modules,monkeypatch,tmp_path)
    def audit_process(command,directory,*,timeout_s,audit=False):
        assert audit is True
        dispatched=read_json(directory/'audit-job.json')
        assert dispatched['target_sha256']==sha256(tmp_path/'deadline-row.json')
        verified=worker.audit_job(dispatched)
        atomic_json(directory/'audit-row.json',verified)
        return dict(audit_pid=verified['audit_pid'],audit_elapsed_s=.01,audit_timeout_s=timeout_s)
    monkeypatch.setattr(driver,'_owned_process',audit_process)
    verified=driver.audit_child(request,tmp_path)
    assert verified['status']=='deadline_exceeded' and verified['passed'] is True
    assert verified['audit_pid']!=row['planning_pid']
    assert calls.count(('load','audit'))==1


def test_audit_timeout_stays_error(modules,monkeypatch,tmp_path):
    driver,worker=modules
    request,row,calls=recovery_setup(modules,monkeypatch,tmp_path)
    def expired(*args,**kwargs):
        raise driver.ChildProcessFailure('audit wall elapsed',dict(audit_timed_out=True,audit_elapsed_s=600.))
    monkeypatch.setattr(driver,'_owned_process',expired)
    result=driver.audit_child(request,tmp_path)
    assert result['status']=='error' and result['passed'] is False
    assert result['audit_timed_out'] is True and result['audit_verified'] is False


def test_audit_rejects_changed_publication_even_when_recovery_target_is_unchanged(modules,monkeypatch,tmp_path):
    driver,worker=modules
    request,row,calls=recovery_setup(modules,monkeypatch,tmp_path)
    changed=checked_event(checked=False)
    (tmp_path/'pipeline/events.jsonl').write_text(json.dumps(changed)+'\n')
    with pytest.raises(ValueError,match='publication hash or plan'):
        worker.audit_job(request)
    assert calls==[('load','audit')]


def test_deadline_audit_keeps_a_rejected_map_in_its_exclusion_column(modules,monkeypatch,tmp_path):
    driver,worker=modules
    request,row,calls=recovery_setup(modules,monkeypatch,tmp_path,with_plan=False)
    monkeypatch.setattr(worker,'_gate',lambda *a:dict(in_envelope=False))
    result=worker.audit_job(request)
    assert result['status']=='out_of_envelope' and result['passed'] is False
    assert result['deadline_exceeded'] is True and result['audit_verified'] is True


def test_owned_process_polls_publications_before_its_hard_wall(modules,monkeypatch,tmp_path):
    import subprocess
    driver,worker=modules
    (tmp_path/'pipeline').mkdir()
    (tmp_path/'pipeline/events.jsonl').write_text(json.dumps(checked_event())+'\n')
    clock=[0.]
    def monotonic():
        clock[0]+=.01
        return clock[0]
    class Child:
        pid=os.getpid()+10000
        returncode=None
        def poll(self):
            return self.returncode
        def wait(self,timeout):
            assert timeout <= .05
            raise subprocess.TimeoutExpired(['owned synthetic'],timeout)
    child=Child()
    monkeypatch.setattr(driver.time,'monotonic',monotonic)
    monkeypatch.setattr(driver.subprocess,'Popen',lambda *a,**kw:child)
    monkeypatch.setattr(driver,'_stop_owned_group',lambda *a:dict(cleanup_failed=False,cleanup_errors=[]))
    row=driver.run_child(job(),tmp_path,timeout_s=.2)
    assert row['status']=='deadline_exceeded' and row['plan']==checked_event()['data']['plan']
    assert 0 <= row['deadline_publication']['published_wall_s'] <= .2
    assert row['child_timed_out'] is True


def test_clean_deadline_continues_and_gets_ordinary_audit_without_planner_retry(modules,monkeypatch,tmp_path):
    driver,worker=modules
    run=tmp_path/'run'
    run.mkdir()
    request=job()
    config=dict(mode='dry-run',workers=1,maps=[{k:v for k,v in request.items() if k!='arm'}],
                twostep_budget_s=30,twostep_s=15,twostep_finalize_s=15)
    atomic_json(run/'config.json',config)
    monkeypatch.setattr(driver,'verify_freeze',lambda *a:dict(run_id='fixed',mode='dry-run'))
    calls=[]
    def child(job,directory,timeout_s):
        calls.append(('plan',job['arm']))
        if job['arm']=='pipeline':
            return driver._deadline_result(job,directory,metrics(),{})
        row=dict(map_id=job['map_id'],cohort=job['cohort'],arm=job['arm'],map_sha256=job['sha256'],
                 status='no_checked_plan',passed=False,full_checker_pass=False,plan=None,wall_s=.01)
        atomic_json(directory/'row.json',row)
        return row
    def independent(job,directory):
        calls.append(('audit',job['arm']))
        if job['arm']=='pipeline':
            assert job['deadline_recovery'] is True
            target=directory/'deadline-row.json'
            return worker.audit_job(dict(job,target=str(target),target_sha256=sha256(target)))
        return dict(read_json(directory/'row.json'),audit_verified=True,audit_fresh_process=True)
    monkeypatch.setattr(driver,'run_child',child)
    monkeypatch.setattr(driver,'audit_child',independent)
    monkeypatch.setattr(worker,'_load',lambda *a,**kw:SimpleNamespace(id='synthetic'))
    monkeypatch.setattr(worker,'_gate',lambda *a:dict(in_envelope=True))
    result=driver.run(run,admission_wait=lambda *a:dict(waited_s=0.))
    assert calls==[('plan','pipeline'),('audit','pipeline'),('plan','twostep'),('audit','twostep')]
    rows=read_json(run/'rows.json')
    assert rows[0]['status']=='deadline_exceeded' and rows[0]['audit_verified'] is True
    assert result['complete'] is True and not (run/'FAILED.json').exists()


def test_tagged_raw_row_uses_exact_preserved_pre_tag_publication_for_fresh_recovery(modules,monkeypatch,tmp_path):
    driver,worker=modules
    request,row,calls=recovery_setup(modules,monkeypatch,tmp_path)
    original=dict(audit(),plan=row['plan'],planning_pid=row['planning_pid'],status='checked_plan')
    original_bytes=(json.dumps(original,indent=4)+'\n').encode()
    (tmp_path/'worker-row.original.json').write_bytes(original_bytes)
    atomic_json(tmp_path/'row.json',dict(original,manifest_sha256='d'*64,tree_digest='e'*64))
    row['deadline_publication'].update(validation_source='worker_original_instance_full_audit',
        publication_sha256=object_hash(original))
    atomic_json(tmp_path/'deadline-row.json',row)
    request['target_sha256']=sha256(tmp_path/'deadline-row.json')
    verified=worker.audit_job(request)
    assert verified['status']=='deadline_exceeded' and verified['passed'] is True
    assert (tmp_path/'worker-row.original.json').read_bytes()==original_bytes
    assert calls==[('load','audit'),('native_audit',row['plan'])]
    atomic_json(tmp_path/'worker-row.original.json',dict(original,plan={'id':'changed'}))
    with pytest.raises(ValueError,match='original worker publication differs'):
        worker.audit_job(request)


def test_independent_watchdog_stops_owned_child_while_observer_is_blocked_even_if_exit_is_zero(modules,monkeypatch,tmp_path):
    import threading
    import time
    driver,worker=modules
    released=threading.Event()
    signals=[]
    class Child:
        pid=os.getpid()+10000
        returncode=None
        def poll(self):
            return self.returncode
        def wait(self,timeout):
            assert released.wait(timeout)
            return self.returncode
    child=Child()
    started=time.monotonic()
    def signal_owned(pgid,signum):
        assert pgid==child.pid
        signals.append((pgid,signum,time.monotonic()-started))
        child.returncode=0  # A handler's clean exit must not erase the deadline.
        released.set()
    def blocked(*args):
        assert released.wait(1.),'Independent watchdog did not run during publication parsing'
    monkeypatch.setattr(driver.subprocess,'Popen',lambda *a,**kw:child)
    monkeypatch.setattr(driver.os,'killpg',signal_owned)
    monkeypatch.setattr(driver,'_stop_owned_group',lambda *a:dict(cleanup_failed=False,cleanup_errors=[]))
    with pytest.raises(driver.ChildProcessFailure) as caught:
        driver._owned_process(['synthetic-owned'],tmp_path,timeout_s=.05,observe=blocked)
    assert signals and signals[0][1]==driver.signal.SIGKILL
    assert signals[0][2]<.5
    assert caught.value.metrics['child_watchdog_fired'] is True
    assert caught.value.metrics['child_timed_out'] is True
    assert caught.value.metrics['child_returncode']==0


@pytest.mark.parametrize('fail',[False,True])
def test_watchdog_cancellation_prevents_late_or_reused_pid_signal_on_all_paths(modules,monkeypatch,tmp_path,fail):
    import time
    driver,worker=modules
    class Child:
        pid=os.getpid()+10000
        returncode=0
        def poll(self):
            return self.returncode
        def wait(self,timeout):
            return 0
    signals=[]
    monkeypatch.setattr(driver.subprocess,'Popen',lambda *a,**kw:Child())
    monkeypatch.setattr(driver.os,'killpg',lambda *args:signals.append(args))
    monkeypatch.setattr(driver,'_stop_owned_group',lambda *a:dict(cleanup_failed=False,cleanup_errors=[]))
    def broken(*args):
        raise ValueError('synthetic observer failure')
    if fail:
        with pytest.raises(driver.ChildProcessFailure):
            driver._owned_process(['owned'],tmp_path,timeout_s=.03,observe=broken)
    else:
        result=driver._owned_process(['owned'],tmp_path,timeout_s=.03)
        assert result['child_watchdog_fired'] is False
    time.sleep(.06)
    assert signals==[]


def test_watchdog_start_failure_still_cleans_the_exact_owned_child(modules,monkeypatch,tmp_path):
    driver,worker=modules
    child=SimpleNamespace(pid=os.getpid()+10000,returncode=None)
    cleaned=[]
    monkeypatch.setattr(driver.subprocess,'Popen',lambda *a,**kw:child)
    def failed_start(*args):
        raise RuntimeError('synthetic thread capacity')
    monkeypatch.setattr(driver,'_start_watchdog',failed_start)
    def cleanup(owned):
        assert owned is child and owned._sealed_owns_session is True
        owned.returncode=-9
        cleaned.append(owned.pid)
        return dict(cleanup_failed=False,cleanup_errors=[])
    monkeypatch.setattr(driver,'_stop_owned_group',cleanup)
    with pytest.raises(driver.ChildProcessFailure,match='thread capacity'):
        driver._owned_process(['owned'],tmp_path,timeout_s=.1)
    assert cleaned==[child.pid]


def test_incremental_tail_keeps_partial_line_and_does_not_reparse_old_events(modules,monkeypatch,tmp_path):
    driver,worker=modules
    directory=tmp_path/'pipeline'
    directory.mkdir()
    first=checked_event(lifts=9.)
    second=checked_event({'id':'better'},lifts=7.)
    encoded=json.dumps(second)
    events=directory/'events.jsonl'
    events.write_text(json.dumps(first)+'\n'+encoded[:len(encoded)//2])
    original_loads=json.loads
    parsed=[]
    def load_once(value,*args,**kwargs):
        parsed.append(value)
        return original_loads(value,*args,**kwargs)
    monkeypatch.setattr(driver.json,'loads',load_once)
    observed,cursor={},{}
    driver._observe_incumbents(job(),tmp_path,1.,10.,observed,cursor=cursor)
    assert len(observed)==1 and cursor['pending']
    with events.open('a') as stream:
        stream.write(encoded[len(encoded)//2:]+'\n')
    driver._observe_incumbents(job(),tmp_path,2.,10.,observed,cursor=cursor)
    driver._observe_incumbents(job(),tmp_path,3.,10.,observed,cursor=cursor)
    assert len(observed)==2 and len(parsed)==2 and cursor['pending']==b''
    assert all('plan' in item for item in observed.values())


@pytest.mark.parametrize('failure',[PermissionError,ProcessLookupError])
def test_watchdog_records_signal_denial_and_handles_disappeared_group(modules,monkeypatch,failure):
    import threading
    driver,worker=modules
    attempted=threading.Event()
    child=SimpleNamespace(pid=os.getpid()+10000,_sealed_owns_session=True,poll=lambda:None)
    def cannot_signal(pgid,signum):
        assert pgid==child.pid and signum==driver.signal.SIGKILL
        attempted.set()
        raise failure('synthetic group state')
    monkeypatch.setattr(driver.os,'killpg',cannot_signal)
    timer,state,lock=driver._start_watchdog(child,.005)
    assert attempted.wait(1.)
    with lock:
        state['finished']=True
        timer.cancel()
    timer.join(timeout=.1)
    assert state['fired'] is True
    assert bool(state['errors']) is (failure is PermissionError)


def test_interrupted_watchdog_start_disarms_its_callback_before_owned_cleanup(modules,monkeypatch):
    driver,worker=modules
    timers=[]
    class InterruptedTimer:
        ident=None
        def __init__(self,seconds,function):
            self.function=function
            self.cancelled=False
            timers.append(self)
        def start(self):
            raise RuntimeError('synthetic start interruption')
        def cancel(self):
            self.cancelled=True
    monkeypatch.setattr(driver.threading,'Timer',InterruptedTimer)
    monkeypatch.setattr(driver.os,'killpg',lambda *args:pytest.fail('late callback signalled a reused PID'))
    child=SimpleNamespace(pid=os.getpid()+10000,_sealed_owns_session=True,poll=lambda:None)
    with pytest.raises(RuntimeError,match='start interruption'):
        driver._start_watchdog(child,.01)
    assert timers[0].cancelled is True
    timers[0].function()
