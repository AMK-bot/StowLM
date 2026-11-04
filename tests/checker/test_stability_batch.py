"""Activation snapshots and ordered, exact independent in-port evaluations."""
import importlib
import json

import pytest

from stowage.model import PartReport, Violation
from tests.checker.test_stability_policy_fixtures import build_instance


def api():
    return importlib.import_module('stowage.checker.stability_batch')


def test_public_batch_docs_state_maximum_displacement_density_condition():
    docs=api().check_in_port_states.__doc__.lower()
    assert '1025' in docs and 'density' in docs and 'omit' in docs


def test_activation_is_read_once_and_off_does_no_physics(monkeypatch):
    from stowage.rules import catalogue
    # Instance validation has its own activation reads; count the evaluator only.
    instance = build_instance()
    reads = []
    monkeypatch.setattr(catalogue,'rule_freeze_v1_enabled',lambda:reads.append(1) or False)
    module = api()
    with module.InPortStateEvaluator(instance,call_index=0) as evaluator:
        assert evaluator.enabled is False
        assert evaluator.evaluate([instance.onboard]).reports == ()
        assert evaluator.evaluate([]).reports == ()
    assert reads == [1]


def test_serial_stops_at_first_unavailable_or_hard_failure(monkeypatch):
    from stowage.rules import catalogue
    from stowage.checker import stability
    monkeypatch.setattr(catalogue,'rule_freeze_v1_enabled',lambda:True)
    seen = []
    def check(instance,ship,*,call_index,in_port):
        assert in_port
        seen.append(ship.vessel_id)
        if ship.vessel_id == 'bad':
            return PartReport(name='stability',violations=[Violation(rule_id='port.stability.cargo_operations',severity='hard',call_index=0,message='list')])
        return PartReport(name='stability')
    monkeypatch.setattr(stability,'check_stability',check)
    i=build_instance()
    ships=[i.onboard.model_copy(update={'vessel_id':name}) for name in ('ok','bad','later')]
    with api().InPortStateEvaluator(i,call_index=0) as evaluator:
        result=evaluator.evaluate(ships,stop_at_first_failure=True)
        assert result.failure_index == 1
        assert result.total_states == 3
        assert len(result.reports) == 2
        assert seen == ['ok','bad']
        seen.clear()
        assert len(evaluator.evaluate(ships).reports) == 3
        assert seen == ['ok','bad','later']


def test_unavailable_is_failure_but_soft_diagnostic_is_not(monkeypatch):
    from stowage.rules import catalogue
    from stowage.checker import stability
    monkeypatch.setattr(catalogue,'rule_freeze_v1_enabled',lambda:True)
    reports=[PartReport(name='stability',violations=[Violation(rule_id='port.stability.cargo_operations',severity='soft',call_index=0,message='diagnostic')]),
             PartReport(name='stability',ran=False,error='unavailable')]
    monkeypatch.setattr(stability,'check_stability',lambda *args,**kwargs:reports.pop(0))
    i=build_instance()
    with api().InPortStateEvaluator(i,call_index=0) as evaluator:
        result=evaluator.evaluate([i.onboard,i.onboard],stop_at_first_failure=True)
    assert result.failure_index == 1 and len(result.reports) == 2


def test_worker_count_and_lifetime_are_explicit():
    i=build_instance()
    module=api()
    for value in (0,-1,True,1.5):
        with pytest.raises((TypeError,ValueError)):
            module.InPortStateEvaluator(i,call_index=0,workers=value)
    evaluator=module.InPortStateEvaluator(i,call_index=0)
    evaluator.close()
    with pytest.raises(RuntimeError,match='closed'):
        evaluator.evaluate([])


@pytest.mark.parametrize('raises',[False,True])
def test_parallel_submission_is_bounded_and_cancelled_on_stop_or_error(monkeypatch,raises):
    from stowage.rules import catalogue
    monkeypatch.setattr(catalogue,'rule_freeze_v1_enabled',lambda:True)
    monkeypatch.setenv('OPENBLAS_NUM_THREADS','1')
    monkeypatch.setenv('OMP_NUM_THREADS','1')
    module=api()
    submitted,cancelled,closed=[],[],[]
    class Future:
        def __init__(self,index): self.index=index
        def result(self):
            if self.index == 1:
                if raises: raise RuntimeError('worker failed')
                return PartReport(name='stability',ran=False,error='unavailable')
            return PartReport(name='stability',metrics={'index':float(self.index)})
        def cancel(self): cancelled.append(self.index)
    class Pool:
        def __init__(self,**kwargs): assert kwargs['max_workers']==2
        def submit(self,function,ship):
            index=len(submitted)
            submitted.append(ship)
            return Future(index)
        def shutdown(self,**kwargs): closed.append(kwargs)
    monkeypatch.setattr(module,'ProcessPoolExecutor',Pool)
    i=build_instance()
    with module.InPortStateEvaluator(i,call_index=0,workers=2) as evaluator:
        if raises:
            with pytest.raises(RuntimeError,match='worker failed'):
                evaluator.evaluate([i.onboard]*100,stop_at_first_failure=True)
        else:
            result=evaluator.evaluate([i.onboard]*100,stop_at_first_failure=True)
            assert result.failure_index==1 and len(result.reports)==2
            assert result.reports[0].metrics['index']==0.
    assert len(submitted)==3  # Two initial tasks, then one replacement.
    assert cancelled==[2]
    assert closed==[{'wait':True,'cancel_futures':True}]


@pytest.mark.slow
def test_spawned_workers_match_serial_reports_and_first_failure(monkeypatch):
    from stowage.rules import catalogue
    from stowage.vessel.fleet import load_vessel
    monkeypatch.setattr(catalogue,'rule_freeze_v1_enabled',lambda:True)
    i=build_instance()
    i.vessel=load_vessel('feeder_500')
    i.vessel.fixed_loads=[]
    i.containers=[]
    ship=i.onboard.model_copy(update={'vessel_id':i.vessel.id,'placements':[],
                                     'tank_masses_kg':{},'covers_off':{}})
    ships=[ship,ship.model_copy(update={'vessel_id':'wrong-vessel'}),ship]
    module=api()
    with module.InPortStateEvaluator(i,call_index=0,workers=1) as serial:
        expected=serial.evaluate(ships)
    with module.InPortStateEvaluator(i,call_index=0,workers=2) as parallel:
        actual=parallel.evaluate(ships)
        first=parallel.evaluate(ships,stop_at_first_failure=True)
    dump=lambda reports:json.dumps([r.model_dump(mode='json') for r in reports],sort_keys=True)
    assert dump(actual.reports) == dump(expected.reports)
    assert first.failure_index == expected.failure_index
    assert expected.failure_index == 1
    assert dump(first.reports) == dump(expected.reports[:first.failure_index+1])
    assert expected.reports[0].ran and not expected.reports[0].violations
