"""The exact search prunes only irreversible securing geometry.

Synthetic engine/checker doubles test search integration, not ship approval.
The shared screen's numerical comparison against the real checker is tested
in test_securing_geometry_1028.py.
"""
from __future__ import annotations

from copy import copy
from fractions import Fraction
from multiprocessing import get_context
from time import monotonic

from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.model import BallastCondition, Instance
from stowage.model.fixtures import build_instance
from stowage.moves import initial_state


def supported_long(*, deck=False, unequal=False):
    data = build_instance().model_dump()
    boxes = data['containers'][:3]
    for box in boxes:
        box.update(dg=None, oog=None, special_stowage=[], pod='THBKK', gross_mass_kg=8000.)
    if unequal:
        boxes[2].update(iso_type='25G1', height_m=2.896)
    data.update(containers=boxes, load_list=[boxes[0]['id']], discharge_list=[])
    tier = '82' if deck else '02'
    data['onboard']['placements'] = [
        {'container_id': box['id'], 'slot': f'{bay}01{tier}'}
        for box, bay in zip(boxes[1:], ('001', '003'))]
    data['yard']['stacks'] = [{'key': 'A-001-01', 'length_ft': 40, 'boxes': data['load_list']}]
    return Instance.model_validate(data)


class ChoicesEngine:
    def __init__(self, instance, slots, hold_pushes):
        self.instance, self.slots, self.hold_pushes = instance, slots, hold_pushes
        self.ex = Expander(instance)

    def copy(self):
        result = copy(self)
        result.ex = self.ex.copy()
        return result

    def is_complete(self):
        return self.ex.where[self.instance.load_list[0]][0] == 'ship'

    def legal_actions(self):
        return [] if self.is_complete() else [Place(self.instance.load_list[0], slot) for slot in self.slots]

    def push(self, action):
        if action.slot == '0020104':
            self.hold_pushes.value += 1
        self.ex.step(action)

    def dead_end(self):
        return None


def test_exact_prunes_irreversible_mixed_hold_and_keeps_checked_alternative(monkeypatch):
    import stowage.solver.exact as exact
    import stowage.solver.exact_engine as engine
    instance = supported_long()
    pushes = get_context('fork').Value('i', 0)
    monkeypatch.setattr(engine, 'make_feasibility_engine',
        lambda inst, space: ChoicesEngine(inst, ('0020104', '0020182'), pushes))
    monkeypatch.setattr(exact, '_validate_terminal', lambda inst, plan, ship, **kw:
        ('yes', BallastCondition(source='planner', tank_masses_kg={}), plan.provenance))
    result = exact.solve_exact(instance, time_limit_s=1., max_nodes=20)
    assert result.plan is not None and result.plan.actions[0]['slot'] == '0020182'
    assert result.status == 'OPTIMAL' and result.metrics['checker_passed']
    assert pushes.value == 0
    assert result.metrics['securing_geometry_rejections'] >= 1


def test_greedy_seed_applies_the_same_irreversible_screen():
    from stowage.solver.exact_engine import _greedy_seed
    instance = supported_long()
    pushes = get_context('fork').Value('i', 0)
    metrics = {}
    result = _greedy_seed(instance, ChoicesEngine(instance, ('0020104', '0020182'), pushes),
        Expander(instance), initial_state(instance), deadline=monotonic()+1.,
        weights={'load': Fraction(0)}, metrics=metrics)
    assert result is not None and result[-1] == (Place(instance.load_list[0], '0020182'),)
    assert pushes.value == 0 and metrics['securing_geometry_rejections'] == 1


def test_terminal_screen_rejects_deferred_deck_geometry_before_ballast(monkeypatch):
    import stowage.solver.exact as exact
    import stowage.solver.exact_engine as engine
    instance = supported_long(deck=True, unequal=True)
    pushes = get_context('fork').Value('i', 0)
    monkeypatch.setattr(engine, 'make_feasibility_engine',
        lambda inst, space: ChoicesEngine(inst, ('0020184',), pushes))
    def must_not_finish(*args, **kwargs):
        raise AssertionError('No ballast can repair unequal physical support heights')
    monkeypatch.setattr(exact, '_validate_terminal', must_not_finish)
    result = exact.solve_exact(instance, time_limit_s=1., max_nodes=20)
    assert result.plan is None and result.status == 'INFEASIBLE'
    assert result.metrics['securing_geometry_rejections'] == 1
    assert 'paired cargo supports must have equal actual height' in result.metrics['securing_geometry_rejection_reasons']
