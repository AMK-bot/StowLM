"""D111 mixed-temperature production decode and backtracking CPU contracts."""
import importlib.util
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[3]


@pytest.fixture
def backend(monkeypatch):
    mx=ModuleType('mlx.core')
    for name in ('array','float32','where','take_along_axis','argmax','arange','repeat'):
        setattr(mx,name,getattr(np,name))
    def logsumexp(values,axis=-1,keepdims=False):
        top=np.max(values,axis=axis,keepdims=True)
        result=top+np.log(np.exp(values-top).sum(axis=axis,keepdims=True))
        return result if keepdims else np.squeeze(result,axis=axis)
    mx.logsumexp=logsumexp
    mx.eval=mx.async_eval=lambda *args:None
    mx.random=NS(key=lambda seed:seed,split=lambda key:(key+1,key+2),
                 categorical=lambda values,key=None:np.argmax(values,axis=-1))
    parent=ModuleType('mlx');parent.core=mx
    monkeypatch.setitem(sys.modules,'mlx',parent)
    monkeypatch.setitem(sys.modules,'mlx.core',mx)
    name='_exit_decode_rows_numpy'
    spec=importlib.util.spec_from_file_location(name,ROOT/'src/llm/stowlm/mask_mlx.py')
    module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,name,module)
    spec.loader.exec_module(module)
    return module,mx


def test_mixed_row_sampling_matches_scalar_distribution_and_greedy_ties(backend):
    module,_=backend
    logits=np.array([[2,100,2],[0,100,2],[1,100,0]],dtype=np.float32)
    sets=[(2,0),(0,2),(0,2)]
    idx,valid=module._index_matrix(sets)
    temps=np.array([0.,2.,.5])
    tokens,scores=module.masked_sample_rows(logits,idx,valid,temps)
    assert tokens.tolist()==[2,2,0]
    assert scores[0]==0.
    for row,temp in enumerate(temps):
        tok,score=module.masked_sample(logits[row:row+1],idx[row:row+1],valid[row:row+1],temp)
        np.testing.assert_array_equal(tokens[row:row+1],tok)
        np.testing.assert_allclose(scores[row:row+1],score,atol=1e-6)


class Decoder:
    def __init__(self,length,*,dead=False):
        self.length=length;self.status='dead_end' if dead else 'running';self.tokens=[]
    def allowed_token_ids(self):return (0,1) if self.status=='running' else ()
    def prefetch(self):return False
    def advance(self,token):
        self.tokens.append(token)
        if len(self.tokens)>=self.length:self.status='complete'


class Model:
    def __init__(self):self.prefills=0;self.batches=[]
    def make_cache(self):
        self.prefills+=1
        return [NS(keys=np.zeros((1,1,1,1)),values=np.zeros((1,1,1,1)),state=())]
    def __call__(self,ids,cache):
        self.batches.append((len(ids),len(cache[0].keys)))
        return np.broadcast_to(np.array([0.,1.]),(*ids.shape,2))


@pytest.mark.parametrize('dead_first',[False,True])
def test_per_row_temperatures_follow_original_ids_after_terminal_kv_row_drop(backend,dead_first):
    module,_=backend
    temps_seen=[]
    original=module.masked_sample_rows
    def record(logits,idx,valid,temps,key=None):
        temps_seen.append(list(temps))
        return original(logits,idx,valid,temps,key)
    module.masked_sample_rows=record
    decoders=[Decoder(1,dead=dead_first),Decoder(3),Decoder(2)]
    model=Model()
    results,stats=module.generate_masked(model,[2],decoders,temperature=[0.,.7,1.],max_tokens=8,prefetch=False)
    assert model.prefills==1
    assert model.batches==[(3,3),(2,2),(1,1)]
    assert temps_seen==([[.7,1.],[.7,1.],[.7]] if dead_first else [[0.,.7,1.],[.7,1.],[.7]])
    assert [r.status for r in results]==(['dead_end','complete','complete'] if dead_first else ['complete']*3)
    assert results[0].masked_logprobs==([] if dead_first else [0.])
    assert len(results[1].tokens)==3 and len(results[2].tokens)==2
    assert stats.batch_sizes==([2,2,1] if dead_first else [3,2,1])


@pytest.mark.parametrize('decoders,temperatures',[(None,[0.]),([Decoder(1)],[0.,.8]),([Decoder(1)],[])])
def test_per_row_contract_rejected_before_prefill(backend,decoders,temperatures):
    module,_=backend
    model=Model()
    with pytest.raises(ValueError,match='per-row'):
        module.generate_masked(model,[2],decoders,temperature=temperatures,prefetch=False)
    assert model.prefills==0


def test_scalar_default_remains_on_original_sampling_path(backend):
    module,_=backend
    module.masked_sample_rows=lambda *a,**kw:pytest.fail('scalar path changed')
    results,_=module.generate_masked(Model(),[2],[Decoder(1),Decoder(2)],temperature=.8,max_tokens=4,prefetch=False)
    assert [result.status for result in results]==['complete','complete']


def test_sampler_change_disables_optional_pruning_until_identity_review(backend):
    from llm.exit.pruning import sampler_is_append_only
    assert not sampler_is_append_only(backend[0].generate_masked)


@pytest.fixture
def backtrack_backend(backend,monkeypatch):
    module,mx=backend
    monkeypatch.setitem(sys.modules,'llm.stowlm.mask_mlx',module)
    cache_module=ModuleType('mlx_lm.models.cache')
    trims=[]
    cache_module.can_trim_prompt_cache=lambda cache:True
    cache_module.trim_prompt_cache=lambda cache,n:trims.append(n)
    monkeypatch.setitem(sys.modules,'mlx_lm.models.cache',cache_module)
    name='_exit_backtrack_numpy'
    spec=importlib.util.spec_from_file_location(name,ROOT/'src/llm/stowlm/backtrack.py')
    backtrack=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,name,backtrack)
    spec.loader.exec_module(backtrack)
    return backtrack,cache_module,trims


class BranchDecoder:
    """One-token actions: branch 1 dead-ends; branch 2 permits terminal token 3."""
    def __init__(self):
        self.actions=[];self.tokens=[];self.status='running';self.dead_end=None
        self._prefix=[];self._node=None;self._allowed=None
        self._cache=NS(encode=lambda action:([action],1))
    def allowed_token_ids(self):
        if self.status!='running':return ()
        allowed=(1,2) if not self.actions else (3,)
        self._node=NS(children={token:NS(count=1,alive=1,screened=False) for token in allowed})
        return allowed
    def advance(self,token):
        self.tokens.append(token)
        if token==3:self.status='complete'
        else:
            self.actions.append(token)
            if token==1:self.status='dead_end'
    def pop_action(self):
        action=self.actions.pop();self.tokens.pop()
        self._node=None;self._allowed=None;self.status='running';self.dead_end=None
        return action


class BranchModel:
    def make_cache(self):return []
    def __call__(self,ids,cache):
        return np.broadcast_to(np.array([0.,4.,3.,2.]),(*ids.shape,4))


@pytest.mark.parametrize('temperature',[0.,.8])
def test_production_backtracking_rewinds_only_failed_action_and_retains_logprob_alignment(backtrack_backend,temperature):
    backtrack,_,trims=backtrack_backend
    inner=BranchDecoder();decoder=backtrack.ForbiddingDecoder(inner)
    result,stats=backtrack.generate_backtracking(BranchModel(),[0],decoder,temperature=temperature,
                                                max_backtracks=3,max_tokens=10)
    assert result.status=='complete' and result.tokens==[2,3]
    assert len(result.masked_logprobs)==len(result.tokens)==2
    assert inner.actions==[2] and decoder.forbidden=={():{1}}
    assert stats['backtracks']==1 and stats['rewound_actions']==1 and stats['cache_trims']==1
    assert stats['steps']==3 and trims==[1]
    if temperature==0:assert result.masked_logprobs==[0.,0.]


def test_production_backtracking_budget_zero_preserves_true_dead_end(backtrack_backend):
    backtrack,_,trims=backtrack_backend
    decoder=backtrack.ForbiddingDecoder(BranchDecoder())
    result,stats=backtrack.generate_backtracking(BranchModel(),[0],decoder,temperature=0.,max_backtracks=0,max_tokens=10)
    assert result.status=='dead_end' and result.tokens==[1]
    assert stats['backtracks']==0 and trims==[]


def test_production_backtracking_cache_falls_back_to_prefill_when_untrimmable(backtrack_backend):
    backtrack,cache,trims=backtrack_backend
    cache.can_trim_prompt_cache=lambda cache:False
    decoder=backtrack.ForbiddingDecoder(BranchDecoder())
    result,stats=backtrack.generate_backtracking(BranchModel(),[0],decoder,temperature=0.,max_backtracks=3,max_tokens=10)
    assert result.status=='complete'
    assert stats['cache_trims']==0 and stats['cache_refills']==1 and trims==[]
