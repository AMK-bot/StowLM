"""Bound synthetic reports exercise condition presentation, not ship certification."""
import pytest

from app.checker import build_checker_payload
from stowage.checker import full
from stowage.model import CheckReport, PartReport, Provenance, Violation


def _bound_report(instance, plan, *, parts=None, passed=False, violations=None):
    return CheckReport(passed=passed, parts=parts or {}, violations=violations or [],
        provenance=Provenance(parents=list(full._input_bindings(instance, plan))))


def _qualified(name, condition_id, phase, *, failed=False, ran=True, gm=1.0):
    violation = Violation(rule_id='iscode.loading.gm', severity='hard', call_index=1,
                          message=f'[{condition_id} {phase}] synthetic low GM')
    return PartReport(name=name, ran=ran, error=None if ran else 'synthetic unavailable data',
        metrics={f'condition.id: {condition_id}': 1., f'condition.phase: {phase}': 1.,
                 'condition.call_index': float(phase == 'arrival'), 'gm_m': gm,
                 'note: APPROXIMATION: endpoint scope only': 1.},
        violations=[violation] if failed else [])


def test_unbound_report_is_retried_then_hides_all_evidence(replay_case, monkeypatch):
    instance, plan = replay_case
    report = CheckReport(passed=True, parts={'stability': PartReport(name='stability',
        metrics={'gm_m': 9., 'note: untrusted note': 1.})}, violations=[Violation(
            rule_id='iscode.loading.gm', severity='hard', call_index=0, message='untrusted diagnostic')])
    calls = []
    monkeypatch.setattr('app.checker.full.check', lambda *args: calls.append(args) or report)
    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    assert len(calls) == 2
    assert payload['bound'] is False
    assert payload['binding_status'] == 'unbound report'
    assert payload['passed'] is False
    assert payload['report'] is None
    assert payload['violations'] == []
    assert payload['conditions'] == []
    assert payload['notes'] == []
    assert payload['stability']['metrics'] == {}
    assert payload['stability']['criteria'] == []
    assert payload['strength']['stations'] == []
    assert payload['dg']['cargo'] == []


def test_recheck_can_recover_bound_evidence(replay_case, monkeypatch):
    instance, plan = replay_case
    reports = iter([CheckReport(passed=True), _bound_report(instance, plan)])
    monkeypatch.setattr('app.checker.full.check', lambda *args: next(reports))
    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    assert payload['bound'] is True
    assert payload['binding_status'] == 'bound'
    assert payload['report']['provenance']['parents']


def test_bound_report_for_edited_plan_is_not_shown(replay_case, monkeypatch):
    instance, plan = replay_case
    other = plan.model_copy(update={'moves': []})
    report = _bound_report(instance, other, parts={'strength': PartReport(
        name='strength', metrics={'max_bm_ratio': .1})})
    monkeypatch.setattr('app.checker.full.check', lambda *args: report)
    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    assert payload['binding_status'] == 'unbound report'
    assert payload['strength']['metrics'] == {}


def test_d50_condition_entries_keep_id_phase_failure_and_departure_alias(replay_case, monkeypatch):
    instance, plan = replay_case
    departure = _qualified('stability', 'leg:0:departure', 'departure', gm=1.)
    arrival = _qualified('stability', 'leg:0:arrival', 'arrival', failed=True, gm=.1)
    parts = {
        'stability': arrival.model_copy(update={'metrics': {'gm_m': 1.}, 'margins': {'gm_m': -.2}}),
        'strength': PartReport(name='strength'),
        'condition:leg:0:departure:stability': departure,
        'condition:leg:0:departure:strength': _qualified('strength', 'leg:0:departure', 'departure'),
        'condition:leg:0:arrival:stability': arrival,
        'condition:leg:0:arrival:strength': _qualified('strength', 'leg:0:arrival', 'arrival'),
        'voyage': PartReport(name='voyage', metrics={'note: arrival berth limits unavailable': 1.}),
    }
    report = _bound_report(instance, plan, parts=parts, violations=arrival.violations)
    monkeypatch.setattr('app.checker.full.check', lambda *args: report)
    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    assert payload['bound'] is True
    conditions = payload['conditions']
    assert [(c['id'], c['phase']) for c in conditions] == [
        ('leg:0:departure', 'departure'), ('leg:0:arrival', 'arrival')]
    assert conditions[0]['passed'] is True
    assert conditions[0]['failed_parts'] == []
    assert conditions[1]['passed'] is False
    assert conditions[1]['failed_parts'] == ['stability']
    assert conditions[1]['stability']['metrics']['gm_m'] == .1
    assert conditions[1]['violations'] == [v.model_dump(mode='json') for v in arrival.violations]
    assert payload['stability']['metrics']['gm_m'] == 1.
    assert payload['stability']['passed'] is True
    assert 'stability' in payload['failed_parts']
    assert 'arrival berth limits unavailable' in payload['notes']
    assert 'APPROXIMATION: endpoint scope only' in conditions[1]['notes']


def test_d38_d42_numeric_metric_notes_are_text_not_violations(replay_case, monkeypatch):
    instance, plan = replay_case
    notes = ['NOTE: ISO fallback rating is diagnostic only',
             'legacy background evidence is diagnostic only']
    report = _bound_report(instance, plan, parts={'rules': PartReport(name='rules',
        metrics={f'note: {note}': 1. for note in notes})}, passed=True)
    monkeypatch.setattr('app.checker.full.check', lambda *args: report)
    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    assert set(notes) <= set(payload['notes'])
    assert payload['violations'] == []


def test_d50_unavailable_arrival_keeps_identity_and_has_no_curve(replay_case, monkeypatch):
    instance, plan = replay_case
    parts = {'stability': PartReport(name='stability'), 'strength': PartReport(name='strength'),
        'condition:next-arrival:stability': _qualified('stability', 'next-arrival', 'arrival', ran=False),
        'condition:next-arrival:strength': _qualified('strength', 'next-arrival', 'arrival', ran=False)}
    report = _bound_report(instance, plan, parts=parts)
    monkeypatch.setattr('app.checker.full.check', lambda *args: report)
    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    condition = payload['conditions'][0]
    assert condition['id'] == 'next-arrival'
    assert condition['phase'] == 'arrival'
    assert condition['failed_parts'] == ['stability', 'strength']
    assert condition['stability']['gz_curve'] == []


def test_condition_plot_context_uses_reached_ballast_and_derived_arrival_density(replay_case, monkeypatch):
    from types import SimpleNamespace
    from stowage.model import BallastCondition, VoyageConditionIdentity, VoyageSpec
    from stowage.moves import apply_plan

    instance, plan = replay_case
    ballast = next(tank for tank in instance.vessel.tanks if tank.adjustable)
    mass = ballast.capacity_m3 * ballast.content_density_kg_m3 * .25
    plan = plan.model_copy(update={'ballast': BallastCondition(
        tank_masses_kg={ballast.id: mass}, source='planner')})
    departure = VoyageConditionIdentity(id='actual-departure', phase='departure', call_index=0,
        locode=instance.port.locode, environment_id='departure-env', density_id='departure-density',
        water_density_kg_m3=instance.port.water_density_kg_m3)
    arrival = VoyageConditionIdentity(id='actual-arrival', phase='arrival', call_index=1,
        locode=instance.rotation.calls[1].locode, environment_id='arrival-env',
        density_id='arrival-density', water_density_kg_m3=1000.)
    instance = instance.model_copy(update={'voyage': VoyageSpec(departure=departure, arrival=arrival)})
    parts = {}
    for identity in (departure, arrival):
        for name in ('stability', 'strength'):
            parts[f'condition:{identity.id}:{name}'] = _qualified(name, identity.id, identity.phase)
    report = _bound_report(instance, plan, parts=parts)
    monkeypatch.setattr('app.checker.full.check', lambda *args: report)
    seen = []
    expected_cargo = apply_plan(instance, plan).final_state.ship.placements

    def derive(ship, spec, *, vessel):
        assert ship.placements == expected_cargo
        assert ship.tank_masses_kg[ballast.id] == mass
        assert spec == instance.voyage
        return SimpleNamespace(tank_masses_kg={ballast.id: mass, 'synthetic-consumable': 10.})

    def panel(context, ship, part):
        seen.append((context, ship, part))
        return dict(ran=True, error=None, metrics=part.metrics, margins={}, criteria=[], gz_curve=[])

    monkeypatch.setattr('stowage.model.derive_arrival_liquid_state', derive)
    monkeypatch.setattr('app.checker.stability_payload', panel)
    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    assert len(seen) == 2
    before, after = seen
    assert before[0].port.water_density_kg_m3 == departure.water_density_kg_m3
    assert before[1].tank_masses_kg[ballast.id] == mass
    assert after[0].port.water_density_kg_m3 == 1000.
    assert after[0].port.locode == arrival.locode
    assert after[0].call_index == 1
    assert after[0].port.max_draught_m is None
    assert after[0].port.port_rules == []
    assert after[1].tank_masses_kg == {ballast.id: mass, 'synthetic-consumable': 10.}
    assert after[1].placements == expected_cargo
    assert payload['conditions'][1]['id'] == arrival.id
