"""CPU-only compatibility aggregation fixtures; no checks, decode, or Metal."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT=Path(__file__).resolve().parents[3]


def load(name):
    path=ROOT/f'experiments/mvp/exit/{name}.py'
    spec=importlib.util.spec_from_file_location('_test_'+name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


@pytest.fixture
def subject(tmp_path,monkeypatch):
    module=load('finalize_rebaseline_d131');shared=load('rebaseline_on_host')
    source=tmp_path/'old';out=tmp_path/'new';work=source/'rebaseline-current'
    parent=tmp_path/'parent';identity={'adapters.safetensors':'8.1weights'}
    old={'src/stowage/checker.py':'same','src/llm/exit/runner.py':'same',
         **{name:'before' for name in module.OPERATIONAL}}
    current={**old,**{name:'after' for name in module.OPERATIONAL}}
    def write(path,row):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(row))
    maps=[];paths=[]
    for i in range(30):
        path=tmp_path/f'corpus/val/small/{i}.json';write(path,dict(id=str(i)))
        paths.append(path);maps.append(dict(path=str(path),sha256=module.sha(path)))
    listing=tmp_path/'audit.txt';listing.write_text(''.join(str(p)+'\n' for p in paths))
    recipe=dict(greedy=True,temperature=0.,samples=1,batched=True,batch_size=4,max_tokens=20000,
        layers=['production','lookahead','securing'],backtracking=False,
        ballast='deterministic scoped V4',ballast_s=90.,checker_watchdog_s=300.,lns=False,solver_cargo_fallback=False)
    write(work/'specification.json',dict(parent=str(parent),parent_identity=identity,current_sources=old,
        maps=maps,audit_list_sha256=module.sha(listing),recipe=recipe))
    epoch=hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest()[:16]
    directory=work/f'epoch-{epoch}'
    write(directory/'identity.json',dict(adapter=identity,sources=old,
        maps=[[r['path'],r['sha256']] for r in maps],max_tokens=20000,batch_size=4))
    for i,path in enumerate(paths):
        write(directory/f'{i:03d}.json',dict(instance_id=str(i),instance_path=str(path),passed=i<20,
            weighted_moves=10 if i<20 else None,
            generation_timings=dict(status='complete',tokens=[1],securing=dict(
                bounds_available=True,evaluations=1,unavailable=0,fail_open=0)),
            verification=dict(report={'passed':i<20},mask={'passed':i<20})))
    write(work/'compute.json',dict(actual_wall_s=700,process_peak_rss_bytes=16000000000,
        status='failed',estimate={'gpu':True}))
    activation=tmp_path/'D131-activation.json'
    write(activation,dict(authorization='D131',targets={name:dict(old_sha256='before',new_sha256='after')
        for name in module.OPERATIONAL}))
    state=dict(held=False,identity=identity,current=current)
    @contextmanager
    def lock():
        if state['held']:raise RuntimeError('held')
        yield
    services=dict(resources=SimpleNamespace(execution_lock=lock),source_pins=lambda:dict(state['current']),
        adapter_identity=lambda p:state['identity'],write_json=write,checker_epoch=lambda:'unchangedchecker',
        outcome_counts=lambda rows:dict(total=30,passed=sum(r['passed'] for r in rows),unavailable=0),
        checker_unavailable=lambda row:False,validate_rows=shared._validate_rows,
        identity_guard=lambda corpus:SimpleNamespace(require_allowed=lambda *a,**kw:None),
        load_instance=lambda path:SimpleNamespace(id=Path(path).stem))
    monkeypatch.setattr(module,'_services',lambda:services)
    # The full report verifier is tested independently below; aggregation never
    # invents a report or evaluates a reconstructed plan in these fixtures.
    monkeypatch.setattr(module,'validate_pass',lambda *a:None)
    args=SimpleNamespace(source_out=source,out=out,parent=parent,activation=activation,
        audit_list=listing,corpus=tmp_path/'corpus')
    return SimpleNamespace(module=module,args=args,state=state,old=old,current=current,
        directory=directory,paths=paths,work=work,services=services,write=write)


def test_exact_operational_change_aggregates_existing_passes_and_preserves_inputs(subject):
    before={str(p):subject.module.sha(p) for p in subject.args.source_out.rglob('*') if p.is_file()}
    ready=subject.module.finalize(subject.args)
    assert ready['summary']['passed']==20 and ready['summary']['mean_weighted_moves']==10
    assert ready['summary']['audit_s']==700
    assert ready['specification']['current_sources']==subject.current
    assert ready['decoded_source_pins']==subject.old
    assert ready['compatibility_receipt']['plans_reconstructed'] is False
    assert ready['compatibility_receipt']['shipping_plans_published'] is False
    assert not ready['training_performed'] and not ready['generation_smoke_pass_claimed']
    assert before=={str(p):subject.module.sha(p) for p in subject.args.source_out.rglob('*') if p.is_file()}
    assert len(ready['outputs'])==4


@pytest.mark.parametrize('fault',['unknown_hash','changed_checker','partial_rows','held_gpu','wrong_adapter','changed_map'])
def test_unsafe_compatibility_or_incomplete_measurement_never_publishes(subject,fault):
    if fault=='unknown_hash':subject.state['current']['src/llm/exit/resources.py']='unknown'
    elif fault=='changed_checker':subject.state['current']['src/stowage/checker.py']='changed'
    elif fault=='partial_rows':(subject.directory/'029.json').unlink()
    elif fault=='held_gpu':subject.state['held']=True
    elif fault=='wrong_adapter':subject.state['identity']={'adapters.safetensors':'other'}
    else:subject.paths[0].write_text('{"id":"changed"}')
    with pytest.raises((RuntimeError,ValueError)):
        subject.module.finalize(subject.args)
    assert not (subject.args.out/'rebaseline-ready.json').exists()


def test_full_pass_requires_real_report_instance_adapter_and_count_binding():
    module=load('finalize_rebaseline_d131')
    instance=SimpleNamespace(model_dump=lambda **kw:{'id':'map'})
    parents=['checker.instance.sha256:'+module.digest({'id':'map'}),
             'checker.plan.sha256:'+'a'*64,'adapter-weights-sha256:8.1']
    report=SimpleNamespace(passed=True,parts={name:SimpleNamespace(ran=True,error=None,violations=[]) for name in
        ('moves','rules','voyage','stability','strength','visibility','lashing','constraints')},
        violations=[],provenance=SimpleNamespace(versions={'checker':'v'},parents=parents),
        count=SimpleNamespace(weighted_nonproductive=10))
    services=dict(CheckReport=SimpleNamespace(model_validate=lambda value:report),
        CHECKER_VERSION='v',require_clean=lambda *a,**kw:None)
    mask=dict(passed=True,status='complete',actions_equal=True,securing=True,lookahead=True,
        actions_completed=1,actions_expected=1,securing_metrics={'bounds_available':True})
    row=dict(passed=True,weighted_moves=10,verification={'report':{},'mask':mask},
             generation_timings={'status':'complete','tokens':[1]})
    module.validate_pass(row,instance,{'adapters.safetensors':'8.1'},services)
    report.count.weighted_nonproductive=11
    with pytest.raises(RuntimeError,match='complete current checker evidence'):
        module.validate_pass(row,instance,{'adapters.safetensors':'8.1'},services)
    report.count.weighted_nonproductive=10
    with pytest.raises(RuntimeError,match='own-adapter binding'):
        module.validate_pass(row,instance,{'adapters.safetensors':'different'},services)
    mask['actions_equal']=False
    with pytest.raises(RuntimeError,match='layered mask/securing evidence'):
        module.validate_pass(row,instance,{'adapters.safetensors':'8.1'},services)
