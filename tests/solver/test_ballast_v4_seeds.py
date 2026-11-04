"""Recipe/order contracts with doubles; no synthetic physical-pass claim."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.model import CheckReport, MovePlan, PartReport
from stowage.model.fixtures import build_instance
from stowage.solver import ballast_v4
from stowage.solver.ballast_search import Deadline
from stowage.solver.ballast_v4_seeds import trim_seed_recipes


def recipe_instance():
    tanks = []
    for prefix in ('APT', 'WING-A', 'FPT', 'FWD-DEEP', 'DB-F', 'OTHER'):
        for side, capacity, density in (('P', 10., 1000.), ('S', 20., 1025.)):
            tanks.append(SimpleNamespace(id=f'{prefix}-{side}', adjustable=True,
                capacity_m3=capacity, content_density_kg_m3=density))
    tanks.append(SimpleNamespace(id='APT-FIXED', adjustable=False,
        capacity_m3=100., content_density_kg_m3=900.))
    masses = {tank.id: 123. if tank.id.endswith('-P') else 456. for tank in tanks}
    return SimpleNamespace(vessel=SimpleNamespace(tanks=tanks),
                           onboard=SimpleNamespace(tank_masses_kg=masses))


def test_grid_exact_order_own_capacities_fixed_tanks_and_inherited_base():
    instance = recipe_instance()
    original = dict(instance.onboard.tank_masses_kg)
    recipes = trim_seed_recipes(instance)
    labels = ['inherited'] + [f"fill{aft}|empty{forward}" for aft in
        ('APT', 'APT+WING-A', 'WING-A') for forward in
        ('-', 'FPT+FWD-DEEP', 'FPT+FWD-DEEP+DB-F')]
    assert [recipe.label for recipe in recipes] == labels
    adjustable = {t.id: t for t in instance.vessel.tanks if t.adjustable}
    for recipe in recipes:
        assert 'APT-FIXED' not in recipe.tank_masses_kg
        assert recipe.tank_masses_kg['OTHER-P'] == 123.
        assert recipe.tank_masses_kg['OTHER-S'] == 456.
        if recipe.label == 'inherited':
            assert recipe.tank_masses_kg == {k:v for k,v in original.items() if k in adjustable}
            continue
        aft, forward = recipe.label.removeprefix('fill').split('|empty')
        for tank_id, tank in adjustable.items():
            expected = original[tank_id]
            if any(tank_id.startswith(prefix) for prefix in aft.split('+')):
                expected = tank.capacity_m3*tank.content_density_kg_m3
            if any(tank_id.startswith(prefix) for prefix in forward.split('+')):
                expected = 0.
            assert recipe.tank_masses_kg[tank_id] == expected
        # Both sides are full fractions, even when capacities/densities differ.
        for prefix in aft.split('+'):
            for side in ('P', 'S'):
                tank = adjustable[f'{prefix}-{side}']
                assert recipe.tank_masses_kg[tank.id]/(tank.capacity_m3*tank.content_density_kg_m3) == 1.
    assert instance.onboard.tank_masses_kg == original
    recipes[1].tank_masses_kg['OTHER-P'] = 999.
    assert recipes[0].tank_masses_kg['OTHER-P'] == 123.
    assert recipes[2].tank_masses_kg['OTHER-P'] == 123.


def test_tiny_tries_actual_arrival_before_proof_or_context(monkeypatch):
    import stowage.checker
    import stowage.solver.ballast as legacy
    import stowage.solver.ballast_proof as proof
    import stowage.solver.ballast_v4_evaluator as evaluator
    instance = build_instance()
    replay_ship = instance.onboard.model_copy(update={'tank_masses_kg':{'WB-P':0., 'WB-S':0.}})
    monkeypatch.setattr(legacy, '_validated_replay', lambda *args: SimpleNamespace(
        final_state=SimpleNamespace(ship=replay_ship)))
    monkeypatch.setattr(proof, 'prove_infeasible', lambda *args: pytest.fail('arrival precedes proof'))
    monkeypatch.setattr(evaluator, 'Evaluator', lambda *args: pytest.fail('arrival precedes context'))
    seen = []
    def check(inst, plan):
        seen.append(plan.ballast.tank_masses_kg)
        assert seen[-1] == {'WB-P':51250., 'WB-S':51250.}
        return CheckReport(passed=True)
    monkeypatch.setattr(stowage.checker, 'check', check)
    plan = MovePlan(id='arrival-first', instance_id=instance.id, call_index=0)
    result = ballast_v4.finish(instance, plan, time_limit_s=1.)
    assert result.status == 'ok' and len(seen) == 1
    assert result.diagnostics['accepted_seed'] == 'inherited'
    assert result.diagnostics['full_checker_passed'] is True


@pytest.mark.parametrize('load_count', [17, 428])
@pytest.mark.parametrize('cheap_passes', [False, True])
def test_large_actual_arrival_is_first_exact_proposal_with_unchanged_full_gate(
        monkeypatch, load_count, cheap_passes):
    import stowage.checker
    import stowage.solver.ballast as legacy
    import stowage.solver.ballast_proof as proof
    import stowage.solver.ballast_v4_evaluator as module
    import stowage.solver.ballast_pressed as pressed
    import stowage.solver.ballast_successive as successive
    instance = build_instance()
    instance.load_list = [instance.load_list[0]]*load_count
    replay_ship = instance.onboard.model_copy(update={'tank_masses_kg':{'WB-P':0., 'WB-S':0.}})
    monkeypatch.setattr(legacy, '_validated_replay', lambda *args: SimpleNamespace(
        final_state=SimpleNamespace(ship=replay_ship)))
    monkeypatch.setattr(proof, 'prove_infeasible', lambda *args: None)
    monkeypatch.setattr(legacy, '_seed_masses', lambda *args: [])
    monkeypatch.setattr(pressed, 'search', lambda *args: [])
    monkeypatch.setattr(successive, 'search', lambda *args, **kwargs: None)
    seen = []
    class Context:
        def __init__(self, inst, ship, deadline):
            self.tanks = sorted((t for t in inst.vessel.tanks if t.adjustable), key=lambda t:t.id)
        def margins(self, fill, active_keys=None):
            seen.append(tuple(fill))
            return {'stability/gm_m':1. if cheap_passes else -1.}
    monkeypatch.setattr(module, 'ExactContext', Context)
    monkeypatch.setattr(module, 'conditions', lambda inst, ship:
        [(phase, inst, ship) for phase in ('departure', 'mid_voyage', 'arrival')])
    monkeypatch.setattr(module, 'checkers', lambda: {
        name:lambda *args, **kwargs: PartReport(name='synthetic_part', margins={'clearance':1.})
        for name in ('stability', 'strength', 'visibility', 'lashing')})
    full = []
    def veto(inst, candidate):
        assert cheap_passes, 'a definite cheap failure must avoid the full checker'
        full.append(candidate.ballast.tank_masses_kg)
        return CheckReport(passed=False)
    monkeypatch.setattr(stowage.checker, 'check', veto)
    plan = MovePlan(id='large-arrival-first', instance_id=instance.id, call_index=0)
    result = ballast_v4.finish(instance, plan, time_limit_s=1.)
    assert seen[:3] == [(0.5, 0.5)]*3  # onboard, not the replayed empty tanks
    assert result.status == 'not_found' and result.plan is None
    assert result.diagnostics['candidate_count'] == 3  # inherited, replay, full
    record = result.diagnostics['seed_outcomes'][0]
    assert record['label'] == 'inherited' and record['method'] == 'exact_screen'
    assert record['full_checker_passed'] is (False if cheap_passes else None)
    if cheap_passes:
        assert full[0] == {'WB-P':51250., 'WB-S':51250.}
    else:
        assert not full and result.diagnostics['full_checker_calls'] == 0


def prepare_search(monkeypatch, *, absent_prefixes=False):
    import stowage.checker
    import stowage.solver.ballast as legacy
    import stowage.solver.ballast_proof as proof
    import stowage.solver.ballast_v4_evaluator as module
    fixture = build_instance()
    if not absent_prefixes:
        # An APT pair plus one tank for each forward group makes all nine
        # recipe vectors distinct without invoking real physics.
        template = next(t for t in fixture.vessel.tanks if t.adjustable)
        fixture.vessel.tanks = [template.model_copy(update={'id':name})
            for name in ('APT-P', 'WING-A-P', 'FPT-P', 'FWD-DEEP-P', 'DB-F-P')]
        fixture.onboard.tank_masses_kg = {t.id:51250. for t in fixture.vessel.tanks}
    events = []
    monkeypatch.setattr(legacy, '_validated_replay', lambda *args: SimpleNamespace(
        final_state=SimpleNamespace(ship=fixture.onboard)))
    monkeypatch.setattr(stowage.checker, 'check', lambda *args: CheckReport(passed=False))
    monkeypatch.setattr(proof, 'prove_infeasible', lambda *args: None)
    def old_seeds(*args):
        events.append('old_portfolio')
        return []
    monkeypatch.setattr(legacy, '_seed_masses', old_seeds)

    class Evaluator:
        def __init__(self, instance, plan, ship, deadline, diagnostics):
            self.tanks = sorted((t for t in instance.vessel.tanks if t.adjustable), key=lambda t:t.id)
            self.capacity = np.array([t.capacity_m3*t.content_density_kg_m3 for t in self.tanks])
            self.initial = np.array([ship.tank_masses_kg.get(t.id,0.) for t in self.tanks])/self.capacity
            self.candidate_identities, self.recipe_identities = set(), set()
            self.best_parts, self.contexts, self.measured = {}, [], {}
            self.accepted = None
        def checkpoint(self):
            pass
        def fractions(self, values):
            return np.asarray(values, dtype=float).copy()
        def evaluate(self, values):
            events.append(tuple(values))
            if 'old_portfolio' in events:
                raise Deadline
            return {'stability/trim_m':-1.}
    monkeypatch.setattr(module, 'Evaluator', Evaluator)
    plan = MovePlan(id='ordered-grid', instance_id=fixture.id, call_index=0)
    return fixture, plan, events


def test_nine_exact_screened_recipes_precede_old_portfolio_after_full_veto(monkeypatch):
    instance, plan, events = prepare_search(monkeypatch)
    result = ballast_v4.finish(instance, plan, time_limit_s=1.)
    assert result.status == 'not_found' and result.plan is None
    recipes = trim_seed_recipes(instance)
    tanks = sorted(instance.vessel.tanks, key=lambda t:t.id)
    expected = [tuple(recipe.tank_masses_kg[t.id]/(t.capacity_m3*t.content_density_kg_m3)
                      for t in tanks) for recipe in recipes[1:]]
    assert events[:9] == expected and events[9] == 'old_portfolio'
    assert result.diagnostics['seed_outcomes'][0]['full_checker_passed'] is False
    assert [r['label'] for r in result.diagnostics['seed_outcomes']] == [r.label for r in recipes]
    assert all(r['full_checker_passed'] is None for r in result.diagnostics['seed_outcomes'][1:])


def test_absent_prefix_grid_labels_are_deduplicated_without_extra_evaluations(monkeypatch):
    instance, plan, events = prepare_search(monkeypatch, absent_prefixes=True)
    result = ballast_v4.finish(instance, plan, time_limit_s=1.)
    assert events[0] == 'old_portfolio'
    records = result.diagnostics['seed_outcomes']
    assert len(records) == 10
    assert all(r['status'] == 'duplicate' and r['duplicate_of'] == 'inherited' for r in records[1:])
    assert result.diagnostics['candidate_count'] == 1


def test_deadline_between_grid_recipes_prevents_later_proposals(monkeypatch):
    instance, plan, events = prepare_search(monkeypatch)
    import stowage.solver.ballast_v4_evaluator as module
    original = module.Evaluator.checkpoint
    calls = []
    def checkpoint(self):
        calls.append(1)
        if len(calls) == 2:
            raise Deadline
        original(self)
    monkeypatch.setattr(module.Evaluator, 'checkpoint', checkpoint)
    result = ballast_v4.finish(instance, plan, time_limit_s=1.)
    assert result.status == 'not_found' and len(events) == 1
    assert 'old_portfolio' not in events


@pytest.mark.parametrize('load_count', [1, 17])
def test_grid_twelve_passing_parts_still_obey_independent_full_checker_veto(monkeypatch, load_count):
    import stowage.checker
    import stowage.solver.ballast_v4_evaluator as module
    import stowage.solver.ballast_pressed as pressed
    import stowage.solver.ballast_successive as successive
    actual_evaluator = module.Evaluator
    instance, plan, _ = prepare_search(monkeypatch)
    instance.load_list = [instance.load_list[0]]*load_count
    monkeypatch.setattr(module, 'Evaluator', actual_evaluator)
    monkeypatch.setattr(module, 'conditions', lambda inst, ship:
        [(phase, inst, ship) for phase in ('departure', 'mid_voyage', 'arrival')])
    class Context:
        def __init__(self, inst, ship, deadline):
            self.tanks = sorted(inst.vessel.tanks, key=lambda t:t.id)
        def margins(self, fill, active_keys=None):
            return {'stability/gm_m':1.}
    monkeypatch.setattr(module, 'ExactContext', Context)
    monkeypatch.setattr(module, 'checkers', lambda: {
        name:lambda *args, **kwargs: PartReport(name='synthetic_part', margins={'clearance':1.})
        for name in ('stability', 'strength', 'visibility', 'lashing')})
    full = []
    def veto(inst, candidate):
        full.append(candidate)
        return CheckReport(passed=False)
    monkeypatch.setattr(stowage.checker, 'check', veto)
    monkeypatch.setattr(pressed, 'search', lambda *args: [])
    monkeypatch.setattr(successive, 'search', lambda *args, **kwargs: None)
    result = ballast_v4.finish(instance, plan, time_limit_s=1.)
    assert len(full) >= 10
    assert result.status == 'not_found' and result.plan is None
    assert result.not_found_cause == 'search:bounded_exhaustion'
    assert result.diagnostics['full_checker_passed'] is False
    assert all(record['full_checker_passed'] is False
               for record in result.diagnostics['seed_outcomes'])
