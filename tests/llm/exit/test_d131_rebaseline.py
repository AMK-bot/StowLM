"""Mock-only standalone current-epoch audit publication; never use Metal here."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT=Path(__file__).resolve().parents[3]


@pytest.fixture
def subject(tmp_path,monkeypatch):
    source=ROOT/'experiments/mvp/exit/rebaseline_on_host.py'
    spec=importlib.util.spec_from_file_location('_d131_rebaseline_test',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    out=tmp_path/'new';parent=tmp_path/'8.1/adapter';paths=[]
    for i in range(30):
        path=tmp_path/f'corpus/val/small/{i}.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'id':str(i)}));paths.append(path)
    listing=tmp_path/'small30.txt';listing.write_text(''.join(str(p)+'\n' for p in paths))
    args=SimpleNamespace(out=out,parent=parent,corpus=tmp_path/'corpus',audit_list=listing,workers=1)
    state=dict(epoch={'current.py':'hash'},identity={'weights':'8.1'},completion=True,
               securing=True,evaluated=0,released=0,reservations=[],drift=False)
    def write(path,row):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_name(path.name+'.tmp');temporary.write_text(json.dumps(row));temporary.replace(path)
    @contextmanager
    def reservation(path,**kw):
        state['reservations'].append(kw)
        yield
    resources=SimpleNamespace(OWNER='mvp-exit/local',reservation=reservation,require_current_parent=lambda p:None)
    def evaluate(parent,given,work,options):
        state['evaluated']+=1
        assert given==paths
        assert options.audit_batch_size==4 and options.ballast_s==90 and options.max_tokens==20000
        assert options.workers==1
        if state['drift']:state['epoch']['current.py']='changed'
        return [dict(instance_id=str(i),instance_path=str(path),passed=i<20,
            weighted_moves=34.05 if i<20 else None,
            generation_timings={'securing':dict(bounds_available=state['securing'],
                evaluations=2,unavailable=0,fail_open=0)},
            verification={'report':{'passed':i<20},'mask':{'passed':i<20}})
            for i,path in enumerate(paths)]
    def release():state['released']+=1
    services=dict(resources=resources,completion_ready=lambda *a:state['completion'],
        preflight_mlx_device=lambda:dict(metal_available=True),frozen_audit_paths=lambda *a:paths,
        source_pins=lambda:dict(state['epoch']),adapter_identity=lambda p:dict(state['identity']),
        checker_epoch=lambda:'current-checker',write_json=write,evaluate=evaluate,release_model=release,
        checker_unavailable=lambda row:bool(row.get('unavailable')),
        outcome_counts=lambda rows:dict(total=len(rows),passed=sum(r['passed'] for r in rows),
            unavailable=0,physics_failed=0,other_not_passed=sum(not r['passed'] for r in rows)))
    monkeypatch.setattr(module,'_services',lambda:services)
    return SimpleNamespace(module=module,args=args,state=state,services=services,paths=paths)


def test_fresh_baseline_publishes_bound_rows_only_after_current_decode(subject):
    ready=subject.module.run(subject.args)
    assert subject.state['evaluated']==1 and subject.state['released']==1
    assert ready['status']=='ready' and ready['summary']['passed']==20
    assert ready['summary']['mean_weighted_moves']==pytest.approx(34.05)
    assert ready['specification']['recipe']['backtracking'] is False
    assert ready['specification']['recipe']['layers']==['production','lookahead','securing']
    assert ready['summary']['securing']['evaluations']==60
    assert not ready['generation_smoke_pass_claimed'] and not ready['training_performed']
    assert subject.state['reservations']==[dict(cores=2,ram_gb=24,gpu=True,estimate_s=1500,load_policy='gpu_generation')]
    binding=json.loads((subject.args.out/'audit-baseline/baseline-binding.json').read_text())
    assert binding['checker_epoch']=='current-checker'
    assert binding['adapter']==subject.state['identity']
    assert len(binding['maps'])==30
    for name,digest in ready['outputs'].items():assert subject.module._sha(name)==digest
    assert not (subject.args.out/'STARTED').exists()


def test_ready_idempotently_reuses_only_exact_bound_measurement(subject):
    ready=subject.module.run(subject.args)
    assert subject.module.run(subject.args)==ready
    assert subject.state['evaluated']==1
    (subject.args.out/'audit-baseline/summary.json').write_text('[]')
    with pytest.raises(RuntimeError,match='published baseline changed'):
        subject.module.run(subject.args)


def test_parent_completion_required_before_gpu_or_publication(subject):
    subject.state['completion']=False
    with pytest.raises(RuntimeError,match='completed original 8.1'):
        subject.module.run(subject.args)
    assert not subject.state['reservations'] and not subject.args.out.exists()


def test_metal_unavailable_never_reserves_or_publishes(subject):
    def unavailable():raise RuntimeError('Metal unavailable')
    subject.services['preflight_mlx_device']=unavailable
    with pytest.raises(RuntimeError,match='Metal unavailable'):subject.module.run(subject.args)
    assert not subject.state['reservations'] and not subject.args.out.exists()


@pytest.mark.parametrize('failure',['source_drift','securing_inactive'])
def test_untrusted_epoch_or_inactive_securing_never_publishes_ready(subject,failure):
    subject.state['drift']=failure=='source_drift'
    subject.state['securing']=failure!='securing_inactive'
    with pytest.raises(RuntimeError,match='changed|securing'):
        subject.module.run(subject.args)
    assert not (subject.args.out/'rebaseline-ready.json').exists()
    assert not (subject.args.out/'audit-baseline/summary.json').exists()
    assert subject.state['released']==1


def test_changed_parent_source_after_readiness_is_not_relabeled(subject):
    subject.module.run(subject.args)
    subject.state['identity']['weights']='different-parent'
    with pytest.raises(RuntimeError,match='changed parent/source/maps/recipe'):
        subject.module.run(subject.args)
    assert subject.state['evaluated']==1


def test_frozen_audit_coverage_is_mandatory(subject):
    original=subject.services['evaluate']
    subject.services['evaluate']=lambda *a:original(*a)[:-1]
    with pytest.raises(RuntimeError,match='frozen 30'):
        subject.module.run(subject.args)
    assert not (subject.args.out/'rebaseline-ready.json').exists()


def test_code_freeze_marker_must_match_current_source_before_gpu_reservation(subject):
    marker=subject.args.out.parent/'D131-CODE.READY'
    marker.write_text(json.dumps({'source_pins':{'wrong.py':'wrong'}}))
    subject.args.code_ready=marker
    with pytest.raises(RuntimeError,match='source-freeze marker'):
        subject.module.run(subject.args)
    assert subject.state['reservations']==[] and subject.state['evaluated']==0
    marker.write_text(json.dumps({'source_pins':subject.state['epoch']}))
    assert subject.module.run(subject.args)['status']=='ready'


@pytest.mark.parametrize('fault', ['zero_evaluations','unavailable','fail_open'])
def test_securing_faults_never_publish_baseline(subject,fault):
    original=subject.services['evaluate']
    def evaluate(*a):
        rows=original(*a)
        for row in rows:
            metrics=row['generation_timings']['securing']
            if fault=='zero_evaluations':metrics['evaluations']=0
            else:metrics[fault]=1
        return rows
    subject.services['evaluate']=evaluate
    with pytest.raises(RuntimeError,match='securing'):
        subject.module.run(subject.args)
    assert not (subject.args.out/'rebaseline-ready.json').exists()
