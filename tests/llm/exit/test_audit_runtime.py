"""CPU-only heterogeneous batching contracts; never import MLX or load weights."""
import ast
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS

import numpy as np
import pytest

from llm.exit import audit_runtime as audit


def response(uid,token,reason=None):
    return NS(uid=uid,token=token,finish_reason=reason)


class Responses:
    def __init__(self,*batches):self.batches=iter(batches)
    def next_generated(self):return next(self.batches,[])


def test_drain_routes_arbitrary_uids_and_interleaved_completion_once():
    decoders={81:NS(status='complete'),7:NS(status='dead_end'),39:NS(status='running')}
    generator=Responses([response(7,4),response(81,3)],
                        [response(39,8),response(81,99,'stop')],
                        [response(7,99,'stop'),response(39,6,'length')])
    seen=[]
    audit.drain_generator(generator,decoders,on_result=lambda *row:seen.append(row))
    assert [(uid,status,tokens) for uid,_,status,tokens in seen]==[
        (81,'complete',[3,99]),(7,'dead_end',[4,99]),(39,'max_tokens',[8,6])]
    assert all(decoder is decoders[uid] for uid,decoder,_,_ in seen)


@pytest.mark.parametrize('batches,decoders',[
    ([[response(9,0)]],{1:NS(status='running')}),
    ([[response(1,99,'stop'),response(1,99,'stop')]],{1:NS(status='complete')}),
    ([],{1:NS(status='running')}),
])
def test_drain_refuses_unknown_duplicate_finished_and_premature_exhaustion(batches,decoders):
    with pytest.raises(RuntimeError):
        audit.drain_generator(Responses(*batches),decoders,on_result=lambda *a:None)


def test_empty_drain_never_queries_generator():
    generator=NS(next_generated=lambda:pytest.fail('empty batch queried'))
    audit.drain_generator(generator,{},on_result=lambda *a:pytest.fail('empty callback'))


def _real_mask_processor():
    """Use the production processor itself with NumPy replacing MLX arrays."""
    path=Path(importlib.util.find_spec('llm.stowlm.mask').origin).with_name('mask_mlx.py')
    tree=ast.parse(path.read_text())
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MaskLogitsProcessor')
    env={'np':np,'mx':NS(array=np.array)}
    future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
    code=ast.fix_missing_locations(ast.Module(body=[future,node],type_ignores=[]))
    exec(compile(code,str(path),'exec'),env)
    return env['MaskLogitsProcessor']


@pytest.fixture
def backend(monkeypatch):
    from llm.exit import runtime,identity
    from llm.exit import securing_guard
    monkeypatch.setattr(securing_guard,'securing_metrics',lambda decoders,**kw:
                        dict(evaluations=len(decoders),fail_open=0,unavailable=0,fixture=True))
    import stowage.io
    import stowage.feasibility
    import stowage.actions
    import llm.stowlm.planner
    import llm.stowlm.format
    import llm.stowlm.format_prompt
    import llm.stowlm.sft_data
    from stowage.model import MovePlan,Provenance
    events=[]
    state=NS(events=events,guard_error=None,insert_uids=None,codecs={},decoders={},generator=None,
             factory_calls=[],constructor=None,dead_end_maps=set())
    maps={name:NS(id=name,seed=i,prompt=[100+i]*(i+2),provenance=Provenance(created_by='fixture'))
          for i,name in enumerate(['map-a','map-b','map-c'])}
    state.maps=maps
    class Guard:
        def require_allowed(self,instance,**kw):
            events.append(('guard',instance.id,kw))
            if state.guard_error:raise ValueError(state.guard_error)
    monkeypatch.setattr(identity,'identity_guard',lambda root:Guard())
    monkeypatch.setattr(stowage.io,'load_instance',lambda path:maps[str(path)])
    class Planner:
        def __init__(self,**kw):
            events.append(('planner',kw));self.provenance=Provenance(created_by='audit.adapter')
    monkeypatch.setattr(llm.stowlm.planner,'StowLMPlanner',Planner)
    monkeypatch.setattr(runtime,'_resident_for',lambda planner:(events.append(('resident',)) or object()))
    monkeypatch.setattr(llm.stowlm.format,'load_tokenizer',lambda:'fixture-tokenizer')
    monkeypatch.setattr(llm.stowlm.format_prompt,'encode_prompt',lambda i,t:list(i.prompt))
    def codec_for(instance,tokenizer):
        assert tokenizer=='fixture-tokenizer'
        return state.codecs.get(instance.id,NS(end_plan_tokens=[99]))
    monkeypatch.setattr(llm.stowlm.sft_data,'codec_for',codec_for)
    def make_engine(instance,space,*,config):
        assert config.extra=={'global_screen':0}
        return NS(instance=instance,to_plan=lambda:MovePlan(id=instance.id+'.plan',instance_id=instance.id,
            call_index=0,provenance=instance.provenance))
    monkeypatch.setattr(stowage.feasibility,'make_feasibility_engine',make_engine)
    monkeypatch.setattr(stowage.actions,'get_action_space',lambda:'boxslot')
    class Decoder:
        def __init__(self,instance,engine):
            self.instance,self.engine=instance,engine
            self.status='running';self.tokens=[]
            self.action_token=11+instance.seed
        def advance(self,token):
            self.tokens.append(token)
            if token==99:self.status='complete'
        def allowed_token_ids(self):
            if self.instance.id in state.dead_end_maps:
                self.status='dead_end'
                return ()
            if self.status!='running':return ()
            return (self.action_token,) if not self.tokens else (99,)
    def factory(instance,engine,codec,config,count):
        assert config is None and count==1
        state.factory_calls.append((instance,engine,codec))
        decoder=Decoder(instance,engine)
        state.decoders[instance.id]=decoder
        return [decoder]
    monkeypatch.setattr(runtime,'decoder_factory',lambda:factory)
    real_processor=_real_mask_processor()
    fake_mask=ModuleType('llm.stowlm.mask_mlx');fake_mask.MaskLogitsProcessor=real_processor
    monkeypatch.setitem(sys.modules,'llm.stowlm.mask_mlx',fake_mask)
    class Generator:
        # Mirror installed BatchGenerator: unspecified sampler is GREEDY;
        # each UID owns its prompt and ordered list of processors. A current
        # generated token reaches processors before its response is returned.
        def __init__(self,model,**kw):
            state.constructor=kw;state.generator=self;self.max_tokens=kw['max_tokens']
            self.closed=False;self.active={}
            assert 'sampler' not in kw or kw['sampler'] is None
        def insert(self,prompts,*,logits_processors):
            state.prompts=prompts;state.processors=logits_processors
            uids=state.insert_uids if state.insert_uids is not None else [81,7,39][:len(prompts)]
            for uid,prompt,processors in zip(uids,prompts,logits_processors):
                assert len(processors)==1
                scores=processors[0](np.array(prompt),np.zeros((1,256)))
                self.active[uid]=NS(context=list(prompt),processor=processors[0],next=int(np.argmax(scores)),count=0)
            return uids
        def next_generated(self):
            rows=[]
            for uid in reversed(list(self.active)):
                row=self.active[uid];token=row.next
                row.context.append(token);row.count+=1
                logits=row.processor(np.array(row.context),np.zeros((1,256)))
                row.next=int(np.argmax(logits))
                reason='stop' if token==99 else ('length' if row.count>=self.max_tokens else None)
                rows.append(response(uid,token,reason))
                if reason:del self.active[uid]
            return rows
        def close(self):self.closed=True
    fake_generate=ModuleType('mlx_lm.generate');fake_generate.BatchGenerator=Generator
    monkeypatch.setitem(sys.modules,'mlx_lm.generate',fake_generate)
    return state


def test_heterogeneous_prompts_have_independent_masked_greedy_state_and_uid_callbacks(backend):
    seen=[]
    rows=audit.generate_audit_batch(['map-a','map-b','map-c'],'adapter',batch_size=2,
        max_tokens=10,corpus_root='fixture-corpus',on_result=lambda *args:seen.append(args))
    assert [row['status'] for row in rows]==['complete']*3
    assert [row['tokens'] for row in rows]==[[11,99],[12,99],[13,99]]
    assert [row['plan'].instance_id for row in rows]==['map-a','map-b','map-c']
    assert [index for index,row in seen]==[2,1,0]  # responses routed, never positional
    assert all(row is rows[index] for index,row in seen)
    assert backend.prompts==[backend.maps[name].prompt for name in backend.maps]
    assert len({id(items[0].decoder) for items in backend.processors})==3
    assert [items[0].prompt_len for items in backend.processors]==[2,3,4]
    assert [decoder.tokens for decoder in backend.decoders.values()]==[[11,99],[12,99],[13,99]]
    assert backend.constructor==dict(max_tokens=10,stop_tokens=[[99]],completion_batch_size=2,
                                    prefill_batch_size=2,prefill_step_size=512)
    assert backend.generator.closed
    assert [event[0] for event in backend.events[:4]]==['guard','guard','guard','planner']
    assert all('audit.adapter'==row['plan'].provenance.created_by or row['plan'].provenance.created_by=='fixture'
               for row in rows)


def test_length_status_never_calls_to_plan_or_labels_partial_plan_complete(backend):
    rows=audit.generate_audit_batch(['map-a'],'adapter',max_tokens=1)
    assert rows[0]['status']=='max_tokens' and rows[0]['plan'] is None
    assert rows[0]['tokens']==[11]
    assert backend.generator.closed


def test_callback_exception_closes_generator(backend):
    def callback(*args):raise RuntimeError('callback failed')
    with pytest.raises(RuntimeError,match='callback failed'):
        audit.generate_audit_batch(['map-a'],'adapter',on_result=callback)
    assert backend.generator.closed


def test_held_out_guard_runs_before_resident_load(backend):
    backend.guard_error='held-out instance refused'
    with pytest.raises(ValueError,match='held-out'):
        audit.generate_audit_batch(['map-a'],'adapter')
    assert not any(event[0]=='resident' for event in backend.events)


def test_multitoken_and_inconsistent_stop_tokens_refused(backend):
    backend.codecs['map-a']=NS(end_plan_tokens=[98,99])
    with pytest.raises(RuntimeError,match='single-token'):
        audit.generate_audit_batch(['map-a'],'adapter')
    backend.codecs['map-a']=NS(end_plan_tokens=[98])
    with pytest.raises(ValueError,match='stop tokens differ'):
        audit.generate_audit_batch(['map-a','map-b'],'adapter')


def test_invalid_batch_size_refused_without_importing_model(monkeypatch):
    with pytest.raises(ValueError,match='batch size'):
        audit.generate_audit_batch(['missing'],'missing',batch_size=0)


def test_installed_batch_generator_contract_is_greedy_and_per_prompt_processor_lists():
    # Parse installed source instead of importing mlx_lm (which initializes Metal).
    paths=list((Path(sys.prefix)/'lib').glob('python*/site-packages/mlx_lm/generate.py'))
    assert paths,'installed mlx-lm source unavailable'
    tree=ast.parse(paths[0].read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='BatchGenerator')
    init=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
    assert any(isinstance(n,ast.Assign) and ast.unparse(n)=='self.sampler = sampler or greedy_sampler'
               for n in ast.walk(init))
    insert=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='insert')
    assert 'logits_processors' in [argument.arg for argument in insert.args.args]
    batch=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='GenerationBatch')
    step=next(n for n in batch.body if isinstance(n,ast.FunctionDef) and n.name=='_step')
    assert 'self.logits_processors[e]' in ast.unparse(step)
    assert 'processor(context, sample_logits)' in ast.unparse(step)



def test_dead_end_fallback_stop_is_never_mislabeled_complete(backend):
    backend.dead_end_maps.add('map-b')
    rows=audit.generate_audit_batch(['map-a','map-b'],'adapter')
    assert [row['status'] for row in rows]==['complete','dead_end']
    assert rows[1]['plan'] is None and rows[1]['tokens']==[99]
    assert backend.decoders['map-b'].tokens==[]
    assert backend.generator.closed
