"""D139 final freeze gets one finisher attempt; CPU fixtures only."""
from contextlib import nullcontext,contextmanager
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH=Path(__file__).resolve().parents[1]/'final_recipe.py'
spec=importlib.util.spec_from_file_location('_d139_final_recipe',PATH)
F=importlib.util.module_from_spec(spec);spec.loader.exec_module(F)


@pytest.fixture
def harness(tmp_path,monkeypatch):
    from llm.exit import runtime,identity,selection
    from llm.stowlm import planner
    from stowage.model import MovePlan,PlannerResult
    from stowage.model.fixtures import build_instance
    instance=build_instance();path=tmp_path/'train-synthetic.json';path.write_text(instance.model_dump_json())
    plan=MovePlan(id='own',instance_id=instance.id,call_index=instance.call_index)
    result=PlannerResult(status='ok',plan=plan,planner='fake',planner_version='1')
    state={'result':result,'verify_calls':[],'plan_calls':[]}
    class Planner:
        def __init__(self,**kwargs):state['config']=kwargs
        def plan(self,instance,**kwargs):
            state['plan_calls'].append(kwargs)
            return state['result']
    monkeypatch.setattr(planner,'StowLMPlanner',Planner)
    monkeypatch.setattr(runtime,'_resident_for',lambda planner:None)
    monkeypatch.setattr(identity,'identity_guard',lambda root:
        SimpleNamespace(require_allowed=lambda *args,**kwargs:None))
    monkeypatch.setattr(selection,'decoder_factory',lambda:object())
    def verify(instance,candidate,**kwargs):
        state['verify_calls'].append(kwargs)
        return SimpleNamespace(passed=True,plan=plan,weighted_moves=0.,
            details={'checker_available':True,'report':{'passed':True},'mask':{'passed':True}})
    monkeypatch.setattr(selection,'verify_candidate',verify)
    monkeypatch.setattr(F,'_pins',lambda parent:{'adapter':'weights','sources':'frozen'})
    monkeypatch.setattr(F,'scoped_v4_finalize',nullcontext)
    monkeypatch.delenv('STOWAGE_BALLAST_V4',raising=False)
    args=SimpleNamespace(corpus=tmp_path,seed=0,max_tokens=20)
    def run():return F.evaluate_recipe(tmp_path/'adapter',[path],tmp_path/'audit',args)[0]
    return SimpleNamespace(state=state,run=run,plan=plan,args=args,tmp=tmp_path,Planner=Planner)


def test_exhausted_finisher_is_not_retried_and_is_not_dead_end_or_physics_fail(harness):
    h=harness
    h.state['result']=h.state['result'].model_copy(update={'metrics':
        {'n_candidates_checked':1,'n_checker_feasible':0,'finisher_budget_timeouts':[0]}})
    row=h.run()
    assert h.state['verify_calls']==[]
    assert not row['passed'] and row['plan'] is None
    assert row['second_finalize_refused'] and row['planner_metrics']['second_finalize_refused']
    assert row['finisher_timeout'] and row['finisher_outcomes']['finisher_timeouts']==1
    assert row['finisher_outcomes']['planner_dead_ends']==0
    assert row['verification_counts']['physics_failed']==0 and not row['unavailable']
    recipe=json.loads((h.tmp/'audit/identity.json').read_text())['recipe']
    assert recipe['finisher_attempts_per_candidate']==1
    assert recipe['version']=='d139-greedy-single-finisher-v1'


def test_checker_rejection_not_retried_but_is_not_fabricated_finisher_timeout(harness):
    h=harness
    h.state['result']=h.state['result'].model_copy(update={'metrics':
        {'n_candidates_checked':1,'n_checker_feasible':0,'finisher_budget_timeouts':[],
         'candidate_failures':{'0':'full checker rejected candidate or report binding'}}})
    row=h.run()
    assert h.state['verify_calls']==[] and row['second_finalize_refused']
    assert not row['passed'] and not row['finisher_timeout']
    assert row['verification']['outcome']=='candidate_not_qualified'
    assert row['verification_counts']['physics_failed']==0


@pytest.mark.parametrize('checked',[0,None])
def test_zero_or_missing_checked_count_allows_first_finisher(harness,checked):
    h=harness
    h.state['result']=h.state['result'].model_copy(update={'metrics':
        {'n_candidates_checked':checked,'n_checker_feasible':0}})
    row=h.run()
    assert row['passed'] and not row['second_finalize_refused']
    assert len(h.state['verify_calls'])==1
    assert h.state['verify_calls'][0]['ballast_s']==120.


def test_already_ballasted_plan_may_be_full_verified(harness):
    from stowage.model.plan import BallastCondition
    h=harness
    plan=h.plan.model_copy(update={'ballast':BallastCondition(tank_masses_kg={},source='finisher')})
    h.state['result']=h.state['result'].model_copy(update={'plan':plan,
        'metrics':{'n_candidates_checked':1,'n_checker_feasible':1}})
    assert h.run()['passed'] and len(h.state['verify_calls'])==1


def test_unavailable_internal_checker_remains_unavailable_not_timeout_or_physics_fail(harness,monkeypatch):
    from llm.exit import checker_watchdog
    from stowage import checker
    h=harness;original=checker.check
    monkeypatch.setattr(checker_watchdog,'run_full_check',lambda *args,**kwargs:
        checker_watchdog.CheckOutcome(False,reason='watchdog_expired'))
    def plan(self,instance,**kwargs):
        checker.check(instance,h.plan)
        pytest.fail('unavailable checker must leave planner through BaseException')
    monkeypatch.setattr(h.Planner,'plan',plan)
    row=h.run()
    assert row['unavailable'] and not row['passed']
    assert not row['finisher_timeout'] and not row['second_finalize_refused']
    assert row['verification_counts']['unavailable']==1
    assert row['verification_counts']['physics_failed']==0
    assert row['finisher_outcomes']['checker_unavailable']==1
    assert h.state['verify_calls']==[] and checker.check is original


def test_metrics_unavailable_cannot_become_false_finisher_timeout(harness):
    h=harness
    h.state['result']=h.state['result'].model_copy(update={'metrics':
        {'n_candidates_checked':1,'n_checker_feasible':0,'checker_available':False,
         'finisher_budget_timeouts':[0]}})
    row=h.run()
    assert not row['passed'] and row['unavailable'] and not row['finisher_timeout']
    assert row['finisher_outcomes']['checker_unavailable']==1


def test_scoped_wrapper_refuses_retry_before_entering_v4_scope(monkeypatch):
    from stowage.model import MovePlan,PlannerResult
    from stowage import planner
    from llm.exit import selection
    result=PlannerResult(status='ok',plan=MovePlan(id='p',instance_id='i',call_index=0),planner='fake',planner_version='1',
        metrics={'n_candidates_checked':1,'n_checker_feasible':0})
    calls=[]
    def finalize(*args,**kwargs):calls.append('finalize');return args[1]
    @contextmanager
    def scope(enabled):
        calls.append('scope');yield
    monkeypatch.setattr(planner,'finalize',finalize)
    monkeypatch.setattr(selection,'_gold_driver',lambda:SimpleNamespace(scoped_ballast_v4=scope))
    with F.scoped_v4_finalize():
        blocked=planner.finalize(object(),result,time_limit_s=480.)
    assert blocked.status=='error' and blocked.metrics['second_finalize_refused']
    assert calls==[] and planner.finalize is finalize
