"""Reservation bounds are optimistic search screens, not checker certificates."""
from types import SimpleNamespace
import random
import time

import numpy as np
import pytest

from stowage.actions.space import Place
from stowage.solver.construct_v4 import FutureDomains, _choices, construct
from stowage.solver.scalable import ScalableConfig, _Node


def fixture(*, heights=(2., 2., 2.), limit=5., static=None):
    ids = [f'b{k}' for k in range(len(heights))]
    cells = ['0010102', '0010104', '0010106']
    boxes = {cid: SimpleNamespace(length_ft=20) for cid in ids}
    t = SimpleNamespace(
        box_ids=ids, box_index={c:k for k,c in enumerate(ids)},
        cell_ids=cells, cell_index={c:k for k,c in enumerate(cells)},
        slot_deck=np.zeros(3,dtype=bool),
        slot_ids=cells, slot_index={c:k for k,c in enumerate(cells)},
        stack_ids=['s'], stack_hmax=np.array([10.]), stack_base_z=np.array([0.]),
        stack_cells=[[0,1,2]], stack_deck=np.array([False]),
        cell_stack=np.array([0,0,0]), slot_stacks=np.array([[0,-1]]*3),
        slot_k=np.array([[0,-1],[1,-1],[2,-1]]),
        extra={'stack_visibility_top':np.array([limit])},
        static=np.ones((len(ids),3),dtype=bool) if static is None else np.asarray(static,dtype=bool),
        height=np.array(heights), rank=np.ones(len(ids),dtype=np.int64),
        units=np.ones(len(ids),dtype=np.int64), length=np.full(len(ids),20),
        mass=np.full(len(ids),10000.), share=np.full(len(ids),10000.),
        e1_threshold=20000., e2_tol=0, closes_stack=np.zeros(len(ids),dtype=bool),
        stack_lim_20=np.array([100000.]), stack_lim_40=np.array([100000.]),
        stack_lim_mixed=np.array([100000.]))
    instance = SimpleNamespace(containers=[SimpleNamespace(id=c, length_ft=20) for c in ids],
                               load_list=ids,discharge_list=[])
    ex = SimpleNamespace(where={},buffer=[],boxes=boxes)
    return instance,t,ex


def test_visibility_bound_counts_actual_minimum_height():
    instance,t,ex=fixture()
    future=FutureDomains(instance,t)
    _,domain=future.evaluate(ex,instance.load_list)
    assert domain[0].tolist()==[True,True,False]
    assert future.last_capacity==dict(demand=3,capacity=2,min_height_m=2.)


def test_optimistic_missing_supports_keep_future_slot():
    instance,t,ex=fixture(heights=(2.,3.),limit=7.)
    future=FutureDomains(instance,t)
    _,domain=future.evaluate(ex,instance.load_list)
    # The three-metre box can occupy the third cell after two minimum-height
    # supports, even though that cell is not currently an open frontier slot.
    assert domain[1,2]


def test_existing_lighter_support_removes_heavy_future_domain():
    instance,t,ex=fixture(heights=(2.,2.),limit=10.)
    t.rank[:]=[0,2]
    t.mass[:]=[9000.,24000.]
    ex.where={'b0':('ship','0010102')}
    future=FutureDomains(instance,t)
    _,domain=future.evaluate(ex,['b1'])
    assert not domain.any()


def test_root_capacity_deficit_prunes_before_oracle_enumeration():
    instance,t,ex=fixture()
    engine=SimpleNamespace(legal_pairs=lambda:pytest.fail('capacity deficit should prune before oracle'))
    node=SimpleNamespace(ex=ex,engine=engine)
    metrics={'candidate_probes':0,'legal_candidates':0}
    assert _choices(instance,node,FutureDomains(instance,t),random.Random(0),metrics,0)==[]
    assert metrics['v4_visibility_capacity']['capacity']==2
    assert metrics['v4_reservation_prunes']==1


def test_deadline_does_not_query_successors():
    instance,t,ex=fixture()
    engine=SimpleNamespace(backend=SimpleNamespace(T=t))
    node=_Node(engine,ex)
    completed,deepest=construct(instance,node,{},ScalableConfig(constructor_v4=True),
                               time.monotonic()-1,random.Random(0),{}, {},None)
    assert completed==[]
    assert deepest is node


def test_custom_oracle_delegates_with_opt_in_disabled(monkeypatch):
    import stowage.solver.scalable as scalable
    instance,t,ex=fixture()
    node=_Node(SimpleNamespace(),ex)
    seen=[]
    def legacy(instance,start,slots,cfg,*args):
        seen.append(cfg.constructor_v4)
        return ['legacy'],start
    monkeypatch.setattr(scalable,'_construct',legacy)
    completed,_=construct(instance,node,{},ScalableConfig(constructor_v4=True),
                          time.monotonic()+1,random.Random(0),{}, {},None)
    assert completed==['legacy']
    assert seen==[False]


def test_unique_static_destination_schedules_before_flexible_cargo(monkeypatch):
    import stowage.solver.scalable as scalable
    monkeypatch.setattr(scalable,'_moment_preference',lambda *args:0.)
    instance,t,ex=fixture(heights=(2.,2.),limit=6.,static=[[1,1,1],[1,0,0]])
    engine=SimpleNamespace(legal_pairs=lambda:(np.array([0,0,0,1]),np.array([0,1,2,0])),
                           forces_restow=lambda action:False)
    node=_Node(engine,ex)
    metrics={'candidate_probes':0,'legal_candidates':0}
    choices=_choices(instance,node,FutureDomains(instance,t),random.Random(0),metrics,0)
    assert choices[0]==Place('b1','0010102')


def test_forced_ship_restows_excluded_from_restricted_search(monkeypatch):
    import stowage.solver.scalable as scalable
    monkeypatch.setattr(scalable,'_moment_preference',lambda *args:0.)
    instance,t,ex=fixture(heights=(2.,),limit=6.)
    engine=SimpleNamespace(legal_pairs=lambda:(np.array([0]),np.array([0])),
                           forces_restow=lambda action:True)
    metrics={'candidate_probes':0,'legal_candidates':0}
    assert _choices(instance,_Node(engine,ex),FutureDomains(instance,t),random.Random(0),metrics,0)==[]


def test_native_tiny_candidate_replays_every_public_mask_action():
    from stowage.actions import get_action_space
    from stowage.actions.samples import tiny_instance
    from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
    from stowage.solver.scalable import ScalableSolver
    instance=tiny_instance(71,n_load=3,n_other=0,p40=0)
    feasibility=FeasibilityConfig(extra={'global_screen':0})
    result=ScalableSolver(ScalableConfig(constructor_v4=True,max_neighborhoods=0,
                                      feasibility_config=feasibility)).plan(instance,time_limit_s=3.)
    assert result.status=='ok',result.message
    assert result.metrics['constructor']=='v4-reservation-dfs'
    assert result.metrics['requires_finalize_and_check']
    assert not result.metrics['checker_passed']
    space=get_action_space()
    engine=make_feasibility_engine(instance,space,config=feasibility)
    for action in space.from_plan(instance,result.plan):
        assert engine.is_legal(action)
        engine.push(action)
    assert engine.is_complete()
