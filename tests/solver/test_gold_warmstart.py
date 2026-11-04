"""Ordering search and admission contracts; full-physics verdicts are doubles."""
from types import SimpleNamespace
from time import monotonic

import pytest

from stowage.actions import get_action_space
from stowage.actions.space import Place, Relocate
from stowage.model import BallastCondition, CheckReport, PlannerResult, YardStack
from stowage.model.fixtures import build_instance
from stowage.moves import apply_plan
from stowage.solver import gold_warmstart as module
from stowage.solver import gold_warmstart_search as search_module


def two_box_case():
    instance = build_instance()
    boxes = [box.model_copy(update={'dg':None, 'special_stowage':[], 'oog':None,
                                   'reefer':False, 'pod':'THSGZ'})
             for box in instance.containers[1:3]]
    instance.containers = boxes
    instance.load_list = [box.id for box in boxes]
    instance.yard.stacks = [YardStack(key='A-001-01', length_ft=20, boxes=instance.load_list)]
    actions = [Place(boxes[0].id, '0010102'), Place(boxes[1].id, '0010202')]
    space = get_action_space('boxslot')
    ballast = BallastCondition(tank_masses_kg={'WB-P':51250.,'WB-S':51250.}, source='finisher')
    baseline = space.expand(instance, actions).model_copy(update={'ballast':ballast})
    candidate = space.expand(instance, list(reversed(actions))).model_copy(update={'ballast':ballast})
    return instance, baseline, candidate


def test_ready_set_ordering_removes_real_yard_rehandle_with_same_final_stow():
    instance, baseline, _ = two_box_case()
    before = apply_plan(instance, baseline)
    assert before.legal and before.count.relocations == 1
    proposals, diagnostics = search_module.search(instance, baseline,
        deadline=monotonic()+2., seed=4, max_neighborhoods=4)
    assert proposals
    after = apply_plan(instance, proposals[0].plan)
    assert after.legal and after.count.relocations == 0
    assert after.count.weighted_nonproductive < before.count.weighted_nonproductive
    assert {p.container_id:p.slot for p in after.final_state.ship.placements} == {
        p.container_id:p.slot for p in before.final_state.ship.placements}
    assert proposals[0].plan.ballast == baseline.ballast
    assert diagnostics['strict_candidates'] > 0


def test_precedence_closes_both_columns_and_hatch_before_deck():
    instance = build_instance()
    actions = [Place('a','0020102'), Place('b','0010104'), Place('c','0030104'),
               Place('d','0020182')]
    predecessors = search_module.precedence_sets(instance, actions)
    assert predecessors[1] == {0} and predecessors[2] == {0}
    assert predecessors[3] == {0,1,2}


def test_expired_or_unsupported_search_returns_no_unverified_proposals():
    instance, baseline, _ = two_box_case()
    proposals, metrics = search_module.search(instance, baseline, deadline=monotonic()-1.)
    assert not proposals and metrics['neighborhoods'] == 0
    instance.discharge_list = [instance.load_list[0]]
    proposals, metrics = search_module.search(instance, baseline, deadline=monotonic()+1.)
    assert not proposals and metrics['unsupported']


def admission_doubles(monkeypatch, *, baseline_mask=True, screen_passes=True):
    import stowage.checker
    import stowage.solver.securing_screen as securing
    instance, baseline, candidate = two_box_case()
    monkeypatch.setattr(stowage.checker, 'check', lambda *args: CheckReport(passed=True))
    monkeypatch.setattr(module, '_mask_replay', lambda *args, **kwargs:
                        dict(passed=baseline_mask, actions=2, cause=None))
    class Screen:
        def __init__(self, instance):
            pass
        def evaluate(self, placements):
            return SimpleNamespace(passed=screen_passes, reason=None)
    monkeypatch.setattr(securing, 'DeckSecuringScreen', Screen)
    result = PlannerResult(status='ok', plan=baseline, planner='baseline.twostep',
                           planner_version='test-double')
    qualified = module.qualify_baseline(instance, result)
    proposal = search_module.Proposal(candidate, -999., {'cut':0})  # untrusted surrogate
    monkeypatch.setattr(search_module, 'search', lambda *args, **kwargs: ([proposal],{}))
    return instance, qualified, candidate


@pytest.mark.parametrize('failure', ['equal', 'worse', 'checker', 'mask', 'screen', 'error', 'timeout', 'zero'])
def test_no_failed_or_non_strict_proposal_replaces_checked_baseline(monkeypatch, failure):
    import stowage.checker
    import stowage.solver.securing_screen as securing
    instance, baseline, candidate = admission_doubles(monkeypatch)
    if failure in ('equal','worse'):
        # The wrapper must recompute true weighted moves, not trust -999 above.
        proposed = baseline.plan
        if failure == 'worse':
            bottom, top = instance.load_list
            proposed = get_action_space('boxslot').expand(instance, [
                Relocate(top,'A-003-01'), Relocate(top,'A-004-01'),
                Place(bottom,'0010102'), Place(top,'0010202')]).model_copy(
                    update={'ballast':baseline.plan.ballast})
        proposal = search_module.Proposal(proposed, -999., {})
        monkeypatch.setattr(search_module, 'search', lambda *args, **kwargs: ([proposal],{}))
    elif failure == 'checker':
        monkeypatch.setattr(stowage.checker, 'check', lambda *args: CheckReport(passed=False))
    elif failure == 'mask':
        monkeypatch.setattr(module, '_mask_replay', lambda *args, **kwargs: dict(passed=False))
    elif failure == 'screen':
        monkeypatch.setattr(securing, 'DeckSecuringScreen', lambda *args: SimpleNamespace(
            evaluate=lambda *args:SimpleNamespace(passed=False)))
    elif failure == 'error':
        def broken(*args, **kwargs):
            raise ValueError('synthetic repair failure')
        monkeypatch.setattr(search_module, 'search', broken)
    elif failure == 'timeout':
        def expired(*args, **kwargs):
            monkeypatch.setattr(module, 'monotonic', lambda:monotonic()+2.)
            return [search_module.Proposal(candidate,-999.,{})], {}
        monkeypatch.setattr(search_module, 'search', expired)
    result = module.improve_checked(instance, baseline,
        time_limit_s=0. if failure == 'zero' else 1.,
        config=module.GoldWarmstartConfig(validation_reserve_s=0.))
    assert result.status == 'ok' and result.plan == baseline.plan
    assert not result.metrics['improved']
    assert result.metrics['objective_after'] == apply_plan(instance, baseline.plan).count.weighted_nonproductive
    assert module._bound_report(instance, result).passed


def test_only_strict_actual_weighted_improvement_with_full_witness_is_admitted(monkeypatch):
    instance, baseline, candidate = admission_doubles(monkeypatch)
    result = module.improve_checked(instance, baseline, time_limit_s=1.,
        config=module.GoldWarmstartConfig(validation_reserve_s=0.))
    assert result.plan == candidate and result.metrics['improved']
    assert result.metrics['objective_after'] == apply_plan(instance, candidate).count.weighted_nonproductive
    assert result.metrics['objective_after'] < result.metrics['objective_before']
    assert result.metrics['candidate_gates'][0]['full_checker_passed']


def test_zero_rehandle_weight_does_not_turn_yard_only_gain_into_objective_gain(monkeypatch):
    instance, baseline, _ = admission_doubles(monkeypatch)
    instance.objective.relocate = 0.
    # The modified instance invalidates bound evidence, so qualification reruns.
    result = module.improve_checked(instance, baseline, time_limit_s=1.,
        config=module.GoldWarmstartConfig(validation_reserve_s=0.))
    assert not result.metrics['improved'] and result.plan == baseline.plan


def test_full_checker_pass_cannot_override_failed_baseline_public_mask(monkeypatch):
    instance, baseline, _ = admission_doubles(monkeypatch, baseline_mask=False)
    assert baseline.status == 'error'
    result = module.improve_checked(instance, baseline, time_limit_s=0.)
    assert result.status == 'error'
    assert not result.metrics['checker_passed']
    assert module._bound_report(instance, result) is None


def test_design_envelope_failure_keeps_checked_fallback_but_skips_lns(monkeypatch):
    instance, baseline, _ = admission_doubles(monkeypatch, screen_passes=False)
    monkeypatch.setattr(search_module, 'search', lambda *args, **kwargs: pytest.fail('no fixed-stow screen escape'))
    result = module.improve_checked(instance, baseline, time_limit_s=1.)
    assert result.status == 'ok' and result.plan == baseline.plan
    assert result.metrics['baseline_securing_screen_passed'] is False
    assert result.metrics['skipped_lns_reason'] == 'baseline_outside_design_envelope'


def test_deserialized_or_mutated_plan_loses_private_baseline_evidence(monkeypatch):
    instance, baseline, _ = admission_doubles(monkeypatch)
    assert module._bound_report(instance, baseline) is not None
    restored = PlannerResult.model_validate_json(baseline.model_dump_json())
    assert module._bound_report(instance, restored) is None
    changed = baseline.model_copy(update={'plan':baseline.plan.model_copy(update={'id':'changed'})})
    assert module._bound_report(instance, changed) is None


@pytest.mark.parametrize('scope', ['later_plans', 'plan_calls'])
def test_later_call_scope_cannot_be_dropped_into_current_call_gold(monkeypatch, scope):
    instance, baseline, _ = admission_doubles(monkeypatch)
    if scope == 'later_plans':
        baseline = baseline.model_copy(update={'later_plans':[baseline.plan]})
    else:
        instance.plan_calls = [1]
    assert module._bound_report(instance, baseline) is None
    result = module.improve_checked(instance, baseline, time_limit_s=0.)
    assert result.status == 'error' and not result.shippable


def test_end_to_end_rejects_later_call_scope_before_planning(monkeypatch):
    import stowage.solver.twostep as twostep
    instance = build_instance()
    instance.plan_calls = [1]
    monkeypatch.setattr(twostep, 'TwoStepPlanner', lambda *args, **kwargs:pytest.fail('out-of-scope work'))
    result = module.solve_gold(instance)
    assert result.status == 'error' and result.plan is None


@pytest.mark.parametrize('value', [-1., float('nan'), float('inf')])
def test_bad_config_budget_rejected(value):
    with pytest.raises(ValueError):
        module.GoldWarmstartConfig(lns_time_limit_s=value)


def test_generic_planner_budget_can_restrict_but_not_inflate_lns(monkeypatch):
    seen = []
    monkeypatch.setattr(module, 'solve_gold', lambda *args, **kwargs:seen.append(kwargs))
    planner = module.GoldWarmstartPlanner(module.GoldWarmstartConfig(lns_time_limit_s=15.))
    planner.plan(None, time_limit_s=120.)
    planner.plan(None, time_limit_s=5.)
    assert [call['lns_time_limit_s'] for call in seen] == [15.,5.]
    assert all(call['baseline_time_limit_s'] == 120. for call in seen)
