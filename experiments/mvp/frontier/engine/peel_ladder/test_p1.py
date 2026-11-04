"""P1: fresh native passes stop the ladder and survive later optional failures."""
from contextlib import contextmanager
from dataclasses import fields, replace
from types import SimpleNamespace as NS
import importlib

import pytest

from . import run
from .loader import isolated
from engine import peel, planner
from engine.config import Config
from stowage.model import BallastCondition, CheckReport, MoveCount, MovePlan


@pytest.mark.parametrize('partial_would_fail',[False,True])
def test_native_pass_at_l3_stops_before_separate_partial_search(monkeypatch,synthetic,partial_would_fail):
    import engine.peel_ladder.loader as loader
    s=synthetic
    full=s.make('fresh-native-full-pass',s.instance.load_list)
    visited=[];published=[]
    @contextmanager
    def configured():
        with isolated() as method:
            private_finishing=importlib.import_module('finishing')
            monkeypatch.setattr(method,'check',lambda i,p:s.reports[p.id])
            monkeypatch.setattr(private_finishing,'check',lambda i,p:s.reports[p.id])
            monkeypatch.setattr(method,'_reference_from_checked',lambda *a:None)
            monkeypatch.setattr(method,'finish_candidate',lambda *a,**k:pytest.fail('finishing after full pass'))
            def generate(instance,*,level,partial=False,**kwargs):
                visited.append((level,partial))
                if level==3 and partial and partial_would_fail:
                    raise RuntimeError('optional L3 partial generation failed')
                return ([full] if level==3 and not partial else []),{}
            monkeypatch.setattr(method,'generate',generate)
            yield method
    monkeypatch.setattr(loader,'isolated',configured)
    found={'plans':[],'publish_checked_pass':lambda plan,report,**k:published.append((plan,report))}
    row=run(s.instance,found)
    assert visited==[(1,False),(2,False),(3,False)]
    assert published==[(full,s.reports[full.id])]
    assert found['checked_pass'] is full and found['checked_report'] is s.reports[full.id]
    assert row['passed'] and not row['relaxed'] and row['level']==0
    assert row['completed']['level']==0 and row['completed']['full_checker_passed']
    assert row['plan']==full.model_dump(mode='json')


def test_publication_and_raw_plan_survive_callback_and_serialization_errors(monkeypatch,synthetic):
    import engine.peel_ladder.loader as loader
    s=synthetic;full=s.make('held-native-full-pass',s.instance.load_list)
    @contextmanager
    def configured():
        with isolated() as method:
            monkeypatch.setattr(method,'check',lambda i,p:s.reports[p.id])
            monkeypatch.setattr(method,'generate',lambda *a,**k:([full],{}))
            monkeypatch.setattr(method,'_reference_from_checked',lambda *a:None)
            yield method
    monkeypatch.setattr(loader,'isolated',configured)
    calls=[]
    def publish(plan,report,**kwargs):
        calls.append((plan,report))
        raise RuntimeError('optional callback telemetry failed')
    def broken_dump(*args,**kwargs):
        raise RuntimeError('optional plan serialization failed')
    monkeypatch.setattr(MovePlan,'model_dump',broken_dump)
    found={'plans':[],'publish_checked_pass':publish}
    row=run(s.instance,found)
    assert calls==[(full,s.reports[full.id])]
    assert found['checked_pass'] is full and found['checked_report'].passed
    assert row['passed'] and not row['relaxed'] and row['completed']['level']==0
    assert 'optional plan serialization failed' in row['serialization_error']
    assert 'optional callback telemetry failed' in found['peel_errors'][0]


def test_no_pass_recovers_new_caller_owned_full_pass_after_run_exception(monkeypatch,synthetic):
    import engine.peel_ladder as package
    s=synthetic;full=s.make('late-error-native-pass',s.instance.load_list)
    monkeypatch.setattr(peel,'_classify',lambda *a,**k:dict(completed=None))
    def late(instance,found,**kwargs):
        found.update(checked_pass=full,checked_report=s.reports[full.id],checked_source='native-probe')
        raise RuntimeError('optional record conversion failed')
    monkeypatch.setattr(package,'run',late)
    found={}
    row=peel.no_pass(s.instance,found)
    assert row['passed'] and not row['relaxed'] and row['completed']['level']==0
    assert row['plan']==full.model_dump(mode='json')
    assert 'optional record conversion failed' in row['post_pass_error']


@pytest.mark.parametrize('mode',['success','callback_then_error','raw_holder_then_error'])
def test_public_planner_holds_new_ladder_native_pass(monkeypatch,mode):
    instance=NS(id='public-peel-pass',load_list=['box'],size_class='medium')
    plan=MovePlan(id='fresh-recipe',instance_id=instance.id,call_index=0,
                  ballast=BallastCondition(tank_masses_kg={'TEST-P':123.},source='finisher'))
    report=CheckReport(passed=True,count=MoveCount(weighted_nonproductive=7.))
    monkeypatch.setattr(planner,'Problem',lambda *a:NS())
    monkeypatch.setattr(planner,'check',lambda i,p:report)
    config=Config()
    off={f.name:False for f in fields(config) if isinstance(getattr(config,f.name),bool)}
    config=replace(config,**{**off,'peel_seam':True,'peel_ladder':True})
    def no_pass(instance,found,**kwargs):
        if mode=='raw_holder_then_error':
            found.update(checked_pass=plan,checked_report=report,checked_source='raw-native-probe')
            raise RuntimeError('optional work after raw full pass')
        found['publish_checked_pass'](plan,report,source='native-ladder-probe')
        if mode=='callback_then_error':
            raise RuntimeError('optional work after published full pass')
        return dict(passed=True,relaxed=False,completed={'level':0,'full_checker_passed':True},
                    level=0,rules_broken=[],best_safe_partial=None,relaxed_plan=plan.model_dump(mode='json'))
    monkeypatch.setattr(peel,'no_pass',no_pass)
    result,row=planner.plan_and_check(instance,budget_s=0.,config=config)
    assert result is not None and row['passed'] and row['lifts']==7.
    assert result.plan.model_dump(mode='json')==plan.model_dump(mode='json')
    assert result.plan.ballast.tank_masses_kg=={'TEST-P':123.}
    assert row['completed']['level']==0 and row['rules_broken']==[]
    assert row['stages']['peel_ladder']['ran'] is True
