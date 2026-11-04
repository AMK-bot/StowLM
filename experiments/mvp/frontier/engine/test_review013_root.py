"""0.13 integration boundaries: arrivals, full pass identity, static skips and tiers."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import ast
from pathlib import Path
import pytest

from . import planner, lab_method, peel
from .test_planner import _setup
from .layover import method as arrivals
from .finishing import chain
from stowage.actions.space import Place
from stowage.model.fixtures import build_instance


def arrival_setup(monkeypatch, *, late_error=False):
    instance, config, calls = _setup(monkeypatch, bound=0., costs=list(range(1,7)), passed={6})
    instance.onboard=NS(placements=[NS(container_id='stay')]);instance.discharge_list=[]
    class Plan(str):
        moves=()
    problem=planner.Problem(instance)
    original=problem.score
    def score(actions):
        row=original(actions)
        return NS(actions=row.actions, objective=row.objective, plan=Plan(row.plan))
    problem.score=score
    monkeypatch.setattr(planner,'Problem',lambda *a:problem)
    monkeypatch.setattr(arrivals,'Problem',lambda *a:problem)
    monkeypatch.setattr(arrivals.E9FIX,'install',lambda:None)
    monkeypatch.setattr(arrivals,'restow_options',lambda *a:[(f'branch{i}',(str(i),)) for i in range(5)])
    monkeypatch.setattr(arrivals,'direct_shift_options',lambda *a:[])
    monkeypatch.setattr(arrivals,'layouts',lambda *a:[])
    monkeypatch.setattr(arrivals,'prepare',lambda *a:(instance,(),NS(moves=[])))
    count=[]
    def construct(*a,**k):
        count.append(1)
        if late_error and len(count)==2:raise RuntimeError('late arrival model')
        return {'box':str(len(count))},{}
    monkeypatch.setattr(arrivals,'_construct',construct)
    monkeypatch.setattr(arrivals.pages,'Model',lambda *a,**k:NS())
    monkeypatch.setattr(arrivals,'order_with_prefixes',lambda problem,assignment,*a:(Place('box',assignment['box']),))
    monkeypatch.setattr(planner,'apply_plan',lambda *a:NS(legal=True,final_state=NS(ship=NS(
        placements=[NS(container_id='stay'),NS(container_id='box')]))))
    monkeypatch.setattr(planner.portfolio,'collect_starts',lambda *a,**k:None)
    return instance,replace(config,bound=False,witness_start=False,targeted_constructor=False,
                            assignment_lp=False,unary_certificate=False),calls


def test_sixth_arrival_candidate_reaches_finish(monkeypatch):
    instance,config,calls=arrival_setup(monkeypatch)
    result,row=planner.plan_and_check(instance,budget_s=100.,config=config)
    assert result is not None and row['lifts']==6.
    assert calls==['1','2','3','4','5','6']
    assert row['replay_legal_stows']==6


def test_arrival_publication_survives_later_exception(monkeypatch):
    instance,config,calls=arrival_setup(monkeypatch,late_error=True)
    result,row=planner.plan_and_check(instance,budget_s=100.,config=config)
    assert calls==['1'] and row['replay_legal_stows']==1
    assert 'late arrival model' in row['stages']['arrival_pages']['errors'][0]


def test_arrival_highs_threads_are_explicit():
    from .layover import page_base
    tree=ast.parse(Path(page_base.__file__).read_text())
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and getattr(n.func,'id','')=='linprog']
    assert calls
    for call in calls:
        options=next(k.value for k in call.keywords if k.arg=='options')
        assert ast.literal_eval(options)['threads']==1


def test_static_e6_failure_skips_every_ballast_lane(monkeypatch):
    from stowage.checker import rules
    monkeypatch.setattr(chain,'apply_plan',lambda *a:NS(legal=True,final_state=NS(ship=object())))
    monkeypatch.setattr(rules,'check_static_rules',lambda *a,**k:NS(violations=[NS(
        rule_id='expert.e6.heavy_under_deck',severity='hard')]))
    report=NS(passed=False,count=NS(weighted_nonproductive=63.))
    outcome=chain.fixed_stow_failure(object(),NS(call_index=0),report,map_lower_bound=65.)
    assert outcome['skip_ballast'] and outcome['below_map_bound']
    assert outcome['static_hard_violations']==['expert.e6.heavy_under_deck']
    assert 'not map infeasibility' in outcome['scope']


def test_soft_e6_does_not_skip_potential_first_pass(monkeypatch):
    from stowage.checker import rules
    monkeypatch.setattr(chain,'apply_plan',lambda *a:NS(legal=True,final_state=NS(ship=object())))
    monkeypatch.setattr(rules,'check_static_rules',lambda *a,**k:NS(violations=[NS(
        rule_id='expert.e6.heavy_under_deck',severity='soft')]))
    assert not chain.fixed_stow_failure(object(),NS(call_index=0),NS(passed=False),map_lower_bound=None)['skip_ballast']


def test_existing_full_pass_is_never_excluded_by_diagnostic_bound():
    row=chain.fixed_stow_failure(object(),object(),NS(passed=True),map_lower_bound=100.)
    assert not row['skip_ballast']


def test_proof_cutoff_gets_only_fresh_checked_upper(monkeypatch):
    from . import bounds
    instance,config,calls=_setup(monkeypatch,bound=1.,costs=[2],passed={2})
    seen=[]
    def bound(*a,**kw):
        seen.append(kw)
        return (kw.get('upper_bound',1.),{'certificate_source':'fresh-cutoff'})
    monkeypatch.setattr(bounds,'map_bound',bound)
    result,row=planner.plan_and_check(instance,budget_s=900.,config=replace(config,
        targeted_constructor=False,unary_certificate=False))
    assert result is not None and row['proved_optimal']
    assert seen[0].get('upper_bound') is None
    assert seen[-1]['upper_bound']==2.
    assert seen[-1]['work_limit']==config.proof_work_limit
    assert seen[-1]['budget_s']==config.proof_outer_s
    assert 'rule freeze off' in row['proof_scope'] and 'provisional' in row['proof_scope']


def test_lp_switch_identity_for_tiny_and_small(monkeypatch):
    from .assignment import method as lp
    from .assignment.routing import assignment_active
    runtime=Mock(side_effect=AssertionError('LP must stay inactive'))
    monkeypatch.setattr(lp,'_runtime',runtime)
    for size in ('tiny','small'):
        instance,config,calls=_setup(monkeypatch,bound=2.,costs=[2],passed={2})
        instance.size_class=size
        results=[]
        for active in (False,True):
            result,row=planner.plan_and_check(instance,budget_s=1.,config=replace(config,
                assignment_lp=active,targeted_constructor=False,unary_certificate=False))
            results.append((row['passed'],row['lifts'],row['winner'],row['proved_optimal'],row['archive']))
        assert results[0]==results[1]
        assert not assignment_active(instance)
    runtime.assert_not_called()


def test_peel_never_called_after_full_pass(monkeypatch):
    instance,config,_=_setup(monkeypatch,bound=2.,costs=[2],passed={2})
    seam=Mock(side_effect=AssertionError('cannot peel a held full pass'))
    monkeypatch.setattr(peel,'no_pass',seam)
    result,row=planner.plan_and_check(instance,budget_s=1.,config=replace(config,
        targeted_constructor=False,unary_certificate=False))
    assert result is not None and row['passed'];seam.assert_not_called()


def test_unknown_rule_t0_and_partial_not_completed():
    assert peel.rule_tier('new.unclassified.rule')=='T0'
    assert peel.rule_tier('expert.e8.away_from_heat')=='T3'
    from .completion import classify
    assert classify({'completed':None,'level':3,'boxes_left_ashore':['b']}) is None
    with pytest.raises(ValueError,match='never peel'):
        peel.no_pass(object(),{'checked_pass':object()})


def test_peel_reads_native_part_dictionary_and_exact_breaches(monkeypatch):
    from stowage.model import Violation
    instance=NS(load_list=['a'],onboard=NS(placements=[]),discharge_list=[],
                containers=[NS(id='a',length_ft=20,gross_mass_kg=10.)])
    monkeypatch.setattr(peel,'apply_plan',lambda *a:NS(legal=True,final_state=NS(ship=NS(
        placements=[NS(container_id='a')]))))
    violation=Violation(rule_id='iscode.loading.draft',severity='hard',call_index=0,
        measured=6.,limit=5.,unit='m',message='draught exceeds limit')
    report=NS(passed=False,violations=[violation],count=NS(weighted_nonproductive=2.),
        parts={'stability':NS(name='stability',ran=True,error=None,metrics={'draft':6.},margins={'draft':-1.})})
    row=peel.inspect(instance,object(),report)
    assert row['completed'] and row['level']==5 and not row['full_checker_passed']
    assert row['breaches'][0]['measured']==6. and row['breaches'][0]['limit']==5.
    assert row['exact_parts'][0]['name']=='stability'
    report.violations=[violation.model_copy(update={'rule_id':'unknown.new.rule'})]
    assert peel.inspect(instance,object(),report)['level'] is None


def test_assignment_context_pass_recovered_after_generator_error(monkeypatch):
    from .assignment import method as lp
    instance,config,calls=_setup(monkeypatch,bound=1.,costs=[2],passed={2})
    instance.load_list=['box']*61
    scored=planner.Problem(instance).score((Place('box','2'),))
    report=NS(passed=True,count=NS(weighted_nonproductive=2.))
    def iterator(instance,*,context,**kw):
        context['checked_passes']=[scored]
        context['assignment_reports']={lp.assignment_key(scored.actions):report}
        raise RuntimeError('late assignment wrapper error')
        yield
    monkeypatch.setattr(lp,'iter_candidates',iterator)
    result=Mock();result.plan='2';result.model_copy.return_value=result
    monkeypatch.setattr(planner,'PlannerResult',lambda **kw:result)
    found,row=planner.plan_and_check(instance,budget_s=1.,config=replace(config,
        starts=False,unary_certificate=False,warm_polish=False,post_pass_exchange=False))
    assert found is result and row['passed'] and row['winner_lane']=='assignment_lp'
    assert calls==[]
    assert 'late assignment wrapper error' in row['stages']['assignment_lp']['errors'][0]


def test_unary_certificate_prevents_every_search_lane(monkeypatch):
    from . import unary,bounds
    from .assignment import method as lp
    instance,config,_=_setup(monkeypatch,bound=1.,costs=[2],passed={2})
    monkeypatch.setattr(unary,'certify',lambda *a,**k:dict(status='certified_unary_infeasible',
        rule='expert.e8.away_from_heat',box='box'))
    proof=Mock(side_effect=AssertionError('search after unary certificate'))
    assignment=Mock(side_effect=AssertionError('LP after unary certificate'))
    monkeypatch.setattr(bounds,'map_bound',proof);monkeypatch.setattr(lp,'iter_candidates',assignment)
    result,row=planner.plan_and_check(instance,budget_s=900.,config=config)
    assert result is None and not row['passed'];proof.assert_not_called();assignment.assert_not_called()
    assert row['unary_no_legal_cell']['rule']=='expert.e8.away_from_heat'


def test_specific_bound_producer_is_not_replaced_by_umbrella_name():
    assert planner.bound_name(dict(certificate_source='certified',
        proof_details={'bound':'certified_plain_packing'}))=='certified_plain_packing'
    assert planner.bound_name(dict(certificate_source='hatch_restow',
        proof_details={'bound':'certified_plain_packing'}))=='hatch_restow'


def test_certified_failed_witness_defers_full_finish_until_baseline_pass(monkeypatch):
    from . import bounds,portfolio
    from .bounds import certified
    instance,config,calls=_setup(monkeypatch,bound=2.,costs=[1,2],passed={2})
    class Plan(str):
        def model_dump(self,**k):return {'test':str(self)}
    problem=planner.Problem(instance);old=problem.score
    def score(actions):
        row=old(actions)
        return NS(actions=row.actions,objective=row.objective,plan=Plan(row.plan))
    problem.score=score
    monkeypatch.setattr(planner,'check',lambda *a:NS(passed=False))
    monkeypatch.setattr(bounds,'map_bound',lambda *a,**k:(2.,dict(certificate_source='certified',certified={'witness':True})))
    def repair(*a,**k):return [(Place('box','1'),)]
    repair.last_stats={};monkeypatch.setattr(certified,'repair',repair)
    def collect(instance,**kw):
        kw['finish'](instance,Plan('2'),label='twostep')
        raise AssertionError('checked bound equality must stop')
    monkeypatch.setattr(portfolio,'collect_starts',collect)
    result,row=planner.plan_and_check(instance,budget_s=1.,config=replace(config,
        targeted_constructor=False,unary_certificate=False))
    assert result is not None and calls==['2'] and row['lifts']==2.
    assert row['stages']['candidate_triage']['deferred']==1


def test_safe_catalog_never_opens_forbidden_tree(monkeypatch):
    from .safe_catalog import install
    catalog=install()
    original=Path.read_text
    def read(path,*a,**k):
        assert '/valsplit/' not in str(path) and '/c6/c6-maps/' not in str(path)
        return original(path,*a,**k)
    monkeypatch.setattr(Path,'read_text',read)
    assert len(catalog.load_set('quick'))==21
    for path in ('experiments/mvp/valsplit/tiny_val48.txt',
                 'experiments/mvp/frontier/lab/c6/c6-maps/new.json'):
        with pytest.raises(ValueError):catalog.load_set(path)


def test_scorecard_failed_agrees_with_engine_complete_replay(monkeypatch):
    import score
    from stowage.model import MovePlan
    instance=NS(id='fixture',load_list=['box'])
    actions=(Place('box','bad'),);plan=NS(slot='bad')
    problem=NS(score=lambda a:NS(actions=actions,objective=1.,plan=plan))
    monkeypatch.setattr(lab_method,'plan_and_check',lambda *a,**k:(None,dict(
        complete_replay_legal=True,replay_candidate_plan={'fixture':'complete'},passed=False)))
    monkeypatch.setattr(MovePlan,'model_validate',lambda *a,**k:plan)
    monkeypatch.setattr(lab_method,'_actions',lambda *a:actions)
    monkeypatch.setattr(lab_method,'_key',lambda *a:[{'box':'box','slot':'bad'}])
    row=score.evaluate(instance,{},dict(seed=0,budget_s=1.,finalize_s=1.,tries=3),
        method=lab_method.propose,finish=lab_method.finish,problem=problem,
        checker=lambda *a:NS(passed=False,violations=[NS(rule_id='iscode.loading.draft')]))
    assert row['status']=='failed' and not row['passed'] and row['lifts'] is None
    assert row['method_metadata']['engine']['complete_replay_legal']
    assert row['tries'][0]['violations']==['iscode.loading.draft']


def test_fixed_static_failure_bypasses_lp_and_v4(monkeypatch):
    from stowage.model import MovePlan
    from stowage.checker import rules
    instance=build_instance()
    plan=MovePlan(id='static-skip',instance_id=instance.id,call_index=0,moves=[])
    monkeypatch.setattr(chain,'check',lambda *a:NS(passed=False,violations=[],count=NS(weighted_nonproductive=63.)))
    monkeypatch.setattr(chain,'apply_plan',lambda *a:NS(legal=True,final_state=NS(ship=object())))
    monkeypatch.setattr(rules,'check_static_rules',lambda *a,**k:NS(violations=[NS(
        rule_id='expert.e6.heavy_under_deck',severity='hard')]))
    no=Mock(side_effect=AssertionError('hopeless fixed-stow ballast work'))
    monkeypatch.setattr(chain,'solve_ballast',no)
    monkeypatch.setattr(chain,'finish_inherited',no)
    monkeypatch.setattr(chain.frozen_planner,'_finish',no)
    result,row=chain.finish_chain(instance,plan,securing=False,map_lower_bound=65.)
    assert result is None and row['finalize_status']=='excluded_fixed_stow';no.assert_not_called()


def test_target_first_model_candidate_retained_and_encoded(monkeypatch):
    import json
    from .bounds import constructor_target
    instance,config,calls=_setup(monkeypatch,bound=0.,costs=[1],passed={1})
    monkeypatch.setattr(constructor_target,'construct',lambda *a,**k:dict(actions=None,
        first_model_actions=(Place('box','1'),),family='restricted_target'))
    monkeypatch.setattr(planner.portfolio,'collect_starts',lambda *a,**k:None)
    old=planner.get_action_space('boxslot')
    monkeypatch.setattr(planner,'get_action_space',lambda *a:NS(from_plan=old.from_plan,
        encode=lambda action:dict(op='P',box=action.box,slot=action.slot)))
    result,row=planner.plan_and_check(instance,budget_s=1.,config=replace(config,
        bound=False,certified_start=False,unary_certificate=False,witness_start=False))
    assert result is not None and calls==['1']
    assert row['stages']['targeted_constructor']['first_model_actions_encoded'][0]['box']=='box'
    json.dumps(row)


def test_cleanup_exception_cannot_erase_a_checked_pass(monkeypatch):
    instance,config,_=_setup(monkeypatch,bound=2.,costs=[2],passed={2})
    monkeypatch.setattr(planner,'ExitStack',lambda:NS(close=lambda:(_ for _ in ()).throw(RuntimeError('late cleanup'))))
    result,row=planner.plan_and_check(instance,budget_s=1.,config=replace(config,
        cached_mask=False,unary_certificate=False,targeted_constructor=False))
    assert result is not None and row['passed']
    assert 'late cleanup' in row['pipeline_errors'][0]
