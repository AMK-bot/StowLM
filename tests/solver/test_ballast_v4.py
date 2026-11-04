"""Isolated acceptance and probe tests; doubles make no physical pass claim."""
from types import SimpleNamespace
import time

import numpy as np
import pytest

from stowage.model import BallastCondition, CheckReport, MovePlan, PartReport, Violation
from stowage.model.fixtures import build_instance
from stowage.solver.ballast_search import Found
from stowage.solver import ballast_v4, ballast_v4_evaluator as module


def setup(monkeypatch, *, cheap_failure=None, physics_failure=None, full_pass=False):
    instance = build_instance()
    instance.vessel = instance.vessel.model_copy(update={'hull': SimpleNamespace(max_list_rad=.1, min_gm_m=.15)})
    plan = MovePlan(id='isolated-v4', instance_id=instance.id, call_index=0)
    phases = ('departure', 'mid_voyage', 'arrival')
    monkeypatch.setattr(module, 'conditions', lambda inst, ship: [(p, inst, ship) for p in phases])
    calls = dict(cheap=[], physics=[], full=[])
    created = []

    class Context:
        def __init__(self, inst, ship, deadline):
            self.phase = phases[len(created)]
            created.append(self)
            self.tanks = sorted((t for t in inst.vessel.tanks if t.adjustable), key=lambda t:t.id)

        def margins(self, fill, active_keys=None):
            calls['cheap'].append((self.phase, active_keys))
            return {'stability/gm_m': -1. if self.phase == cheap_failure else 1.}

    monkeypatch.setattr(module, 'ExactContext', Context)
    counter = dict.fromkeys(('stability', 'strength', 'visibility', 'lashing'), 0)

    def checker(name):
        def check(inst, ship, *, call_index):
            phase = phases[counter[name] % 3]
            counter[name] += 1
            calls['physics'].append((phase, name))
            unavailable = (phase, name) == physics_failure
            return PartReport(name=name, ran=not unavailable,
                              error='synthetic unavailable' if unavailable else None,
                              margins={'test_clearance': 1.})
        return check

    monkeypatch.setattr(module, 'checkers', lambda: {n:checker(n) for n in counter})
    def full(inst, candidate):
        assert candidate.ballast is not None
        calls['full'].append(candidate)
        return CheckReport(passed=full_pass, parts={'rules':PartReport(name='rules')},
            violations=[] if full_pass else [Violation(rule_id='synthetic.full_gate', severity='hard',
                                                       call_index=0, message='independent veto')])
    import stowage.checker
    monkeypatch.setattr(stowage.checker, 'check', full)
    diagnostics = dict(evaluations=0, cache_hits=0, cheap_probe_calls=0, cheap_probe_s=0.,
                       cheap_rejections=0, physics_part_calls=0, physics_part_s=0.,
                       physics_rejections=0, full_checker_calls=0, full_checker_s=0.)
    evaluator = module.Evaluator(instance, plan, instance.onboard, time.monotonic()+5., diagnostics)
    return instance, plan, evaluator, calls


@pytest.mark.parametrize('phase', ['departure', 'mid_voyage', 'arrival'])
def test_each_d57_cheap_failure_prevents_expensive_checks(monkeypatch, phase):
    _, _, evaluator, calls = setup(monkeypatch, cheap_failure=phase)
    margins = evaluator.evaluate(evaluator.initial)
    assert margins[module.phase_prefix(phase)+'stability/gm_m'] < 0.
    assert {p for p,_ in calls['cheap']} == {'departure','mid_voyage','arrival'}
    assert not calls['physics'] and not calls['full']


def test_twelve_passing_parts_cannot_override_full_checker_rejection(monkeypatch):
    _, _, evaluator, calls = setup(monkeypatch)
    margins = evaluator.evaluate(evaluator.initial)
    assert margins['full_checker/hard_violation'] < 0.
    assert len(calls['physics']) == 12
    assert len(calls['full']) == 1
    assert evaluator.accepted is None
    assert evaluator.evaluate(evaluator.initial) == margins
    assert len(calls['full']) == 1  # rejected identical recipe is cached


def test_full_checker_witness_is_required_for_found(monkeypatch):
    _, _, evaluator, calls = setup(monkeypatch, full_pass=True)
    with pytest.raises(Found):
        evaluator.evaluate(evaluator.initial)
    assert len(calls['physics']) == 12
    assert len(calls['full']) == 1
    assert evaluator.accepted_report.passed
    assert evaluator.accepted.ballast.source == 'finisher'


def test_unavailable_midpoint_part_never_reaches_full_acceptance(monkeypatch):
    _, _, evaluator, calls = setup(monkeypatch, physics_failure=('mid_voyage','visibility'), full_pass=True)
    margins = evaluator.evaluate(evaluator.initial)
    assert margins['mid_voyage/visibility/unavailable'] < 0.
    assert not calls['full']
    assert len(calls['physics']) == 7  # stop at first completed failure


def test_selected_cheap_slopes_skip_unrelated_phases_and_parts(monkeypatch):
    _, _, evaluator, calls = setup(monkeypatch)
    key = 'arrival/stability/gm_m'
    assert evaluator.evaluate(evaluator.initial, active_keys=(key,)) == {key:1.}
    assert calls['cheap'] == [('arrival', {'stability/gm_m'})]
    assert not calls['physics'] and not calls['full']


def test_expired_evaluator_does_not_probe(monkeypatch):
    _, _, evaluator, calls = setup(monkeypatch)
    evaluator.deadline = time.monotonic()-1.
    from stowage.solver.ballast_search import Deadline
    with pytest.raises(Deadline):
        evaluator.evaluate(evaluator.initial)
    assert not any(calls.values())


def test_existing_ballast_is_not_kept_after_full_checker_veto(monkeypatch):
    instance, plan, _, calls = setup(monkeypatch)
    import stowage.solver.ballast as legacy
    monkeypatch.setattr(legacy, '_validated_replay', lambda *args: SimpleNamespace(
        final_state=SimpleNamespace(ship=instance.onboard)))
    plan = plan.model_copy(update={'ballast':BallastCondition(tank_masses_kg={},source='planner')})
    outcome = ballast_v4.finish(instance, plan, time_limit_s=1.)
    assert outcome.status == 'not_found' and outcome.plan is None
    assert outcome.not_found_cause == 'checker:existing_ballast_rejected'
    assert len(calls['full']) == 1


def test_timeout_diagnostics_can_preserve_unknown_worker_counters():
    result = ballast_v4.BallastV4Outcome(status='not_found',
        not_found_cause='timeout:worker', diagnostics={'evaluations':None})
    restored = ballast_v4.BallastV4Outcome.model_validate_json(result.model_dump_json())
    assert restored.diagnostics['evaluations'] is None
    assert restored.not_found_cause == 'timeout:worker'


def test_tiny_initial_recipe_pass_skips_proof_context_and_duplicate_parts(monkeypatch):
    instance, plan, _, calls = setup(monkeypatch, full_pass=True)
    import stowage.solver.ballast as legacy
    import stowage.solver.ballast_proof as proof
    monkeypatch.setattr(legacy, '_validated_replay', lambda *args: SimpleNamespace(
        final_state=SimpleNamespace(ship=instance.onboard)))
    monkeypatch.setattr(proof, 'prove_infeasible', lambda *args: pytest.fail('passing tiny seed needs no proof'))
    monkeypatch.setattr(module, 'Evaluator', lambda *args: pytest.fail('passing tiny seed needs no context'))
    result = ballast_v4.finish(instance, plan, time_limit_s=1.)
    assert result.status == 'ok' and result.plan.ballast.source == 'finisher'
    assert result.diagnostics['tiny_initial_full_checker_passed']
    assert result.diagnostics['full_checker_calls'] == 1
    assert not calls['cheap'] and not calls['physics']


def test_tiny_initial_full_veto_continues_search(monkeypatch):
    instance, plan, evaluator, calls = setup(monkeypatch, full_pass=True)
    import stowage.solver.ballast as legacy
    import stowage.solver.ballast_proof as proof
    import stowage.checker
    full = stowage.checker.check
    attempts = []
    def first_veto(inst, candidate):
        attempts.append(candidate)
        if len(attempts) == 1:
            return CheckReport(passed=False)
        return full(inst, candidate)
    monkeypatch.setattr(stowage.checker, 'check', first_veto)
    monkeypatch.setattr(legacy, '_validated_replay', lambda *args: SimpleNamespace(
        final_state=SimpleNamespace(ship=instance.onboard)))
    monkeypatch.setattr(proof, 'prove_infeasible', lambda *args: None)
    monkeypatch.setattr(legacy, '_seed_masses', lambda *args: [])
    def ready(inst, candidate, ship, deadline, diagnostics):
        evaluator.diagnostics = diagnostics
        return evaluator
    monkeypatch.setattr(module, 'Evaluator', ready)
    result = ballast_v4.finish(instance, plan, time_limit_s=1.)
    assert result.status == 'ok'
    assert len(attempts) == 2
    assert not result.diagnostics['tiny_initial_full_checker_passed']
    assert result.diagnostics['candidates_evaluated'] == 1
    assert result.diagnostics['candidate_count'] == 1


def test_shared_successive_helper_sees_legacy_gm_endpoint_escape(monkeypatch):
    _, _, evaluator, calls = setup(monkeypatch, full_pass=True)
    for _, context in evaluator.contexts:
        context.margins = lambda fill, active_keys=None: {'stability/gm_m': float(np.sum(fill))-.1}
    from stowage.solver.ballast_successive import search
    with pytest.raises(Found):
        search([np.zeros(len(evaluator.tanks))], evaluator.evaluate, time.monotonic()+1.)
    # Existing helper goes directly from negative GM to the full endpoint,
    # without wasting or misdirecting a finite-difference gradient at dry tanks.
    assert evaluator.diagnostics.get('selective_margin_evaluations', 0) == 0
    assert len(calls['full']) == 1


@pytest.mark.parametrize('error_type', [ValueError, ArithmeticError])
def test_failed_recipe_loading_probe_is_cached_and_later_seed_can_pass(monkeypatch, error_type):
    _, _, evaluator, calls = setup(monkeypatch, full_pass=True)
    context = evaluator.contexts[0][1]
    attempts = []
    def per_recipe(fill, active_keys=None):
        attempts.append(fill.copy())
        if np.all(fill == 0.):
            raise error_type('synthetic nonconvergent empty-tank equilibrium')
        return {'stability/gm_m':1.}
    context.margins = per_recipe
    original_stability = evaluator.functions['stability']
    def public_stability(inst, ship, *, call_index):
        if not any(ship.tank_masses_kg.get(t.id, 0.) for t in evaluator.tanks):
            return PartReport(name='stability', ran=False, error='synthetic public equilibrium unavailable')
        return original_stability(inst, ship, call_index=call_index)
    evaluator.functions['stability'] = public_stability
    empty = np.zeros(len(evaluator.tanks))
    values = evaluator.evaluate(empty)
    assert values['stability/unavailable'] < 0.
    assert evaluator.evaluate(empty) == values
    assert len(attempts) == 1
    assert not calls['physics'] and not calls['full']
    with pytest.raises(Found):
        evaluator.evaluate(np.ones(len(evaluator.tanks)))
    assert evaluator.accepted_report.passed
    assert evaluator.diagnostics['cheap_probe_errors'] == 1
    assert evaluator.diagnostics['candidate_count'] == 2


@pytest.mark.parametrize('failure', ['near_gm', 'equilibrium_unavailable', 'exception'])
def test_cheap_boundary_or_unknown_defers_to_passing_full_checker(monkeypatch, failure):
    _, _, evaluator, calls = setup(monkeypatch, full_pass=True)
    context = evaluator.contexts[0][1]
    def cheap(fill, active_keys=None):
        if failure == 'exception':
            raise ValueError('Picard unavailable; public coupled-root may converge')
        return ({'stability/gm_m':-.005} if failure == 'near_gm' else
                {'stability/gm_m':1., 'stability/equilibrium_unavailable':-1.})
    context.margins = cheap
    with pytest.raises(Found):
        evaluator.evaluate(evaluator.initial)
    assert evaluator.accepted_report.passed
    assert len(calls['physics']) == 12 and len(calls['full']) == 1


@pytest.mark.parametrize('flag', ['probe_unavailable', 'equilibrium_unavailable'])
def test_other_definite_failure_drops_unknown_feedback_before_caching(monkeypatch, flag):
    _, _, evaluator, calls = setup(monkeypatch, cheap_failure='mid_voyage', full_pass=True)
    evaluator.contexts[0][1].margins = lambda fill, active_keys=None: {
        'stability/gm_m':1., 'stability/'+flag:-1.}
    values = evaluator.evaluate(evaluator.initial)
    assert values['mid_voyage/stability/gm_m'] == -1.
    assert not any(key.endswith(('probe_unavailable', 'equilibrium_unavailable')) for key in values)
    assert evaluator.evaluate(evaluator.initial) == values
    assert not calls['physics'] and not calls['full']
