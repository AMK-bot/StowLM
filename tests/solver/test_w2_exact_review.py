"""Review reproductions: real expansions, controlled sequence-sensitive validation.

These tests isolate cache/certificate handling; they do not claim full-physics
acceptance for their intentionally controlled feasibility/checker doubles.
"""
from unittest.mock import patch
from types import SimpleNamespace

import pytest

from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.model import BallastCondition
from stowage.model.yard import YardStack
from stowage.moves import apply_plan
from stowage.rules.catalogue import rule_freeze_v1
from stowage.solver import exact
from stowage.solver.exact_engine import solve_engine


def _two_orderings():
    instance = tiny_instance(71, n_load=2, n_other=0, p40=0)
    for box in instance.containers:
        box.gross_mass_kg = 10000
        box.dg = box.oog = None
        box.special_stowage = []
        box.pod = 'THBKK'
    a, b = instance.load_list
    instance.yard.stacks = [YardStack(key='A-001-01', length_ft=20, boxes=[a]),
                            YardStack(key='A-002-01', length_ft=20, boxes=[b])]
    slots = {a: '0010182', b: '0030182'}

    class Engine:
        def __init__(self, reverse=False):
            self.done, self.reverse = [], reverse

        def copy(self):
            child = Engine(self.reverse)
            child.done = list(self.done)
            return child

        def is_complete(self):
            return len(self.done) == 2

        def legal_actions(self):
            return [Place(c, slots[c]) for c in ((b, a) if self.reverse else (a, b))
                    if c not in self.done]

        def push(self, action):
            assert action in self.legal_actions()
            self.done.append(action.box)

        def dead_end(self):
            return None

    return instance, Engine, a, b, slots


@pytest.mark.parametrize('first_verdict', ['unknown', 'no'])
@pytest.mark.parametrize('reverse', [False, True])
def test_terminal_rejection_keeps_semantic_action_history(first_verdict, reverse):
    instance, Engine, a, b, slots = _two_orderings()
    checked = []

    def terminal(instance, plan, ship, **kwargs):
        assert apply_plan(instance, plan).legal
        order = tuple(m.container_id for m in plan.moves if m.kind == 'load')
        checked.append(order)
        return ('yes' if order == (b, a) else first_verdict,
                BallastCondition(source='planner', tank_masses_kg={}), plan.provenance)

    with rule_freeze_v1(), patch(
        'stowage.solver.exact_engine.make_feasibility_engine',
        lambda instance, space: Engine(reverse)
    ), patch('stowage.solver.exact._validate_terminal', terminal):
        result = solve_engine(instance, time_limit_s=1, max_nodes=20, seed=0, num_workers=1)

    assert result.status == 'OPTIMAL'
    assert result.plan is not None
    assert tuple(m.container_id for m in result.plan.moves if m.kind == 'load') == (b, a)
    assert checked == ([(b, a)] if reverse else [(a, b), (b, a)])
    # The two real expansions reach exactly the same physical state.
    space = get_action_space()
    forward = apply_plan(instance, space.expand(instance, [Place(a, slots[a]), Place(b, slots[b])]))
    backward = apply_plan(instance, space.expand(instance, [Place(b, slots[b]), Place(a, slots[a])]))
    assert forward.legal and backward.legal
    assert forward.final_state == backward.final_state


def test_unknown_is_retried_for_seed_and_search_candidate():
    # Empty manifest makes the seed and the original frontier root distinct
    # candidate visits with the very same physical and semantic-history key.
    instance = tiny_instance(71, n_load=0, n_other=0)

    class CompletedEngine:
        def copy(self):
            return self

        def is_complete(self):
            return True

    checked = []

    def terminal(instance, plan, ship, **kwargs):
        assert apply_plan(instance, plan).legal
        checked.append(tuple(m.container_id for m in plan.moves if m.kind == 'load'))
        return ('unknown' if len(checked) == 1 else 'yes',
                BallastCondition(source='planner', tank_masses_kg={}), plan.provenance)

    with rule_freeze_v1(), patch(
        'stowage.solver.exact_engine.make_feasibility_engine', lambda instance, space: CompletedEngine()
    ), patch('stowage.solver.exact._validate_terminal', terminal):
        result = solve_engine(instance, time_limit_s=3, max_nodes=100, seed=0, num_workers=1)

    assert result.metrics['incumbent_seed_complete']
    assert result.plan is not None
    assert result.status == 'OPTIMAL'
    assert checked == [(), ()]


@pytest.mark.parametrize('status,checker_passed,label', [
    ('OPTIMAL', True, 'optimal for the move model; checker-passed: yes'),
    ('OPTIMAL', False, 'optimal for the move model; checker-passed: no'),
    ('FEASIBLE', True, 'optimality unproved for the move model; checker-passed: yes'),
    ('TIME_LIMIT', False, 'optimality unproved for the move model; checker-passed: no'),
])
def test_exact_result_labels_move_optimality_and_checker_separately(monkeypatch, status, checker_passed, label):
    instance = tiny_instance(71, n_load=0, n_other=0)
    result = exact.ExactSearchResult(status, None, None, 0., None, 0, 0.,
                                     {'checker_passed': checker_passed})
    monkeypatch.setattr(exact, 'solve_exact', lambda *args, **kwargs: result)
    planned = exact.ExactPlanner().plan(instance)
    assert planned.metrics['optimality_label'] == label
    assert label in planned.message


def test_exact_error_result_does_not_claim_optimality_or_checker_pass(monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError('controlled unavailable engine')

    monkeypatch.setattr(exact, 'solve_exact', unavailable)
    planned = exact.ExactPlanner().plan(tiny_instance(71, n_load=0, n_other=0))
    assert planned.status == 'error'
    label = 'optimality unproved for the move model; checker-passed: no'
    assert planned.metrics['optimality_label'] == label
    assert label in planned.message
    assert 'ValueError: controlled unavailable engine' in planned.message


@pytest.mark.parametrize('mode,checker_passed', [
    ('worker', True), ('worker', False),
    ('finished', True), ('finished', False),
    ('checkpoint_timeout', True), ('checkpoint_timeout', False),
    ('initialization_timeout', False),
])
def test_public_exact_result_always_labels_scope(monkeypatch, mode, checker_passed):
    instance = tiny_instance(71, n_load=0, n_other=0)
    worker_result = exact.ExactSearchResult('OPTIMAL', None, None, 0., None, 0, 0.,
                                          {'checker_passed': checker_passed})
    monkeypatch.setattr(exact, 'in_worker', lambda: mode == 'worker')
    monkeypatch.setattr(exact, '_solve_exact', lambda *args, **kwargs: worker_result)
    expired = mode in ('checkpoint_timeout', 'initialization_timeout')
    monkeypatch.setattr(exact, 'run_with_deadline', lambda *args, **kwargs: SimpleNamespace(
        timed_out=expired,
        checkpoint=None if mode == 'initialization_timeout' else worker_result,
        value=worker_result, worker_pid=123))

    result = exact.solve_exact(instance, time_limit_s=1.)

    optimality = ('optimality unproved for the move model' if expired
                  else 'optimal for the move model')
    checked = 'yes' if checker_passed else 'no'
    assert result.metrics['optimality_label'] == f'{optimality}; checker-passed: {checked}'
    assert result.status == ('TIME_LIMIT' if expired else 'OPTIMAL')
