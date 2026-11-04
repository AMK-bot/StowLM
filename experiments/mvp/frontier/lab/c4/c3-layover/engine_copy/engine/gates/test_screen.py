# Copied from experiments/mvp/frontier/lab/c2/c2-gates/test_screen.py; engine integration changes are local.
"""Safety regressions for unsupported or indeterminate gate evaluations."""
from types import SimpleNamespace

from . import screen as gate
from stowage.model import PartReport, Violation


def violation(rule='example.rule',severity='hard',**kw):
    return Violation(rule_id=rule,severity=severity,call_index=0,message='probe',**kw)


def setup_valid(monkeypatch):
    ship=SimpleNamespace(placements=[SimpleNamespace(container_id='box')])
    instance=SimpleNamespace(load_list=['box'],call_index=0)
    replay=SimpleNamespace(legal=True,violations=[],final_state=SimpleNamespace(ship=ship))
    monkeypatch.setattr(gate,'apply_plan',lambda i,p: replay)
    monkeypatch.setattr(gate,'departure_condition',lambda i,p: ship)
    monkeypatch.setattr(gate,'_samples',lambda i,s: [('departure',i,s)])
    monkeypatch.setattr(gate,'active_stubs',lambda: {})
    monkeypatch.setattr(gate,'rule_freeze_v1_enabled',lambda: False)
    for name in ['check_static_rules','check_visibility','check_lashing','check_stability']:
        monkeypatch.setattr(gate,name,lambda *a,**k: PartReport(name='test'))
    return instance,replay


def test_no_pass_shortcut(monkeypatch):
    instance,_=setup_valid(monkeypatch)
    assert gate.screen(instance,object())=='unknown'


def test_hard_public_rule_rejects_and_tolerated_does_not(monkeypatch):
    instance,_=setup_valid(monkeypatch)
    monkeypatch.setattr(gate,'check_static_rules',lambda *a,**k: PartReport(
        name='rules',violations=[violation()]))
    assert gate.screen(instance,object())=='fail'
    monkeypatch.setattr(gate,'check_static_rules',lambda *a,**k: PartReport(
        name='rules',violations=[violation(severity='tolerated')]))
    assert gate.screen(instance,object())=='unknown'


def test_timeout_cannot_become_rejection(monkeypatch):
    instance,_=setup_valid(monkeypatch)
    monkeypatch.setattr(gate,'check_stability',lambda *a,**k: PartReport(
        name='stability',ran=False,error='budget',violations=[violation('checker.internal_error')]))
    assert gate.screen(instance,object())=='unknown'


def test_unexpected_error_and_stub_are_unknown(monkeypatch):
    instance,_=setup_valid(monkeypatch)
    def bad(*a,**k): raise RuntimeError('diagnostic')
    monkeypatch.setattr(gate,'check_visibility',bad)
    assert gate.screen(instance,object())=='unknown'
    monkeypatch.setattr(gate,'active_stubs',lambda: {'checker.check':'test'})
    assert gate.screen(instance,object())=='unknown'


def test_illegal_replay_and_shutout_fail(monkeypatch):
    instance,replay=setup_valid(monkeypatch)
    replay.legal=False
    assert gate.screen(instance,object())=='fail'
    replay.legal=True
    replay.final_state.ship.placements=[]
    assert gate.screen(instance,object())=='fail'


def test_ladder_success_requires_full_checker(monkeypatch):
    instance,_=setup_valid(monkeypatch)
    calls=[]
    monkeypatch.setattr(gate,'check',lambda *a: calls.append(True) or SimpleNamespace(passed=True,violations=[]))
    assert gate.ladder_verdict(instance,object())==(True,[],False)
    assert calls==[True]


def test_ladder_hard_noniscode_can_skip_irrelevant_strength(monkeypatch):
    instance,_=setup_valid(monkeypatch)
    monkeypatch.setattr(gate,'check_visibility',lambda *a,**k: PartReport(name='visibility',violations=[violation('solas.v22.visibility')]))
    def forbidden(*a): raise AssertionError('unneeded full checker')
    monkeypatch.setattr(gate,'check',forbidden)
    assert gate.ladder_verdict(instance,object())==(False,['solas.v22.visibility'],False)


def test_changed_source_requires_full_fallback(monkeypatch):
    instance,_=setup_valid(monkeypatch)
    monkeypatch.setattr(gate,'_learning_sources_supported',lambda: False)
    calls=[]
    monkeypatch.setattr(gate,'check',lambda *a: calls.append(True) or SimpleNamespace(passed=True,violations=[]))
    assert gate.ladder_verdict(instance,object())==(True,[],False)
    assert calls==[True]


def test_saturated_flags_skip_later_uninformative_parts(monkeypatch):
    instance,_=setup_valid(monkeypatch)
    monkeypatch.setattr(gate,'_samples',lambda i,s: [(p,i,s) for p in ['departure','mid_voyage','arrival']])
    calls={'visibility':0,'lashing':0,'stability':0}
    def part(name,rule,**kw):
        def evaluate(*a,**k):
            calls[name]+=1
            return PartReport(name=name,violations=[violation(rule,**kw)])
        return evaluate
    monkeypatch.setattr(gate,'check_visibility',part('visibility','solas.v22.visibility'))
    monkeypatch.setattr(gate,'check_lashing',part('lashing','iacs.member_irs.container_securing'))
    monkeypatch.setattr(gate,'check_stability',part('stability','iscode.loading.trim',measured=-3.))
    result=gate.ladder_evidence(instance,object())
    assert result.status=='fail' and result.complete_learning and not result.used_full_check
    assert result.bow_down
    assert calls==dict(visibility=1,lashing=1,stability=1)


def test_unsaturated_absence_requires_all_three_samples(monkeypatch):
    instance,_=setup_valid(monkeypatch)
    monkeypatch.setattr(gate,'_samples',lambda i,s: [(p,i,s) for p in ['departure','mid_voyage','arrival']])
    calls=[]
    def part(*a,**k):
        calls.append(True)
        return PartReport(name='lashing')
    monkeypatch.setattr(gate,'check_lashing',part)
    gate.diagnose(instance,object(),complete_learning=True)
    assert len(calls)==3


def test_bow_down_preserves_full_condition_message_prefix():
    leg=gate.VoyageSpec.model_construct(
        departure=SimpleNamespace(id='fwd-condition',call_index=0),
        arrival=SimpleNamespace(id='arrival',call_index=1))
    instance=SimpleNamespace(voyage=leg)
    values=gate._condition_violations(instance,'departure',[violation('iscode.loading.draft')])
    assert values[0].message=='[fwd-condition departure] probe'
    assert gate.Evidence(violations=values).bow_down
