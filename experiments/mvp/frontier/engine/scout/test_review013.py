"""Exception recovery and capacity/thread boundaries without map solving."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from stowage.actions.space import Place

from . import candidates, capacity, dense, highs, layout, layout011, layout_work
from ..test_planner import _setup
from .. import planner


def test_legacy_checked_pass_survives_later_model_exception_through_public_planner(monkeypatch):
    instance, config, calls = _setup(monkeypatch, bound=1., costs=[2], passed={2})
    instance.load_list = ['box']*61
    instance.onboard = NS(placements=[])
    problem = planner.Problem(instance)
    problem.boxes = {'box': NS(gross_mass_kg=1.)}
    problem.cells = {'0010102': NS(lcg_m=10.)}
    first = problem.score((Place('box', '2'),))
    models = []
    def factory(*args, **kwargs):
        models.append(kwargs)
        if len(models) > 1:
            raise RuntimeError('later optional model construction')
        return NS()
    monkeypatch.setattr(layout011, 'model_factory', factory)
    monkeypatch.setattr(layout011, 'one_stow', lambda *a, **k: (first, {'replay_legal': True}))
    monkeypatch.setattr(layout011, 'verdict', lambda *a, **k: (True, [], False))
    monkeypatch.setattr(layout011, 'apply_plan', lambda *a: NS(final_state=NS(ship=NS(
        placements=[NS(container_id='box', slot='0010102')]))))
    monkeypatch.setattr(layout011, 'Fast', lambda *a: object())
    monkeypatch.setattr(layout011, 'counting_lower_bound', lambda *a: NS(relocations=0))
    monkeypatch.setattr(planner, 'check', lambda *a: NS(passed=True, count=NS(weighted_nonproductive=2.)))
    def result_factory(**kwargs):
        result = Mock(**kwargs)
        result.model_copy.return_value = result
        return result
    monkeypatch.setattr(planner, 'PlannerResult', result_factory)
    config = replace(config, work_count_search=False, capacity_deck_cap=False,
                     diagnostic_screen=False, ballast_lp=False, warm_polish=False)
    result, record = planner.plan_and_check(instance, budget_s=1., config=config)
    assert len(models) == 2
    assert result is not None and record['passed'] and record['lifts'] == 2.
    assert calls == []  # already checked: no ballast refinish is needed


def test_public_delegate_recovers_caller_owned_concrete_plan_and_recipe(monkeypatch):
    recipe = object()
    scored = NS(actions=(Place('box', 'slot'),), objective=1., plan=NS(ballast=recipe))
    context = {'engine_config': NS(work_count_search=False), 'method_metadata': {}}
    published = []
    context['publish_candidate'] = lambda candidate, **kw: published.append((candidate, kw['checked']))
    def optional_error(*a, **kw):
        candidates.publish(context, scored)
        candidates.publish(context, scored, checked=True)
        raise RuntimeError('later LP failure')
    monkeypatch.setattr(layout011, 'propose', optional_error)
    assert layout.propose(object(), seed=0, budget_s=1., context=context) == [scored.actions]
    assert context['_engine_checked_page_candidates'][0].plan.ballast is recipe
    assert published == [(scored, False), (scored, True)]


def test_publication_early_stop_belongs_to_caller():
    class Stop(Exception):
        pass
    context = {'publish_candidate': lambda *a, **k: (_ for _ in ()).throw(Stop())}
    with pytest.raises(Stop) as stopped:
        candidates.publish(context, object())
    with pytest.raises(Stop):
        candidates.recover(context, stopped.value)


def synthetic_capacity():
    return NS(boxes=[0], T=NS(units=[193]), closed_hold=set(), pages=[
        NS(kind='20', slots=tuple(range(60)), deck=False, cover='hold'),
        *[NS(kind='20', slots=tuple(range(5)), deck=True, cover='hold') for _ in range(36)]])


def test_capacity_floor_replaces_121_box_three_tier_shortage():
    model = synthetic_capacity()
    assert capacity.family_capacity(model, deck_cap=3) == 168
    assert capacity.minimum_deck_cap(model) == 4
    settings = layout_work.initial_pure_settings(121, 50., bow_down=False, minimum_cap=4)
    assert settings['deck_tiers'] == 4
    learned = layout_work.learn_pure(settings, ['solas.v22.visibility'], False, 50., minimum_cap=4)
    assert learned['deck_tiers'] == 4


def test_closure_screen_removes_hold_capacity_but_keeps_deck():
    model = synthetic_capacity()
    assert layout_work.page_capacity_ok(model)
    model.closed_hold = {'hold'}
    assert capacity.family_capacity(model) == 180
    assert not layout_work.page_capacity_ok(model)
    assert capacity.minimum_deck_cap(model) == 5


def test_highs_forwards_one_thread_and_preserves_other_options(monkeypatch):
    seen = {}
    monkeypatch.setattr(highs, '_linprog', lambda *a, **kw: seen.update(kw) or 'done')
    assert highs.linprog([1.], options={'threads': 8, 'time_limit': 2.}) == 'done'
    assert seen['options'] == {'threads': 1, 'time_limit': 2.}


def test_all_copied_lp_routes_use_explicit_thread_wrapper():
    from . import pages, mixed_pages
    assert pages.linprog is highs.linprog
    assert mixed_pages.linprog is highs.linprog


def test_dense_ablation_strips_moment_targets_and_uses_only_local_checked_seed(monkeypatch):
    seen = {}
    factory = lambda problem, **kw: seen.update(kw) or NS()
    context = dict(engine_config=NS(moment_pricing=False), dense_model_factory=factory)
    dense.make_model(object(), context, work=object(), deadline=100.,
        settings=dict(deck_tiers=(3, 5, 5), lcg_min=40., cargo_vmoment_max=100., vertical_weight=.1))
    assert seen['deck_tiers'] == (3, 5, 5)
    assert all(k not in seen for k in ('lcg_min', 'cargo_vmoment_max', 'vertical_weight'))
    assert 'oracle' not in seen
    assert dense.checked_seed(object(), None) is None


def test_checked_lp_seed_only_reaches_c4_after_unchanged_old_anchor_work(monkeypatch):
    instance = NS(id='same-map', call_index=0, load_list=['a'], discharge_list=[], onboard=NS(placements=[]))
    problem = NS(instance=instance)
    recipe = object()
    first = NS(actions=(Place('a', 'first'),), objective=10.,
               plan=NS(id='first', instance_id=instance.id, call_index=0))
    provided = NS(actions=(Place('a', 'lp'),), objective=3.,
                  plan=NS(id='lp', instance_id=instance.id, call_index=0, ballast=recipe))
    report = NS(passed=True, count=NS(weighted_nonproductive=3.), shut_out=[],
                parts={'native': NS(ran=True)}, provenance=NS(created_by='checker.check'))
    model = NS(boxes=[0], T=NS(units=[1]), pages=[NS(kind='20', deck=False, cover='A', slots=(0,))])
    monkeypatch.setattr(layout_work, 'make_model', lambda *a, **kw: model)
    monkeypatch.setattr(layout_work, 'PureModel', lambda *a, **kw: model)
    monkeypatch.setattr(layout_work, 'construct', lambda *a, **kw: ([first], dict(primary_candidate_index=0)))
    monkeypatch.setattr(layout_work, 'verdict', lambda *a, **kw: (False, ['test.failure'], False))
    monkeypatch.setattr(layout_work, 'reference_lcg', lambda *a: 50.)
    monkeypatch.setattr(layout_work, 'Fast', lambda *a: object())
    monkeypatch.setattr(layout_work, 'enumerate_layouts', lambda *a, **kw: [{'A': 1}])
    replayed = []
    def replay(instance, plan):
        replayed.append(plan.id)
        return NS(legal=True, count=NS(weighted_nonproductive=3. if plan.id == 'lp' else 10.),
                  final_state=NS(ship=NS(placements=[NS(container_id='a')])) )
    monkeypatch.setattr(layout_work, 'apply_plan', replay)
    monkeypatch.setattr(layout_work, 'report_matches_inputs', lambda *a: True)
    monkeypatch.setattr(layout_work, 'get_action_space', lambda *a: NS(from_plan=lambda *a: provided.actions))
    anchor_calls, dense_calls, local_calls = [], [], []
    def anchor(problem, first_model, layouts, **kw):
        anchor_calls.append((dict(kw['settings']), list(kw['passing']), kw['rungs'], kw['seed']))
        return [], kw['settings']
    monkeypatch.setattr(layout_work, 'run_pure_anchor', anchor)
    def reconstruct(problem, **kw):
        dense_calls.append(kw['incumbent'])
        return [], kw['incumbent']
    monkeypatch.setattr(dense, 'reconstruct', reconstruct)
    monkeypatch.setattr(dense, 'improve_checked', lambda problem, incumbent, **kw: local_calls.append(incumbent) or [])
    config = NS(**{name: False for name in layout_work.FEATURES})
    config.dense_layout = config.dense_fixed_charge_lns = True
    ordinary = dict(problem=problem, engine_config=config, method_metadata={})
    seeded = dict(problem=problem, engine_config=config, method_metadata={},
                  checked_incumbent=provided, checked_incumbent_report=report)
    assert layout_work.propose(instance, seed=7, budget_s=1., context=ordinary) == [first.actions]
    assert layout_work.propose(instance, seed=7, budget_s=1., context=seeded) == [first.actions]
    assert anchor_calls[0] == anchor_calls[1]
    assert anchor_calls[1][1] == []  # LP pass never enters old families' passing list
    assert dense_calls[0] is None and dense_calls[1].plan is provided.plan
    assert dense_calls[1].plan.ballast is recipe
    assert local_calls[0].plan is provided.plan
    assert replayed.count('lp') == 1
    assert seeded['method_metadata']['fully_checked_stows'] == 0
    assert seeded['method_metadata']['dense_checked_seed']['accepted']


def test_checked_context_seed_rejects_wrong_report_binding_before_replay(monkeypatch):
    instance = NS(id='map', call_index=0)
    plan = NS(instance_id='map', call_index=0)
    report = NS(passed=True, count=object(), shut_out=[], parts={'native': NS(ran=True)},
                provenance=NS(created_by='checker.check'))
    context = dict(checked_incumbent=NS(plan=plan), checked_incumbent_report=report)
    monkeypatch.setattr(layout_work, 'report_matches_inputs', lambda *a: False)
    monkeypatch.setattr(layout_work, 'apply_plan', lambda *a: pytest.fail('wrong report must not seed replay'))
    assert layout_work.checked_context_incumbent(NS(instance=instance), context) is None
    assert not context['method_metadata']['dense_checked_seed']['accepted']
