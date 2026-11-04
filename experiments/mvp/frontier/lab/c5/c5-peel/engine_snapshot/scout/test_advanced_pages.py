# Provenance: local integration tests for lab/c2/fx-scout and lab/c3/c2-layout.
"""Work budgets, mixed support geometry, cache restrictions and all-candidate retention."""
from collections import namedtuple
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from alns import Problem
from stowage.model.fixtures import build_instance
from . import advanced_pages as pages
from . import layout_work
from .mixed_pages import build_pages


def test_all_complete_candidates_preserve_third_failure_and_alternate_orders():
    Action = namedtuple('Action', 'box slot')
    def scored(lifts, *actions):
        return SimpleNamespace(objective=lifts, actions=actions)
    first = scored(1., Action('a', 's'), Action('b', 't'))
    other_order = scored(2., Action('b', 't'), Action('a', 's'))
    third = scored(3., Action('c', 'u'))
    fourth = scored(4., Action('d', 'v'))
    result = layout_work.retain_all([fourth, third, other_order, first, first])
    assert result == [first, other_order, third, fourth]


def test_work_limits_are_independent_of_wall_budget_and_have_outer_safety_only():
    assert layout_work.safety_deadline(.1, now=10.) == 3610.
    assert layout_work.safety_deadline(900., now=10.) == 3610.
    assert layout_work.safety_deadline(1000., now=10.) == 4010.
    config = SimpleNamespace(lp_rounds=9, polish_rounds=3, dive_nodes=7)
    assert layout_work.work_limits({'engine_config': config}) == pages.Work(lp_rounds=9, polish_rounds=3, dive_nodes=7)


def test_mixed_geometry_has_complete_paired_twenty_bases_on_deck_only():
    problem = Problem(build_instance())
    all_pages = build_pages(problem.engine.T, mixed=True)
    pure = build_pages(problem.engine.T, mixed=False)
    assert not any(page.kind == 'mixed' for page in pure)
    assert any(page.kind == 'mixed' for page in all_pages)
    T = problem.engine.T
    for page in all_pages:
        if page.kind != 'mixed':
            continue
        assert page.deck and page.base in (1, 2)
        assert len(page.slots) > 2*page.base
        assert all(T.slot_n[s] == 1 for s in page.slots[:2*page.base])
        assert all(T.slot_n[s] == 2 for s in page.slots[2*page.base:])
        for level in range(page.base):
            left, right = page.slots[2*level:2*level+2]
            assert T.slot_k[left, 0] == T.slot_k[right, 0] == level
            assert {int(T.slot_stacks[left, 0]), int(T.slot_stacks[right, 0])} == set(page.stacks)


def test_advanced_models_keep_static_cache_private_and_reservation_ablation():
    problem = Problem(build_instance())
    first = pages.MixedModel(problem, mixed=True, reserve=False)
    second = pages.MixedModel(problem, mixed=False, reserve=False)
    original = second.static.copy()
    first.static[:] = False
    assert np.array_equal(second.static, original)
    assert second.reserved == {}
    assert not any(page.kind == 'mixed' for page in second.pages)
    assert second.unary_cache_hit


def test_per_stack_roof_and_closed_cover_are_constructor_restrictions():
    problem = Problem(build_instance())
    base = pages.PureModel(problem)
    deck_page = next(page for page in base.pages if page.deck)
    roof = {j: float(base.T.stack_base_z[j])+2.7 for j in deck_page.stacks}
    capped = pages.PureModel(problem, roofs=roof)
    for page in capped.pages:
        if page.deck and set(page.stacks).intersection(deck_page.stacks):
            assert len(page.slots) <= 1
    cover = next(page.cover for page in base.pages if not page.deck)
    closed = pages.PureModel(problem, closed_hold=[cover])
    for s in range(len(closed.T.slot_ids)):
        if any(not closed.T.stack_deck[int(j)] and closed.T.stack_cover[int(j)] == cover
               for j in closed.T.slot_stacks[s] if j >= 0):
            assert not closed.static[:, s].any()


def test_counted_generate_clamps_rounds_and_enforces_fixed_dive_nodes():
    class Base:
        def generate(self, **kwargs):
            self.seen = kwargs
            return dict(uncovered=4.)
    class Model(pages.Counted, Base):
        pass
    model = Model()
    model.work = pages.Work(lp_rounds=5, polish_rounds=2, dive_nodes=1)
    model.safety_deadline = 100.
    model.boxes, model.stacks, model.use_root_master = [0], [0], False
    model._in_dive, model._dive_nodes = True, 0
    model.generate(active_boxes={0}, active_stacks={0}, deadline=200., max_rounds=99)
    assert model.seen['deadline'] == 100. and model.seen['max_rounds'] == 5
    with pytest.raises(pages.WorkExhausted):
        model.generate(active_boxes={0}, active_stacks={0}, deadline=200.)


def test_single_worker_cp_uses_count_limits_and_outer_deadline():
    solver = SimpleNamespace(parameters=SimpleNamespace())
    with patch.object(pages, 'monotonic', return_value=10.):
        pages.configure_solver(solver, pages.Work(cp_conflicts=17, cp_deterministic=.25), 110., 3)
    assert solver.parameters.num_workers == 1
    assert solver.parameters.max_time_in_seconds == 100.
    assert solver.parameters.max_number_of_conflicts == 17
    assert solver.parameters.max_deterministic_time == .25
    assert solver.parameters.random_seed == 3


def test_postfinish_polish_preserves_recipe_and_checked_incumbent_on_failure(monkeypatch):
    Action = namedtuple('Action', 'box slot')
    recipe = object()
    plan = SimpleNamespace(ballast=recipe)
    instance = SimpleNamespace(onboard=SimpleNamespace(placements=[]), discharge_list=[])
    problem = SimpleNamespace()
    context = dict(problem=problem, method_metadata={}, engine_config=SimpleNamespace(warm_polish=True))
    replay = SimpleNamespace(legal=True, count=SimpleNamespace(weighted_nonproductive=8.))
    monkeypatch.setattr(layout_work, 'apply_plan', lambda *args: replay)
    monkeypatch.setattr(layout_work, 'check', lambda *args: SimpleNamespace(passed=True, count=replay.count))
    monkeypatch.setattr(layout_work, 'get_action_space',
                        lambda *args: SimpleNamespace(from_plan=lambda *args: [Action('a', 's')]))
    def fail_after_input(problem, incumbent, *, context, **kwargs):
        assert incumbent.plan is plan
        assert context['_warm_ballast'] is recipe
        raise RuntimeError('late optional polish failure')
    monkeypatch.setattr(layout_work, 'improve_checked', fail_after_input)
    result = layout_work.polish_finished(instance, plan, seed=0, budget_s=1., context=context)
    assert len(result) == 1 and result[0].plan is plan
    assert result[0].objective == 8.
    assert 'late optional' in context['method_metadata']['warm_polish_after_finish']['error']


def test_postfinish_polish_rejects_unchecked_reference(monkeypatch):
    replay = SimpleNamespace(legal=True)
    monkeypatch.setattr(layout_work, 'apply_plan', lambda *args: replay)
    monkeypatch.setattr(layout_work, 'check', lambda *args: SimpleNamespace(passed=False))
    context = dict(problem=object(), method_metadata={})
    assert layout_work.polish_finished(object(), object(), seed=0, budget_s=1., context=context) == []


def test_postreplay_polish_exception_keeps_complete_candidate(monkeypatch):
    Action = namedtuple('Action', 'box slot')
    scored = SimpleNamespace(actions=(Action('a', 's'),), objective=7., plan=object())
    model = SimpleNamespace(
        boxes=[0], stacks=[0], pages=[SimpleNamespace(kind='20')], columns=[object()],
        work=pages.Work(), seed_chosen=[0], _valid_seed=lambda chosen: True,
        generate=lambda **kwargs: dict(uncovered=0., rounds=1),
        dive=lambda **kwargs: ([0], {}), balance=lambda *args, **kwargs: ([0], 0.),
        assignment=lambda chosen: {'a': 's'})
    def fail(*args, **kwargs):
        raise RuntimeError('optional securing polish')
    model.polish = fail
    problem = SimpleNamespace(instance=object())
    context = dict(engine_config=SimpleNamespace(warm_polish=True, securing_cuts=False))
    monkeypatch.setattr(layout_work, 'score_assignment', lambda *args, **kwargs: scored)
    monkeypatch.setattr(layout_work, 'apply_plan',
                        lambda *args: SimpleNamespace(final_state=SimpleNamespace(ship=object())))
    monkeypatch.setattr(layout_work, 'Fast', lambda *args: object())
    result, row = layout_work.construct(problem, model, seed=0, deadline=float('inf'), context=context)
    assert result == [scored]
    assert 'optional securing polish' in row['securing_polish_error']


def test_pure_anchor_initializes_reference_bow_target_before_first_design():
    settings = layout_work.initial_pure_settings(88, 55., bow_down=True)
    assert settings == dict(deck_tiers=4, lash_limit=.94, lcg_target=53.5, hold_reward=3.)
    assert layout_work.initial_pure_settings(162, 55., bow_down=False)['lcg_target'] is None
    assert layout_work.initial_pure_settings(162, 55., bow_down=False)['deck_tiers'] == 3


def test_pure_anchor_retries_learned_design_without_new_page_restrictions(monkeypatch):
    calls, warmed = [], []
    oracle = object()
    def pure_model(problem, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace()
    monkeypatch.setattr(layout_work, 'PureModel', pure_model)
    def forbidden(*args, **kwargs):
        raise AssertionError('mixed/reservation model displaced pure anchor')
    monkeypatch.setattr(layout_work, 'MixedModel', forbidden)
    monkeypatch.setattr(layout_work, 'warm_columns',
                        lambda model, previous, **kwargs: warmed.append(kwargs) or 0)
    candidate = SimpleNamespace(objective=71.)
    monkeypatch.setattr(layout_work, 'construct',
                        lambda *args, **kwargs: ([candidate], dict(primary_candidate_index=0)))
    passing = []
    def evaluate(scored, row):
        row['bow_down'] = False
        if len(calls) == 1:
            return False, ['solas.v22.visibility']
        passing.append(scored)
        return True, []
    rows, settings = layout_work.run_pure_anchor(
        object(), object(), [{'A': 1}], settings=layout_work.initial_pure_settings(88, 55., bow_down=True),
        lcg=55., oracle=oracle, context={}, work=pages.Work(), deadline=float('inf'),
        seed=0, rungs=2, evaluate=evaluate, passing=passing)
    assert len(calls) == 2 and rows[0]['retry_same_design']
    assert calls[0]['layout'] == calls[1]['layout'] == {'A': 1}
    assert calls[0]['layout_deck'] is calls[1]['layout_deck'] is True
    assert calls[0]['deck_tiers'] == 4 and calls[1]['deck_tiers'] == 3
    assert all(call['oracle'] is oracle and 'roofs' not in call and 'reserve' not in call for call in calls)
    assert all(row['limit'] == 4000 and row['deadline'] == float('inf') for row in warmed)
    assert settings['lcg_target'] == 53.5


def test_warm_oracle_observes_actual_declared_departure_ballast(monkeypatch):
    from alns import Scored
    from stowage.model import MovePlan, BallastCondition
    from stowage.moves import apply_plan
    from . import lns_lp

    instance = build_instance().model_copy(update={'load_list': []})
    recipe = BallastCondition(tank_masses_kg={'WB-P': 12345., 'WB-S': 23456.}, source='finisher')
    plan = MovePlan(id='warm-reference-tanks', instance_id=instance.id, call_index=0,
                    moves=[], ballast=recipe)
    raw = apply_plan(instance, plan)
    assert raw.legal and raw.final_state.ship.tank_masses_kg['WB-P'] != 12345.
    seen = []
    monkeypatch.setattr(layout_work, 'Fast',
                        lambda instance, ship: seen.append(dict(ship.tank_masses_kg)) or object())
    monkeypatch.setattr(layout_work, 'tier_profile', lambda *args: (1, 1, 1))
    monkeypatch.setattr(layout_work, 'PureModel',
                        lambda *args, **kwargs: SimpleNamespace(oracle=kwargs['oracle'], pages=[], columns=[]))
    monkeypatch.setattr(lns_lp, 'seed_assignment', lambda *args: [])
    layout_work.improve_checked(SimpleNamespace(instance=instance), Scored((), 0., plan),
        context={}, work=pages.Work(), deadline=float('inf'), seed=0, iterations=0,
        evaluate=lambda *args: (False, []), force_closure=False)
    assert seen[0]['WB-P'] == 12345. and seen[0]['WB-S'] == 23456.
    for tank in instance.vessel.tanks:
        if not tank.adjustable:
            assert seen[0][tank.id] == raw.final_state.ship.tank_masses_kg[tank.id]


def test_complete_seed_column_validity_is_separate_from_lcg_guidance():
    model = object.__new__(pages.PureModel)
    model.boxes, model.columns = [0], [SimpleNamespace(page=0, boxes=(0,))]
    model.pages, model.banned = [SimpleNamespace(stacks=(0,))], set()
    model.page_cuts = []
    model.legal = lambda *args: True
    model.lash_ok = lambda *args: True
    model.lcg_target = 0.
    model.lcg_moment = lambda column: 1.
    assert model._seed_columns_valid([0])
    assert not model._valid_seed([0])


@pytest.mark.parametrize('uncovered,rejected,expected_phases',
                         [(0., True, [1, 3]), (5., True, [1]), (5., False, [1])])
def test_complete_guidance_seed_survives_lp_screen_or_failed_dive_without_pool_mutation(
        monkeypatch, uncovered, rejected, expected_phases):
    Action = namedtuple('Action', 'box slot')
    scored = SimpleNamespace(actions=(Action('a', 's'),), objective=71., plan=object())
    class Model:
        def __init__(self):
            self.work = pages.Work()
            self.boxes, self.stacks = [0], [0]
            self.pages = [SimpleNamespace(kind='20')]
            self.columns, self.pool = [object()], {('original',): 0}
            self.seed_chosen = [0]
            self.phases, self.dives = [], 0
        def _valid_seed(self, chosen):
            return not rejected  # optional heuristic LCG target, not physical legality
        def _seed_columns_valid(self, chosen):
            return True
        def generate(self, **kwargs):
            self.phases.append(kwargs['phase'])
            return dict(uncovered=uncovered, rounds=1)
        def dive(self, **kwargs):
            self.dives += 1
            return None, dict(reason='fixed dive node limit')
        def balance(self, chosen, rng, **kwargs):
            self.pool[('moved',)] = len(self.columns)
            self.columns.append(object())
            return chosen, 0.
        def assignment(self, chosen):
            return {'a': 's'}
    model = Model()
    monkeypatch.setattr(layout_work, 'score_assignment', lambda *args, **kwargs: scored)
    result, row = layout_work.construct(object(), model, seed=0, deadline=float('inf'), context={})
    assert result == [scored] and row['seed_alternative_replay']
    assert row['seed_rejected_by_guidance'] is rejected
    assert model.phases == expected_phases
    assert model.dives == int(uncovered == 0.)
    assert len(model.columns) == 1 and model.pool == {('original',): 0}
