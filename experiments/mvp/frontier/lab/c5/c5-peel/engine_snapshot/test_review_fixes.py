"""Reviewer counterexamples: preserve passes, exact orders, and arrival actions."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from stowage.actions.space import Place, Discharge, RestowOff
from . import planner, portfolio, finishing
from .config import Config
from .test_planner import _setup
from .scout import method


def test_constructor_callback_pass_survives_later_constructor_exception(monkeypatch):
    instance, config, calls = _setup(monkeypatch, bound=1., costs=[2], passed={2})
    def collector(instance, **kwargs):
        kwargs['finish'](instance, '2', label='twostep')
        raise RuntimeError('later constructor')
    monkeypatch.setattr(portfolio, 'collect_starts', collector)
    result, record = planner.plan_and_check(instance, budget_s=0., config=config)
    assert result is not None and record['passed'] and record['lifts'] == 2.
    assert 'RuntimeError: later constructor' in record['stages']['starts']['errors']


def test_search_exception_cannot_erase_checked_constructor_pass(monkeypatch):
    instance, config, calls = _setup(monkeypatch, bound=1., costs=[2], passed={2})
    def collector(instance, **kwargs):
        kwargs['finish'](instance, '2', label='twostep')
        scored = planner.Problem(instance).score((Place('box', '2'),))
        return portfolio.StartBank(planner.Problem(instance), [('twostep', scored)],
                                   None, None, {}, set(), {})
    monkeypatch.setattr(portfolio, 'collect_starts', collector)
    monkeypatch.setattr(portfolio, 'baseline_search', Mock(side_effect=RuntimeError('search')))
    result, record = planner.plan_and_check(instance, budget_s=0., config=replace(config, search=True))
    assert result is not None and record['passed'] and record['lifts'] == 2.
    assert record['stages']['search']['errors'] == ['RuntimeError: search']


def test_both_orders_of_one_stow_reach_full_finishing(monkeypatch):
    instance, config, _ = _setup(monkeypatch, bound=0., costs=[2], passed=set())
    instance.load_list = ['a', 'b']
    a, b = Place('a', '0010102'), Place('b', '0030102')
    first = SimpleNamespace(actions=(a,b), objective=4., plan='bad')
    second = SimpleNamespace(actions=(b,a), objective=4., plan='good')
    by_plan = {'bad': first, 'good': second}
    problem = SimpleNamespace(score=lambda actions: first if actions[0] == a else second)
    monkeypatch.setattr(planner, 'Problem', lambda instance: problem)
    monkeypatch.setattr(planner, 'get_action_space', lambda name: SimpleNamespace(
        from_plan=lambda instance, plan: by_plan[plan].actions))
    monkeypatch.setattr(portfolio, 'collect_starts', lambda *a, **k:
        portfolio.StartBank(problem, [('one',first),('two',second)],None,None,{},set(),{}))
    good = Mock(); good.plan='good'; good.model_copy.return_value=good
    finish = Mock(side_effect=[(None,{'passed':False}), (good,{'passed':True,'weighted':4.})])
    monkeypatch.setattr(finishing, 'finish_chain', finish)
    result, record = planner.plan_and_check(instance, budget_s=0., config=replace(config,bound=False))
    assert result is good and record['replay_legal_stows'] == 2
    assert [c.args[1] for c in finish.call_args_list] == ['bad','good']
    assert portfolio._key(first) != portfolio._key(second)


def test_arrival_archive_keeps_discharge_and_restow_prefix(monkeypatch):
    instance, config, _ = _setup(monkeypatch, bound=0., costs=[2], passed={2})
    instance.load_list=['box']; instance.discharge_list=['off']
    instance.onboard=SimpleNamespace(placements=[SimpleNamespace(container_id='stay'),
                                                SimpleNamespace(container_id='off')])
    actions=(Discharge('off'), RestowOff('stay'), Place('stay','0010102'),Place('box','0030102'))
    monkeypatch.setattr(planner,'apply_plan',lambda *a:SimpleNamespace(legal=True,
        final_state=SimpleNamespace(ship=SimpleNamespace(placements=[
            SimpleNamespace(container_id='stay'),SimpleNamespace(container_id='box')]))))
    scored=SimpleNamespace(actions=actions,objective=2.,plan='2')
    problem=SimpleNamespace(score=lambda actions:scored)
    monkeypatch.setattr(planner,'Problem',lambda instance:problem)
    monkeypatch.setattr(planner,'get_action_space',lambda name:SimpleNamespace(from_plan=lambda *a:actions))
    monkeypatch.setattr(portfolio,'collect_starts',lambda *a,**k:
        portfolio.StartBank(problem,[('arrival',scored)],None,None,{},set(),{}))
    result, record=planner.plan_and_check(instance,budget_s=0.,config=replace(config,
        bound=False,arrival_pages=False,arrival_bound=False,witness_start=False))
    assert result is not None and record['replay_legal_stows']==1
    assert planner.complete_actions(instance,actions)
    assert not planner.complete_actions(instance,actions[:2]+actions[3:])


def test_scoped_optimality_names_bound_and_future_projection(monkeypatch):
    instance, config, _ = _setup(monkeypatch,bound=2.,costs=[2],passed={2})
    result,record=planner.plan_and_check(instance,budget_s=1.,config=config)
    assert result is not None and record['proved_optimal']
    assert record['lower_bound_name'] in record['proof_scope']
    assert 'D15' in record['proof_scope'] and 'fixed stow' in record['proof_scope']
    assert 'no later loads' in record['proof_scope']


def test_scout_keeps_securing_failure_and_sole_viable_third_hopeful():
    def scored(box,cost):
        return SimpleNamespace(actions=(Place(box,'0010102'),),objective=cost)
    failed=[scored('securing',1),scored('bad-first',2),scored('bad-second',3),scored('viable-third',4)]
    candidates=method.retain_candidates([],failed)
    assert candidates==[s.actions for s in failed]
    # Identical placement set with different order also survives this seam.
    a,b=Place('a','0010102'),Place('b','0030102')
    assert len(method.retain_candidates([], [SimpleNamespace(actions=(a,b),objective=1),
                                            SimpleNamespace(actions=(b,a),objective=1)]))==2


def test_scout_rung_never_exceeds_remaining_wall_time():
    for remaining in (-1.,0.,.1,19.9,20.,119.9,121.):
        allowance=method.rung_allowance(100.+remaining,now=100.)
        assert allowance==pytest.approx(max(0.,min(120.,remaining)))


def test_certified_start_is_checked_before_constructor_and_scopes_early_stop(monkeypatch):
    instance, config, _ = _setup(monkeypatch,bound=2.,costs=[2],passed={2})
    from . import bounds
    from .bounds import certified
    monkeypatch.setattr(bounds,'map_bound',lambda *a,**k:(2.,dict(
        certificate_source='certified',certified={'relaxation_assignment':{'box':'2'}})))
    def repair(*args,**kwargs):
        return [(Place('box','2'),)]
    repair.last_stats={'candidates':1,'full_checker_pass_claim':False}
    monkeypatch.setattr(certified,'repair',repair)
    collect=Mock(side_effect=AssertionError('strict checked certificate stops before constructors'))
    monkeypatch.setattr(portfolio,'collect_starts',collect)
    result,record=planner.plan_and_check(instance,budget_s=1.,config=config)
    assert result is not None and record['proved_optimal']
    assert record['winner']=='certified-0' and 'certified' in record['proof_scope']
    assert collect.call_count==0


def test_scout_late_rung_exception_preserves_checked_first_stow(monkeypatch):
    first=SimpleNamespace(actions=(Place('box','0010102'),),objective=4.,plan='checked')
    ship=SimpleNamespace(placements=[SimpleNamespace(container_id='box',slot='0010102')])
    problem=SimpleNamespace(boxes={'box':SimpleNamespace(gross_mass_kg=1.)},
                            cells={'0010102':SimpleNamespace(lcg_m=1.)})
    model=SimpleNamespace(pages=[],boxes=[],T=SimpleNamespace(units={}),pod={})
    monkeypatch.setattr(method,'Model',lambda *a,**k:model)
    monkeypatch.setattr(method,'one_stow',Mock(side_effect=[(first,{}),RuntimeError('late rung')]))
    monkeypatch.setattr(method,'Fast',lambda *a:object())
    monkeypatch.setattr(method,'apply_plan',lambda *a:SimpleNamespace(final_state=SimpleNamespace(ship=ship)))
    monkeypatch.setattr(method,'verdict',lambda *a:(True,[],False))
    context={'problem':problem,'method_metadata':{}}
    instance=SimpleNamespace(onboard=SimpleNamespace(placements=[]))
    assert method.propose(instance,seed=0,budget_s=100.,context=context)==[first.actions]
    assert context['method_metadata']['errors']==['RuntimeError: late rung']
    assert '_scout_candidates' not in context
