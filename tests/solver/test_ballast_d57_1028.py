"""The finisher must not accept a D57 state that its old endpoint gate omitted."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.model import MovePlan, PartReport, Violation
from stowage.model.fixtures import build_instance
from stowage.model.voyage import VoyageSpec, VoyageTankPolicy, VoyageConsumption
from stowage.solver import ballast
from stowage.solver.ballast_arrival import arrival_condition, mid_voyage_condition


def _instance():
    instance = build_instance()
    instance.vessel = instance.vessel.model_copy(update={
        'hull': None,
        'voyage_policy': VoyageTankPolicy(
            groups=[dict(id='fuel', kind='fuel_oil', tank_ids=['FO-1'])],
            drawdown_order=['FO-1'])})
    instance.voyage = VoyageSpec.model_validate(dict(
        departure=dict(id='dep', phase='departure', call_index=0, locode='THLCH',
                       environment_id='dep-water', density_id='dep-rho', water_density_kg_m3=1025.),
        arrival=dict(id='arr', phase='arrival', call_index=1, locode='THBKK',
                     environment_id='arr-water', density_id='arr-rho', water_density_kg_m3=1000.)))
    return instance


def _isolate(monkeypatch, instance, failure_phase, failure_part, *, threshold=2.):
    import stowage.moves as moves
    import stowage.solver.ballast_proof as proof
    monkeypatch.setattr(moves, 'apply_plan', lambda *a: SimpleNamespace(
        legal=True, final_state=SimpleNamespace(ship=instance.onboard)))
    monkeypatch.setattr(proof, 'prove_infeasible', lambda *a, **kw: None)
    seen = []
    capacities = {t.id: t.capacity_m3*t.content_density_kg_m3
                  for t in instance.vessel.tanks if t.adjustable}
    for name in ('stability', 'strength', 'visibility', 'lashing'):
        module = __import__('stowage.checker.'+name, fromlist=['check_'+name])

        def checker(inst, ship, *, call_index, _name=name):
            phase = ('arrival' if call_index == 1 else 'departure'
                     if ship.tank_masses_kg['FO-1'] == 47500. else 'mid_voyage')
            seen.append((phase, _name))
            fill = min(ship.tank_masses_kg[tid]/capacity for tid, capacity in capacities.items())
            margin = fill-threshold if (phase, _name) == (failure_phase, failure_part) else 1.
            return PartReport(name=_name, margins={'test_margin': margin}, violations=[Violation(
                rule_id='synthetic.d57.failure', severity='hard', call_index=call_index,
                message=f'{phase} {_name} fails')] if margin < 0. else [])
        monkeypatch.setattr(module, 'check_'+name, checker)
    return seen


@pytest.mark.parametrize('phase', ['departure', 'mid_voyage', 'arrival'])
@pytest.mark.parametrize('part', ['stability', 'strength', 'visibility', 'lashing'])
def test_every_physics_part_at_every_sample_can_veto_ballast(monkeypatch, phase, part):
    instance = _instance()
    seen = _isolate(monkeypatch, instance, phase, part)
    monkeypatch.setattr(ballast, 'search', lambda *a, **kw: None)
    outcome = ballast._finish_ballast(instance, MovePlan(
        id='d57-veto', instance_id=instance.id, call_index=0), time_limit_s=2.)
    assert outcome.status == 'not_found'
    assert outcome.plan is None
    assert set(seen) == {(p, n) for p in ('departure', 'mid_voyage', 'arrival')
                         for n in ('stability', 'strength', 'visibility', 'lashing')}
    assert any(v.rule_id == 'synthetic.d57.failure' for report in outcome.parts.values()
               for v in report.violations)


def test_midpoint_failure_guides_search_and_success_retains_legacy_reports(monkeypatch):
    instance = _instance()
    _isolate(monkeypatch, instance, 'mid_voyage', 'visibility', threshold=.6)

    def search(fills, evaluate, *args, **kwargs):
        key = 'mid_voyage/visibility/test_margin'
        assert evaluate(np.full(2, .25))[key] == pytest.approx(-.35)
        assert evaluate(np.full(2, .3), active_keys=(key,)) == {key: pytest.approx(-.3)}
        evaluate(np.full(2, .75))
    monkeypatch.setattr(ballast, 'search', search)
    outcome = ballast._finish_ballast(instance, MovePlan(
        id='d57-repaired', instance_id=instance.id, call_index=0), time_limit_s=2.)
    assert outcome.status == 'ok'
    assert set(outcome.parts) == {'stability', 'strength', 'visibility', 'lashing',
                                  'arrival/stability', 'arrival/strength'}
    assert outcome.parts['visibility'].margins['mid_voyage/test_margin'] == pytest.approx(.15)
    assert all(p.ran and not p.violations for p in outcome.parts.values())


@pytest.mark.parametrize('explicit_consumption', [False, True])
def test_midpoint_uses_model_fraction_and_departure_density(explicit_consumption):
    instance = _instance()
    if explicit_consumption:
        instance.voyage.consumption = [VoyageConsumption(group_id='fuel', mass_kg=1000.)]
    original = instance.model_dump()
    context, ship = mid_voyage_condition(instance, instance.onboard)
    assert ship.tank_masses_kg['FO-1'] == pytest.approx(47000. if explicit_consumption else 26125.)
    assert ship.tank_masses_kg['WB-P'] == instance.onboard.tank_masses_kg['WB-P']
    assert context.port.water_density_kg_m3 == 1025.
    assert context.port.locode == 'THLCH'
    assert context.call_index == 0
    assert context.port.max_draught_m is None and context.port.port_rules == []
    assert instance.model_dump() == original


def test_midpoint_id_collision_fails_closed():
    instance = _instance()
    instance.voyage.arrival.id = 'dep:mid_voyage'
    for helper in (arrival_condition, mid_voyage_condition):
        with pytest.raises(ValueError, match='collides'):
            helper(instance, instance.onboard)


@pytest.mark.parametrize('part', ['stability', 'strength', 'visibility', 'lashing'])
def test_unavailable_midpoint_part_cannot_authorize_ballast(monkeypatch, part):
    instance = _instance()
    _isolate(monkeypatch, instance, 'unused', 'unused')
    module = __import__('stowage.checker.'+part, fromlist=['check_'+part])

    def check(inst, ship, *, call_index):
        middle = 4750. < ship.tank_masses_kg['FO-1'] < 47500.
        return PartReport(name=part, ran=not middle, error='unavailable' if middle else None)
    monkeypatch.setattr(module, 'check_'+part, check)
    monkeypatch.setattr(ballast, 'search', lambda *a, **kw: None)
    outcome = ballast._finish_ballast(instance, MovePlan(
        id='d57-unavailable', instance_id=instance.id, call_index=0), time_limit_s=2.)
    assert outcome.status == 'not_found' and outcome.plan is None
    assert not outcome.parts[part].ran
