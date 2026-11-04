"""D50 finisher acceptance includes the declared consumable arrival endpoint."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.model import MovePlan, PartReport, Violation
from stowage.model.fixtures import build_instance
from stowage.model.voyage import VoyageSpec, VoyageTankPolicy


def _instance():
    instance = build_instance()
    instance.vessel = instance.vessel.model_copy(update={
        'hull': None,
        'voyage_policy': VoyageTankPolicy(groups=[dict(id='fuel', kind='fuel_oil', tank_ids=['FO-1'])],
                                         drawdown_order=['FO-1']),
    })
    instance.voyage = VoyageSpec.model_validate(dict(
        departure=dict(id='leg-departure', phase='departure', call_index=0, locode='THLCH',
                       environment_id='departure-water', density_id='departure-density',
                       water_density_kg_m3=1025.),
        arrival=dict(id='leg-arrival', phase='arrival', call_index=1, locode='THBKK',
                     environment_id='arrival-water', density_id='arrival-density',
                     water_density_kg_m3=1000.),
    ))
    return instance


def _isolated(monkeypatch, instance, *, arrival_failure=True):
    import stowage.moves as moves
    import stowage.solver.ballast as ballast
    import stowage.solver.ballast_proof as proof
    import stowage.solver.ballast_pressed as pressed
    import stowage.checker.stability as stability
    import stowage.checker.strength as strength
    import stowage.checker.visibility as visibility
    import stowage.checker.lashing as lashing
    monkeypatch.setattr(moves, 'apply_plan', lambda *a: SimpleNamespace(
        legal=True, final_state=SimpleNamespace(ship=instance.onboard)))
    monkeypatch.setattr(proof, 'prove_infeasible', lambda *a, **kw: None)
    monkeypatch.setattr(pressed, 'search', lambda *a, **kw: [])
    seen = []
    capacities = {t.id: t.capacity_m3*t.content_density_kg_m3
                  for t in instance.vessel.tanks if t.adjustable}

    def checker(name):
        def run(inst, ship, *, call_index):
            fractions = [ship.tank_masses_kg[tid]/cap for tid, cap in capacities.items()]
            arrival = call_index == 1
            seen.append((name, arrival, inst.port.locode, inst.port.water_density_kg_m3,
                         ship.tank_masses_kg.copy()))
            failed = arrival and arrival_failure and name == 'stability' and min(fractions) < .6
            return PartReport(name=name, margins={'gm_m': min(fractions)-.6 if failed else 1.},
                violations=[Violation(rule_id='iscode.loading.gm', severity='hard', call_index=1,
                                      message='Synthetic arrival fails; departure passes')]
                           if failed else [])
        return run
    for module, name in ((stability, 'stability'), (strength, 'strength'),
                         (visibility, 'visibility'), (lashing, 'lashing')):
        monkeypatch.setattr(module, 'check_'+name, checker(name))
    return ballast, seen


def test_arrival_failure_repairs_ballast_and_retains_both_condition_reports(monkeypatch):
    instance = _instance()
    original = instance.model_dump()
    ballast, seen = _isolated(monkeypatch, instance)
    size = sum(t.adjustable for t in instance.vessel.tanks)

    def search(fills, evaluate, *args, **kwargs):
        margins = evaluate(np.full(size, .25))
        assert margins['arrival/stability/gm_m'] == pytest.approx(-.35)
        slopes = evaluate(np.full(size, .3), active_keys=('arrival/stability/gm_m',))
        assert slopes == {'arrival/stability/gm_m': pytest.approx(-.3)}
        evaluate(np.full(size, .75))
    monkeypatch.setattr(ballast, 'search', search)
    plan = MovePlan(id='arrival-regression', instance_id=instance.id, call_index=0)
    result = ballast._finish_ballast(instance, plan, time_limit_s=5)
    assert result.status == 'ok'
    assert set(result.parts) == {'stability', 'strength', 'visibility', 'lashing',
                                 'arrival/stability', 'arrival/strength'}
    arrivals = [row for row in seen if row[1]]
    assert arrivals
    assert all(row[2:4] == ('THBKK', 1000.) for row in arrivals)
    assert all(row[4]['FO-1'] == pytest.approx(4750.) for row in arrivals)
    assert all(result.plan.ballast.tank_masses_kg[t.id] == pytest.approx(
        t.capacity_m3*t.content_density_kg_m3*.75) for t in instance.vessel.tanks if t.adjustable)
    assert instance.model_dump() == original


@pytest.mark.parametrize('missing', ['voyage', 'policy', 'forbidden_ballast'])
def test_missing_or_forbidden_arrival_evidence_never_accepts_departure_only(monkeypatch, missing):
    instance = _instance()
    if missing == 'voyage':
        instance.voyage = None
    elif missing == 'policy':
        instance.vessel.voyage_policy = None
    else:
        from stowage.model.voyage import VoyageBallastOperation
        instance.voyage.ballast_operations = [VoyageBallastOperation(tank_id='WB-P', mass_change_kg=0.)]
    ballast, seen = _isolated(monkeypatch, instance, arrival_failure=False)
    def search(fills, evaluate, *args, **kwargs):
        margins = evaluate(np.zeros(sum(t.adjustable for t in instance.vessel.tanks)))
        assert margins['arrival/unavailable'] < 0
    monkeypatch.setattr(ballast, 'search', search)
    plan = MovePlan(id='missing-arrival', instance_id=instance.id, call_index=0)
    result = ballast._finish_ballast(instance, plan, time_limit_s=5)
    assert result.status == 'not_found'
    assert not result.parts['arrival/stability'].ran
    assert result.parts['arrival/stability'].error


def test_arrival_strength_hard_violation_cannot_accept_departure_only(monkeypatch):
    import stowage.checker.strength as strength
    instance = _instance()
    ballast, _ = _isolated(monkeypatch, instance, arrival_failure=False)
    def check_strength(inst, ship, *, call_index):
        return PartReport(name='strength', violations=[Violation(
            rule_id='iacs.s11a.still_water_shear', severity='hard', call_index=call_index,
            message='Synthetic arrival-only shear exceedance')] if call_index == 1 else [])
    monkeypatch.setattr(strength, 'check_strength', check_strength)
    def search(fills, evaluate, *args, **kwargs):
        margins = evaluate(np.zeros(sum(t.adjustable for t in instance.vessel.tanks)))
        assert margins['arrival/strength/hard_violation'] == -1.
    monkeypatch.setattr(ballast, 'search', search)
    result = ballast._finish_ballast(instance, MovePlan(
        id='arrival-strength', instance_id=instance.id, call_index=0), time_limit_s=5)
    assert result.status == 'not_found'
    assert result.parts['arrival/strength'].violations[0].call_index == 1


def test_explicit_consumption_is_used_by_arrival_context():
    from stowage.model.voyage import VoyageConsumption
    from stowage.solver.ballast_arrival import arrival_condition
    instance = _instance()
    instance.voyage.consumption = [VoyageConsumption(group_id='fuel', mass_kg=1000.)]
    before = instance.onboard.model_dump()
    context, arrival = arrival_condition(instance, instance.onboard)
    assert arrival.tank_masses_kg['FO-1'] == 46500.
    assert arrival.tank_masses_kg['WB-P'] == instance.onboard.tank_masses_kg['WB-P']
    assert arrival.placements == instance.onboard.placements
    assert instance.onboard.model_dump() == before
    assert context.call_index == 1
    assert context.voyage.arrival.environment_id == 'arrival-water'
    # D50 is a seagoing endpoint. The current berth is not the next berth;
    # unknown next-port limits must not be copied from another LOCODE.
    assert context.port.max_draught_m is None
    assert context.port.port_rules == []
    assert context.vessel.hull is instance.vessel.hull
