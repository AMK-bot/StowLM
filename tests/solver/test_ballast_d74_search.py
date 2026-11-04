"""D74 search orchestration and unknown calculations are isolated from physics."""
import pytest

from stowage.model import PartReport, Violation
from stowage.solver import ballast
from tests.solver.test_ballast_inport import _instance, _plan, _isolate, _in_port


def _policy(monkeypatch, instance):
    _isolate(monkeypatch)
    helper = _in_port(monkeypatch, instance)
    monkeypatch.setattr(helper, 'rate_margin', lambda masses: 1., raising=False)
    monkeypatch.setattr(helper, 'pumped_mass', lambda masses: 0., raising=False)
    monkeypatch.setattr(helper, 'seed_masses', lambda: [], raising=False)
    return helper


def test_unknown_seagoing_report_is_not_a_cached_rejection_or_search_constraint(monkeypatch):
    import stowage.checker.stability as stability
    instance = _instance()
    helper = _policy(monkeypatch, instance)
    calls = []
    monkeypatch.setattr(ballast, 'search', lambda *a, **kw: calls.append('search'))
    monkeypatch.setattr(stability, 'check_stability', lambda *a, **kw: PartReport(
        name='stability', ran=False, error='StabilityTimeout: time budget exceeded',
        violations=[Violation(rule_id='checker.internal_error', severity='hard',
                              call_index=0, message='StabilityTimeout')]))
    result = ballast._search_ballast(instance, _plan(instance), time_limit_s=1., _in_port=helper)
    assert result.status == 'not_found'
    assert calls == []
    assert result.not_found_cause == 'timeout:search'
    assert not any('unavailable' in key for part in result.parts.values() for key in part.margins)


def test_active_departure_trial_stops_at_first_completed_physical_failure(monkeypatch):
    instance = _instance()
    helper = _policy(monkeypatch, instance)
    seen = []
    for name in ('stability', 'strength', 'visibility', 'lashing'):
        module = __import__('stowage.checker.'+name, fromlist=['check_'+name])
        def check(*args, _name=name, **kw):
            seen.append(_name)
            return PartReport(name=_name, margins={'gm_m': -.1}, violations=[Violation(
                rule_id='iscode.a2_2.initial_gm', severity='hard', call_index=0,
                message='completed physical failure')])
        monkeypatch.setattr(module, 'check_'+name, check)
    result = ballast._search_ballast(instance, _plan(instance), time_limit_s=1., _in_port=helper)
    assert result.status == 'not_found'
    assert seen == ['stability']


def test_active_search_checks_arrival_before_lp_seed(monkeypatch):
    import stowage.linear.bands as bands
    instance = _instance()
    helper = _policy(monkeypatch, instance)
    events = []
    import stowage.checker.stability as stability
    monkeypatch.setattr(stability, 'check_stability', lambda *a, **kw: (
        events.append('arrival_seed') or PartReport(name='stability')))
    def no_lp(*a, **kw):
        events.append('lp')
        raise ValueError('synthetic no LP')
    monkeypatch.setattr(bands, 'band_for_instance', no_lp)
    result = ballast._search_ballast(instance, _plan(instance), time_limit_s=1., _in_port=helper)
    assert result.status == 'ok'
    assert events[0] == 'arrival_seed'
    assert 'lp' not in events


def test_passing_candidates_keep_the_smallest_pumped_mass_incumbent(monkeypatch):
    from stowage.model import BallastCondition, BallastOutcome
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    _isolate(monkeypatch)
    choices = [0., 25625.]
    seen = []
    def search(inst, plan, *, _in_port, **kw):
        if not choices:
            return BallastOutcome(status='not_found')
        mass = choices.pop(0)
        seen.append(mass)
        masses = dict(inst.onboard.tank_masses_kg, **{'DB-P': mass, 'DB-S': mass})
        return BallastOutcome(status='ok', plan=plan.model_copy(update={
            'ballast': BallastCondition(tank_masses_kg=masses, source='finisher')}))
    def accept(policy, masses):
        policy.failure = policy.failure_cause = None
        policy.ballast_programme = {'pumped_mass_t': policy.pumped_mass(masses)/1000.}
        return PartReport(name='in_port')
    monkeypatch.setattr(ballast, '_search_ballast', search)
    monkeypatch.setattr(InPortFinisher, 'accept', accept)
    with rule_freeze_v1(enabled=True):
        result = ballast._finish_ballast(instance, _plan(instance), time_limit_s=1.)
    assert result.status == 'ok'
    assert seen == [0., 25625.]
    assert result.plan.ballast.tank_masses_kg['DB-P'] == 25625.
    assert result.ballast_programme['pumped_mass_t'] == 51.25


def test_d74_fields_serialize_only_for_activated_result():
    from stowage.model import BallastOutcome
    result = ballast._active_metadata(BallastOutcome(status='not_found'),
        cause='timeout:search', programme={'changeover_k': 5, 'rate_check_rule': 'port.ballast.transfer_rate'})
    assert result.model_dump()['not_found_cause'] == 'timeout:search'
    assert result.model_dump()['ballast_programme']['changeover_k'] == 5
    assert 'not_found_cause' not in BallastOutcome(status='not_found').model_dump()


def test_reported_pump_delay_is_not_a_hard_search_margin(monkeypatch):
    instance = _instance()
    helper = _policy(monkeypatch, instance)
    monkeypatch.setattr(helper, 'rate_margin', lambda masses: -1.)
    result = ballast._search_ballast(instance, _plan(instance), time_limit_s=1., _in_port=helper)
    assert result.status == 'ok'
    assert not any('rate' in key for part in result.parts.values() for key in part.margins)


@pytest.mark.parametrize('cause', ['rate_infeasible:R2',
                                 'acceptance_failure:port.stability.cargo_operations'])
def test_rejected_seed_does_not_starve_later_fully_passing_seed(monkeypatch, cause):
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    _isolate(monkeypatch)
    seen = []
    monkeypatch.setattr(InPortFinisher, 'seed_masses',
        lambda self: [{'DB-P': 25625., 'DB-S': 25625.}], raising=False)
    def accept(policy, masses):
        mass = masses['DB-P']
        seen.append(mass)
        if mass == 51250.:
            policy.failure_cause, policy.failure = cause, 'completed nonideal rejection'
            # No valid ideal-split gradient exists for this rejection.
            assert not policy.failed_states
            return PartReport(name='in_port', error=policy.failure, violations=[Violation(
                rule_id=('port.ballast.transfer_rate' if cause == 'rate_infeasible:R2'
                         else 'port.stability.cargo_operations'), severity='hard', call_index=0,
                message=policy.failure)])
        policy.failure_cause = policy.failure = None
        return PartReport(name='in_port')
    monkeypatch.setattr(InPortFinisher, 'accept', accept)
    with rule_freeze_v1(enabled=True):
        result = ballast._finish_ballast(instance, _plan(instance), time_limit_s=1.)
    assert result.status == 'ok', (result.model_dump(), seen)
    assert seen == [51250., 25625.]
    assert result.plan.ballast.tank_masses_kg['DB-P'] == 25625.


def test_unknown_acceptance_does_not_become_a_rejected_candidate(monkeypatch):
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    _isolate(monkeypatch)
    observed = []
    def accept(policy, masses):
        observed.append(policy)
        policy.failure, policy.failure_cause = 'endpoint unavailable', 'unknown_witness'
        return PartReport(name='in_port', ran=False, error=policy.failure)
    monkeypatch.setattr(InPortFinisher, 'accept', accept)
    with rule_freeze_v1(enabled=True):
        result = ballast._finish_ballast(instance, _plan(instance), time_limit_s=1.)
    assert result.not_found_cause == 'unknown_witness'
    assert len(observed) == 1
    assert not getattr(observed[0], 'rejected_candidates', set())


def test_completed_rejection_still_allows_real_selective_slope_measurement(monkeypatch):
    import numpy as np
    instance = _instance()
    helper = _policy(monkeypatch, instance)
    helper.rejected_candidates = {ballast._candidate_key({tid: mass for tid, mass in
        helper.project({'DB-P': 51250., 'DB-S': 51250.}).items() if helper.tanks[tid].adjustable})}
    seen = []
    def search(fills, evaluate, *args, **kw):
        margins = evaluate(np.array([.5, .5]), active_keys=('stability/test_margin',))
        seen.append(margins)
        assert margins == {'stability/test_margin': 1.}
    monkeypatch.setattr(ballast, 'search', search)
    ballast._search_ballast(instance, _plan(instance), time_limit_s=1., _in_port=helper)
    assert len(seen) == 1


@pytest.mark.parametrize('lifts,expected', [(0, 'not_found'), (300, 'ok')])
def test_delayed_changeover_keeps_window_hard_and_long_window_passes(monkeypatch, lifts, expected):
    """Synthetic physics isolates real D74 timing/window programme through 4.2."""
    from stowage.model import BallastCondition, BallastOutcome, HatchCoverOff, HatchCoverOn
    from stowage.rules.catalogue import rule_freeze_v1
    instance = _instance()
    seen = _isolate(monkeypatch)
    instance.onboard.tank_masses_kg.update({'DB-P': 0., 'DB-S': 0.})
    if not lifts:
        import stowage.checker.stability as stability
        original = stability.check_stability
        def arrival_failure(inst, ship, **kw):
            part = original(inst, ship, **kw)
            if kw.get('in_port') and ship.tank_masses_kg.get('DB-P', 0.) == 0.:
                return PartReport(name='stability', margins={'gm_m': -.1},
                    violations=[Violation(rule_id='port.stability.cargo_operations',
                        severity='hard', call_index=0, message='synthetic arrival k=0')])
            return part
        monkeypatch.setattr(stability, 'check_stability', arrival_failure)
    cover = instance.vessel.hatch_covers[0].id
    moves = [HatchCoverOff(call_index=0, cover_id=cover), HatchCoverOn(call_index=0, cover_id=cover)]*(lifts//2)
    plan = _plan(instance).model_copy(update={'moves': moves})
    # R1: 200t at 300m³/h, density1025: requires~0.65h. No-lift
    # allowance0.5h adds delay. The unsafe arrival endpoint remains a hard
    # window rejection; 300lifts pass and require a nonempty endpoint window.
    proposed = False
    def search(inst, candidate_plan, **kw):
        nonlocal proposed
        if proposed:
            return BallastOutcome(status='not_found')
        proposed = True
        masses = dict(inst.onboard.tank_masses_kg, **{'DB-P': 100000., 'DB-S': 100000.})
        return BallastOutcome(status='ok', plan=candidate_plan.model_copy(update={
            'ballast': BallastCondition(tank_masses_kg=masses, source='finisher')}))
    monkeypatch.setattr(ballast, '_search_ballast', search)
    with rule_freeze_v1(enabled=True):
        result = ballast._finish_ballast(instance, plan, time_limit_s=2.)
    assert result.status == expected, result.model_dump()
    assert result.ballast_programme['pumped_mass_t'] == 200.
    assert result.ballast_programme['rate_check_rule'] == 'port.ballast.transfer_rate'
    if expected == 'not_found':
        assert result.ballast_programme['ballast_delay_h'] > 0.
        assert result.not_found_cause == 'acceptance_failure:port.stability.cargo_operations'
    else:
        assert result.ballast_programme['changeover_k'] == 301
        assert result.ballast_programme['pump_window_lifts'] > 0
        # Arrival pass plus BOTH endpoint checks; suffix is empty at k=301.
        assert len([entry for entry in seen if entry[1]]) > len(moves)+1


def test_public_nonpositive_budget_has_cause_only_when_activated():
    from stowage.rules.catalogue import rule_freeze_v1
    instance = _instance()
    with rule_freeze_v1(enabled=True):
        result = ballast.finish_ballast(instance, _plan(instance), time_limit_s=0.)
    assert result.model_dump()['not_found_cause'] == 'timeout:search'
    with rule_freeze_v1(enabled=False):
        legacy = ballast.finish_ballast(instance, _plan(instance), time_limit_s=0.)
    assert 'not_found_cause' not in legacy.model_dump()


def test_known_witness_fact_is_required_for_search_miss_classification(monkeypatch):
    from stowage.model import BallastOutcome
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    _isolate(monkeypatch)
    prepare = InPortFinisher.prepare
    def verified(policy):
        prepare(policy)
        # Isolate a prior complete witness verdict; existence of a recipe
        # alone never sets this fact in production.
        policy.known_passing_witness = True
        return policy
    monkeypatch.setattr(InPortFinisher, 'prepare', verified)
    monkeypatch.setattr(ballast, '_search_ballast', lambda *a, **kw: BallastOutcome(status='not_found'))
    with rule_freeze_v1(enabled=True):
        result = ballast._finish_ballast(instance, _plan(instance), time_limit_s=1.)
    assert result.not_found_cause == 'search_miss'


def test_remaining_seed_order_is_d58_then_berth_then_rate_lp(monkeypatch):
    from types import SimpleNamespace
    import stowage.checker.stability as stability
    import stowage.linear.bands as bands
    import stowage.linear.model as linear
    import stowage.solver.ballast_rate_lp as rate_lp
    instance = _instance()
    helper = _policy(monkeypatch, instance)
    mass = 102500.
    monkeypatch.setattr(helper, 'seed_masses', lambda: [
        {'DB-P': .6*mass, 'DB-S': .6*mass}, {'DB-P': .7*mass, 'DB-S': .7*mass}])
    seen = []
    def failed(inst, ship, **kw):
        seen.append(ship.tank_masses_kg['DB-P']/mass)
        return PartReport(name='stability', margins={'gm_m': -.1}, violations=[Violation(
            rule_id='iscode.a2_2.initial_gm', severity='hard', call_index=0, message='synthetic failure')])
    monkeypatch.setattr(stability, 'check_stability', failed)
    monkeypatch.setattr(bands, 'band_for_instance', lambda *a, **kw: object())
    monkeypatch.setattr(linear, 'build_linear_model', lambda *a, **kw: object())
    monkeypatch.setattr(rate_lp, 'solve_rate_ballast_lp', lambda *a, **kw: SimpleNamespace(
        status='optimal', tank_masses_kg={'DB-P': .8*mass, 'DB-S': .8*mass}))
    result = ballast._search_ballast(instance, _plan(instance), time_limit_s=1., _in_port=helper)
    assert result.status == 'not_found'
    assert seen == pytest.approx([.5, .6, .7, .8])
