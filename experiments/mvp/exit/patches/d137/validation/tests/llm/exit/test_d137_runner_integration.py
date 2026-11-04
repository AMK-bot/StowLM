"""D137 staged runner behavior, CPU fakes only; no source edits or GPU imports."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').is_file())
from llm.exit import runner as R, selection as S

@pytest.fixture
def case(tmp_path,monkeypatch):
    from stowage.model.fixtures import build_instance
    from stowage.model import MovePlan
    from llm.exit import identity
    instance=build_instance();path=tmp_path/'map.json';path.write_text(instance.model_dump_json())
    class Guard:
        def require_allowed(self,*args,**kwargs):pass
    monkeypatch.setattr(identity,'identity_guard',lambda corpus:Guard())
    epoch={'physics':'v1'}
    monkeypatch.setattr(R,'source_pins',lambda:dict(epoch))
    def candidate(source,cost):
        plan=MovePlan(id=f'{source}-{cost}',instance_id=instance.id,call_index=instance.call_index)
        return R.Candidate(plan,source,source,{'weighted_moves':cost})
    return SimpleNamespace(path=path,instance=instance,epoch=epoch,candidate=candidate,tmp=tmp_path)


@pytest.mark.parametrize('drift',['epoch','input','budget'])
def test_teacher_job_publishes_current_bound_proof_and_ack_then_reuses_cache(case,monkeypatch,drift):
    from llm.exit import selection
    gold=case.candidate('gold',3);calls=[]
    monkeypatch.setattr(R,'load_gold_candidate',lambda *args:gold)
    def verify(instance,candidate,**kw):
        calls.append(kw)
        return S.Verification(True,candidate.plan,3,{'report':{'passed':True},'mask':{'passed':True}})
    monkeypatch.setattr(selection,'verify_candidate',verify)
    def prepare(instance,candidate,**kw):
        assert kw['planning_s']==kw['ballast_s']==120
        checked=kw['verifier'](instance,candidate)
        return [candidate],{candidate.candidate_id:checked},[{'source':'gold','passed':True}]
    monkeypatch.setattr(selection,'prepare_teacher_candidates',prepare,raising=False)
    target=R.teacher_job(case.path,case.tmp,case.tmp/'gold',case.tmp/'teacher')
    row=R.read_json(target)
    assert row['binding']['sources']==case.epoch
    assert calls[0]['expected_outcome_epoch']==R._LOADED_AUDIT_EPOCH
    assert calls[0]['ballast_s']==120
    assert row['candidates'][0]['verification']['passed'] is True
    assert R.teacher_job(case.path,case.tmp,case.tmp/'gold',case.tmp/'teacher')==target
    assert len(calls)==1
    extra={}
    if drift=='epoch':case.epoch['physics']='v2'
    elif drift=='input':case.path.write_text(case.path.read_text()+'\n')
    else:extra['planning_s']=121.
    with pytest.raises(RuntimeError,match='teacher cache epoch'):
        R.teacher_job(case.path,case.tmp,case.tmp/'gold',case.tmp/'teacher',**extra)


@pytest.mark.parametrize('teacher_source',['gold','baseline_twostep'])
def test_selection_merges_teacher_and_checks_costlier_own_models(case,monkeypatch,teacher_source):
    from llm.exit import selection,preferences
    teacher=case.candidate(teacher_source,3)
    teacher_proof=S.Verification(True,teacher.plan,3,
        {'report':{'passed':True},'mask':{'passed':True}})
    monkeypatch.setattr(R,'read_teacher_manifest',lambda *args:
        ([teacher],{teacher.candidate_id:teacher_proof},
         {'teachers':[{'source':teacher_source,'passed':True}],'wall_s':2.}))
    calls=[]
    def verifier(instance,candidate,**kw):
        calls.append(candidate.candidate_id)
        return S.Verification(True,candidate.plan,candidate.metadata['weighted_moves'],
            {'report':{'passed':True},'mask':{'passed':True}})
    monkeypatch.setattr(selection,'verify_candidate',verifier)
    def select(instance,candidates,**kw):
        kw['scorer']=lambda i,c:c.metadata['weighted_moves']
        return S.select_winner(instance,candidates,**kw)
    monkeypatch.setattr(R,'select_winner',select)
    seen=[]
    monkeypatch.setattr(preferences,'prepare_training_pairs',lambda *a,**kw:
        (seen.append(kw) or {'pairs':[],'refusals':[]}))
    model=case.candidate('model',8)
    raw=[dict(plan=model.plan,source=model.source,candidate_id='own-model',metadata=model.metadata)]
    result=R.select_job(case.path,raw,case.tmp,case.tmp/'gold',case.tmp/'selection',
        {'verify_all':True,'polish_top':0},preference_max_pairs=2,teacher_manifest='proof')
    assert result['source']==teacher_source and result['weighted_moves']==3
    assert calls==['own-model']
    assert seen[0]['prefer_model_costlier_only'] is True
    assert result['teachers'][0]['passed'] is True
    assert result['candidates'][1]['metadata']['exit_verified'] is True
    record=R.read_json(result['winner_record'])
    assert record['report']['passed'] is True


@pytest.mark.parametrize('teacher_passes',[False,True])
def test_teacher_jobs_queued_before_decode_and_coverage_gate(case,monkeypatch,teacher_passes):
    from llm.exit import audit,progress,capacity
    args=R.parser().parse_args(['--parent','parent','--out',str(case.tmp/'run'),
        '--tiny','1','--small','1','--workers','2'])
    args.audit_clock=None;args.early_pruning=False;args.audit_every=2
    args._loaded_source_pins=case.epoch
    path2=case.tmp/'map2.json';path2.write_bytes(case.path.read_bytes())
    paths=[case.path,path2];events=[]
    class Tracker:
        @contextmanager
        def phase(self,*args,**kwargs):yield
        def update(self,**kwargs):pass
    monkeypatch.setattr(progress,'start_generation',lambda *a,**kw:Tracker())
    monkeypatch.setattr(audit,'frozen_audit_paths',lambda *a,**kw:[])
    monkeypatch.setattr(audit,'check_clock',lambda args:None)
    monkeypatch.setattr(R,'adapter_identity',lambda adapter:{'weights':'same'})
    class Schedule:
        root=case.tmp
        state={'generations':{}}
        def next_train(self,*args,**kwargs):return paths
    def teacher_job(*args):return 'teacher-proof'
    def select_job(*args):
        assert args[5]['verify_all'] is True
        assert args[9]=='teacher-proof'
        source='gold' if teacher_passes else 'model'
        return dict(instance_id=str(args[0]),source=source,passed=True,weighted_moves=3,
            teachers=[dict(source='gold',passed=teacher_passes,attempted=True)],
            timings={'verify_s':0.,'greedy_verify_s':0.,'polish_s':0.,'teacher_worker_s':1.})
    monkeypatch.setattr(R,'teacher_job',teacher_job)
    monkeypatch.setattr(R,'select_job',select_job)
    class Future:
        def __init__(self,value):self.value=value
        def result(self):return self.value
        def cancel(self):pass
    class Pool:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def submit(self,function,*args):
            events.append(('submit',function.__name__))
            return Future(function(*args))
    monkeypatch.setattr(R,'ProcessPoolExecutor',Pool)
    def decode(*args,**kwargs):
        events.append(('decode',str(args[0])))
        return {'timings':{'wall_s':.01},'securing':{'evaluations':1},'candidates':[],'samples':[]}
    monkeypatch.setattr(R,'_raw_generation',decode)
    class CoveragePassed(Exception):pass
    def stop_after_coverage(*args,**kwargs):raise CoveragePassed()
    monkeypatch.setattr(capacity,'CapacitySizer',stop_after_coverage)
    population=[dict(adapter='parent',learning_rate=1e-5,temperature=.8,replay_ratio=.67)]
    if teacher_passes:
        with pytest.raises(CoveragePassed):R.run_generation(0,population,args,Schedule())
    else:
        with pytest.raises(RuntimeError,match='teacher coverage gate failed'):
            R.run_generation(0,population,args,Schedule())
    assert events[:2]==[('submit','teacher_job'),('submit','teacher_job')]
    assert events[2][0]=='decode'
    report=R.read_json(args.out/'gen-000/teacher-coverage.json')
    assert report['gold_passes']==(2 if teacher_passes else 0)
