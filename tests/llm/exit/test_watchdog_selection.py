"""Staged D124 admission integration without importing or changing GPU code."""
from contextlib import nullcontext
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def staged(monkeypatch):
    root=Path(__file__).resolve().parents[3]
    directory=root/'experiments/mvp/exit/patches/d124'
    result={}
    for name in ('checker_watchdog','selection'):
        live=root/'src/llm/exit'/f'{name}.py'
        source=live if (root/'src/llm/exit/outcomes.py').exists() else directory/f'{name}.py'
        spec=importlib.util.spec_from_file_location(f'_d124_integration_{name}',source)
        module=importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules,spec.name,module);spec.loader.exec_module(module)
        monkeypatch.setitem(sys.modules,f'llm.exit.{name}',module)
        result[name]=module
    module=result['selection']
    monkeypatch.setattr(module,'_gold_driver',lambda:SimpleNamespace(scoped_ballast_v4=lambda _:nullcontext()))
    import stowage.planner
    monkeypatch.setattr(stowage.planner,'finalize',lambda i,r,**kw:r)
    monkeypatch.setattr(module,'mask_replay',lambda *a:pytest.fail('unavailable/failed check reached mask admission'))
    from stowage.model import MovePlan
    from stowage.model.fixtures import build_instance
    instance=build_instance();plan=MovePlan(id='d124.plan',instance_id=instance.id,call_index=instance.call_index)
    return result,instance,module.Candidate(plan,'model','sample')


@pytest.mark.parametrize('reason',('watchdog_expired','subprocess_crash','checker_internal_error','report_missing'))
def test_unavailable_full_check_never_supplies_winner_or_report(staged,monkeypatch,reason):
    modules,instance,candidate=staged;watchdog=modules['checker_watchdog'];seen=[]
    def check(i,p,**kw):
        seen.append(kw['timeout_s'])
        return watchdog.CheckOutcome(False,reason=reason,wall_s=.1,pid=123,exitcode=-9)
    monkeypatch.setattr(watchdog,'run_full_check',check)
    result=modules['selection'].verify_candidate(instance,candidate)
    assert not result.passed and result.plan is None and result.weighted_moves is None
    assert result.details['checker_available'] is False and result.details['unavailable'] is True
    assert 'report' not in result.details and 'violations' not in result.details
    assert result.details['checker_watchdog']['expired']==(reason=='watchdog_expired')
    assert seen==[300.]


def test_checker_launch_failure_is_unavailable_not_cargo_failure(staged,monkeypatch):
    modules,instance,candidate=staged
    def launch(*a,**kw):raise OSError('fixture process creation failed')
    monkeypatch.setattr(modules['checker_watchdog'],'run_full_check',launch)
    result=modules['selection'].verify_candidate(instance,candidate)
    assert not result.passed and result.details['unavailable'] is True
    assert result.details['unavailable_reason']=='checker_process_or_transport_error'
    assert 'report' not in result.details


def test_completed_physical_failure_keeps_report_and_availability(staged,monkeypatch):
    modules,instance,candidate=staged
    from stowage.model import CheckReport,PartReport,Violation
    report=CheckReport(passed=False,parts={name:PartReport(name=name,ran=True) for name in
        ('moves','rules','voyage','stability','strength','visibility','lashing','constraints')},
        violations=[Violation(rule_id='objective.all_loaded',severity='hard',call_index=0,message='not loaded')])
    monkeypatch.setattr(modules['checker_watchdog'],'run_full_check',lambda *a,**kw:
        modules['checker_watchdog'].CheckOutcome(True,report=report,wall_s=.1))
    result=modules['selection'].verify_candidate(instance,candidate)
    assert not result.passed and result.details['checker_available'] is True
    assert result.details['report']['violations'][0]['rule_id']=='objective.all_loaded'
    assert not result.details.get('unavailable',False)
