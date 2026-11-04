"""A replay shares one pool while each task keeps its exact FSM phase."""
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from stowage.checker import in_port_evaluator as physics
from stowage.model import PartReport
from stowage.model.fixtures import build_instance


@dataclass
class Context:
    geometry: object
    params: object
    departure_ship: object
    max_fsm_tank_ids: frozenset = frozenset({'HEEL'})

    def for_phase(self, phase):
        return replace(self,max_fsm_tank_ids=frozenset(
            {'HEEL'} if phase=='arrival' else {'HEEL','DB'}))


@pytest.mark.parametrize('workers',[1,2])
def test_phase_views_share_pool_and_cold_reports_without_crossing_fsm(monkeypatch,workers):
    pools=[]
    class Pool:
        def __init__(self,*,initializer,initargs,**kwargs):
            pools.append(self);self.stops=0;initializer(*initargs)
        def submit(self,fn,*args):
            return SimpleNamespace(result=lambda:fn(*args),cancel=lambda:None)
        def shutdown(self,**kwargs): self.stops+=1
    def check(instance,ship,context,continuation=None,**kwargs):
        assert not kwargs['warm_start']
        return PartReport(name='in_port',metrics={'fsm_count':float(len(context.max_fsm_tank_ids))})
    monkeypatch.setattr(physics,'ProcessPoolExecutor',Pool)
    monkeypatch.setattr(physics,'check_state',check)
    monkeypatch.setattr(physics,'_interval_report',lambda i,s,c,r:r.model_copy(
        update={'metrics':dict(r.metrics,interval_fsm_count=float(len(c.max_fsm_tank_ids)))}))
    monkeypatch.setenv('OPENBLAS_NUM_THREADS','1')
    monkeypatch.setenv('OMP_NUM_THREADS','1')
    context=Context(object(),{},object())
    with physics.InPortEvaluator(build_instance(),context,workers=workers,warm_start=False) as owner:
        for phase,count in [('arrival',1.),('departure',2.),('arrival',1.)]:
            with owner.for_context(context.for_phase(phase)) as view:
                base=view.evaluate([SimpleNamespace()]).reports
                enriched=view.evaluate_intervals([SimpleNamespace()],base).reports
                assert base[0].metrics=={'fsm_count':count}
                assert enriched[0].metrics=={'fsm_count':count,'interval_fsm_count':count}
        assert len(pools)==(1 if workers==2 else 0)
        assert all(pool.stops==0 for pool in pools)
    assert all(pool.stops==1 for pool in pools)
    with pytest.raises(RuntimeError,match='closed'):
        owner.for_context(context)


def test_phase_view_rejects_another_departure_candidate():
    context=Context(object(),{},object())
    with physics.InPortEvaluator(build_instance(),context,warm_start=False) as owner:
        with pytest.raises(ValueError,match='same liquid context'):
            owner.for_context(replace(context,departure_ship=object()))


@pytest.mark.parametrize('operation',['evaluate','evaluate_intervals'])
def test_serial_phase_view_rejects_closed_owner(monkeypatch,operation):
    context=Context(object(),{},object())
    owner=physics.InPortEvaluator(build_instance(),context,warm_start=False)
    view=owner.for_context(context)
    monkeypatch.setattr(physics,'_timed_check',lambda *a,**kw:(PartReport(name='in_port'),.1))
    monkeypatch.setattr(physics,'_timed_interval',lambda *a,**kw:(PartReport(name='in_port'),.1))
    owner.close()
    with pytest.raises(RuntimeError,match='closed'):
        if operation=='evaluate':
            view.evaluate([SimpleNamespace()])
        else:
            view.evaluate_intervals([SimpleNamespace()],[PartReport(name='in_port')])


def test_spawn_initializer_receives_reference_reuse_snapshot(monkeypatch):
    from stowage.checker.perf import reuse_enabled,without_reuse
    snapshots=[]
    class Pool:
        def __init__(self,*,initializer,initargs,**kwargs):
            snapshots.append((len(initargs),initargs[-1]))
            initializer(*initargs)
        def shutdown(self,**kwargs): pass
    monkeypatch.setattr(physics,'ProcessPoolExecutor',Pool)
    context=Context(object(),{},object())
    with without_reuse():
        with physics.InPortEvaluator(build_instance(),context,workers=2,warm_start=False) as owner:
            owner._ensure_pool()
        assert not reuse_enabled()
    assert snapshots==[(5,False)]
    assert reuse_enabled()  # A fake initializer must not leak into its parent.


@pytest.mark.parametrize('worker',['_worker_check','_worker_interval_check',
    '_worker_phase_check','_worker_phase_interval'])
def test_worker_reference_snapshot_is_scoped_per_call(monkeypatch,worker):
    from stowage.checker.perf import reuse_enabled
    context=Context(object(),{},object())
    observed=[]
    def timed(*args,**kwargs):
        observed.append(reuse_enabled())
        return PartReport(name='in_port'),.1
    monkeypatch.setattr(physics,'_timed_check',timed)
    monkeypatch.setattr(physics,'_timed_interval',timed)
    physics._worker_init(build_instance(),context,False,False,False)
    assert reuse_enabled()
    ship,report=SimpleNamespace(),PartReport(name='in_port')
    arguments={'_worker_check':(ship,),
        '_worker_interval_check':(ship,report),
        '_worker_phase_check':(ship,context.max_fsm_tank_ids,False),
        '_worker_phase_interval':(ship,report,context.max_fsm_tank_ids)}
    getattr(physics,worker)(*arguments[worker])
    assert observed==[False]
    assert reuse_enabled()
    # Default-True initializer remains compatible and resets prior worker state.
    physics._worker_init(build_instance(),context,False)
    getattr(physics,worker)(*arguments[worker])
    assert observed==[False,True]
    assert reuse_enabled()
