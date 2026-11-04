"""CPU-only D129 prepared receipt/child-update integrity regressions."""
from contextlib import nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT=Path(__file__).resolve().parents[3]
STAGE=ROOT/'experiments/mvp/exit/patches/d129'


def load(name):
    directory=ROOT/'src/llm/exit' if (ROOT/'src/llm/exit/salvage.py').exists() else STAGE
    if not (directory/f'{name}.py').exists():directory=STAGE.parent/'d124'
    spec=importlib.util.spec_from_file_location(f'_d129_integrity_{name}',directory/f'{name}.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path,row):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(row))


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    salvage=load('salvage');reverify=load('reverify')
    out,parent,old=tmp_path/'new',tmp_path/'parent',tmp_path/'old'
    identity={'weights':'original'};epoch={'checker.py':'current'}
    manifest=out/'gen-000/instances.json'
    write(manifest,dict(population=[dict(adapter=str(parent))],instances=['map.json']))
    baseline=out/'audit-baseline/summary.json';write(baseline,[{'passed':True}])
    schedule=out/'schedule.json';write(schedule,{'generations':{'0':[]}})
    instance=old/'instance.json';write(instance,{'id':'map'})
    selection=old/'selection.json';write(selection,dict(instance_path=str(instance),passed=False))
    result=out/'gen-000/reverification/result.json';write(result,dict(selections=[],wall_s=1))
    receipt=dict(schema=reverify.SCHEMA,status='ready',training_epoch=epoch,
        model_identity={'adapter':identity}, inputs=reverify._inputs([selection])[1],
        outputs={str(result):sha(result)})
    receipt['receipt_sha256']=reverify._digest(receipt)
    receipt_path=result.with_name('receipt.json');write(receipt_path,receipt)
    marker=dict(schema='exit-salvage-prepared-v1',status='ready',new_out=str(out),parent=str(parent),
        source_out=str(old),parent_identity=identity,current_sources=epoch,
        receipt_path=str(receipt_path),original_selection_paths=[str(selection)],selections=[],
        output_sha256={str(p):sha(p) for p in (manifest,baseline,schedule)})
    marker['prepared_sha256']=salvage._digest(marker)
    marker_path=out/'salvage-prepared.json';write(marker_path,marker)
    args=SimpleNamespace(out=out,parent=parent,recover_prepared=marker_path)
    stub=SimpleNamespace(read_json=lambda p:json.loads(Path(p).read_text()),
        adapter_identity=lambda p:dict(identity),source_pins=lambda:dict(epoch),
        write_json=write,train_selected=lambda *a,**kw:pytest.fail('committed salvage retrained'),
        resize_after_pilot=lambda *a:None)
    monkeypatch.setitem(sys.modules,'llm.exit.runner',stub)
    monkeypatch.setitem(sys.modules,'llm.exit.reverify',reverify)
    monkeypatch.setattr(reverify,'_epoch',lambda:dict(epoch))
    return SimpleNamespace(salvage=salvage,args=args,marker=marker,marker_path=marker_path,
        manifest=manifest,baseline=baseline,schedule=schedule,selection=selection,result=result,
        runner=stub,identity=identity,epoch=epoch)


@pytest.mark.parametrize('target', ['baseline','manifest'])
def test_prepared_baseline_and_manifest_mutation_refused(prepared,target):
    subject=prepared
    subject.salvage.validate_prepared(subject.marker_path,subject.args)
    write(getattr(subject,target),{'changed':True})
    with pytest.raises(ValueError,match='prepared critical output changed'):
        subject.salvage.validate_prepared(subject.marker_path,subject.args)


def test_original_selection_mutation_refused(prepared):
    write(prepared.selection,{'instance_path':'swapped.json'})
    with pytest.raises((ValueError,FileNotFoundError)):
        prepared.salvage.validate_prepared(prepared.marker_path,prepared.args)


def test_parent_hash_and_training_source_changes_refused(prepared):
    prepared.identity['weights']='different'
    with pytest.raises(ValueError,match='parent or source epoch changed'):
        prepared.salvage.validate_prepared(prepared.marker_path,prepared.args)


def test_bound_result_mutation_refused(prepared):
    write(prepared.result,{'selections':[{'unchecked':True}]})
    with pytest.raises(ValueError,match='training receipt output identity mismatch'):
        prepared.salvage.validate_prepared(prepared.marker_path,prepared.args)


def test_schedule_only_may_grow_after_inheritance_commits(prepared):
    write(prepared.schedule,{'generations':{'0':[],'1':['new-map']}})
    with pytest.raises(ValueError,match='prepared critical output changed'):
        prepared.salvage.validate_prepared(prepared.marker_path,prepared.args)
    write(prepared.args.out/'gen-000/summary.json',{'salvage_prepared_sha256':prepared.marker['prepared_sha256']})
    prepared.salvage.validate_prepared(prepared.marker_path,prepared.args)
    write(prepared.baseline,[{'passed':False}])
    with pytest.raises(ValueError,match='prepared critical output changed'):
        prepared.salvage.validate_prepared(prepared.marker_path,prepared.args)


def test_consumed_inheritance_checkpoint_never_retrains(prepared):
    population=[{'adapter':'parent','elite':True},{'adapter':'checked-child'}]
    write(prepared.args.out/'gen-000/summary.json',dict(
        salvage_prepared_sha256=prepared.marker['prepared_sha256'],next_population=population))
    prepared.salvage.inherit_prepared(prepared.args)
    assert prepared.args._start_generation==1
    assert prepared.args._resume_population==population


@pytest.mark.parametrize('mutation_at', ['dataset','before_dpo','before_second_child'])
def test_every_child_update_rechecks_prepared_integrity(tmp_path,monkeypatch,mutation_at):
    monkeypatch.setitem(sys.modules,'llm.exit.outcomes',load('outcomes'))
    runner=load('runner')
    import llm.exit.runtime
    import llm.exit.replay
    import llm.exit.preferences
    parent=str(tmp_path/'parent');out=tmp_path/'new';out.mkdir()
    dataset=out/'gen-000';dataset.mkdir()
    integrity={'valid':True,'calls':0};updates=[]
    def assert_extra():
        integrity['calls']+=1
        if not integrity['valid']:raise ValueError('prepared critical output changed: baseline')
    args=SimpleNamespace(out=out,gold=tmp_path/'gold',dagger=False,dpo=mutation_at=='before_dpo',
        dpo_max_pairs=2,dpo_max_length=2048,
        population=1,steps=1,_training_extra_checks=assert_extra)
    monkeypatch.setitem(sys.modules,'llm.exit.reverify',SimpleNamespace(assert_training_receipt=lambda *a,**kw:None))
    monkeypatch.setattr(runner,'frozen_validation',lambda args:[])
    monkeypatch.setattr(runner,'adapter_identity',lambda path:{'weights':'parent'})
    monkeypatch.setattr(runner,'saved_gold_records',lambda *a,**kw:[tmp_path/'gold.json'])
    configs=[dict(replay_ratio=.67,learning_rate=.0001,method='sft_dpo' if args.dpo else 'sft')]
    if mutation_at=='before_second_child':configs*=2
    monkeypatch.setattr(runner,'mutate_children',lambda *a,**kw:configs)
    monkeypatch.setattr(llm.exit.replay,'choose_replay',lambda *a,**kw:[])
    monkeypatch.setattr(llm.exit.preferences,'write_pairs',lambda *a:None)
    def train(*a,**kw):
        updates.append(a)
        if mutation_at!='dataset':integrity['valid']=False
    monkeypatch.setattr(llm.exit.runtime,'train',train)
    def build(*a,**kw):
        if mutation_at=='dataset':integrity['valid']=False
        return {'rows':1}
    monkeypatch.setattr(runner,'build_training_dataset',build)
    tracker=SimpleNamespace(phase=lambda *a,**kw:nullcontext())
    with pytest.raises(ValueError,match='prepared critical output changed'):
        pair=dict(chosen={'prompt':[],'tokens':[1]}, rejected={'prompt':[],'tokens':[2]})
        runner.train_selected(0,dict(adapter=parent),[dict(passed=True,winner_record='checked.json',
            preferences={'pairs':[pair]} if args.dpo else {})],
            [tmp_path/'map.json'],args,dataset,tracker,
            training_receipt={'training_epoch':{'checker.py':'current'}},original_selections=[],
            model_identity={'adapter':{'weights':'parent'}})
    assert integrity['calls']==(2 if mutation_at=='dataset' else 3)
    assert len(updates)==(0 if mutation_at=='dataset' else 1)
