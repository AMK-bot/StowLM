"""Path-sensitive dominance regressions; isolated D64 acceptance, not canary certification."""
import random
from time import monotonic

import pytest

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import PartReport, Violation
from stowage.model.yard import YardStack
from stowage.moves import apply_move, apply_plan, initial_state
from stowage.rules.catalogue import rule_freeze_v1
from stowage.solver import scalable


def sequence_fixture():
    instance = tiny_instance(971, n_load=2, n_other=0, p40=0)
    a, b = instance.load_list
    for box in instance.containers:
        box.gross_mass_kg = 10000
        box.pod = 'THBKK'
    instance.yard.stacks = [YardStack(key='A-001-01', length_ft=20, boxes=[a]),
                            YardStack(key='A-002-01', length_ft=20, boxes=[b])]
    return instance, (Place(a, '0010182'), Place(b, '0030182'))


def test_native_beam_retains_both_orders_for_sequence_sensitive_checker(monkeypatch):
    """Native mask/engine replay plus full-checker dispatch, with a D64 test double.

    The numerical in-port part rejects the prefix with A alone. This controlled
    acceptance predicate isolates path-sensitive dominance; other full-checker
    failures are retained, and no physical approval certificate is asserted.
    """
    import stowage.checker.full as full
    from stowage.checker import check

    instance, actions = sequence_fixture()
    a, b = instance.load_list

    def in_port(inst, plan, ship):
        state = initial_state(inst)
        problems = []
        for index, move in enumerate(plan.moves):
            state = apply_move(inst, state, move)
            if {p.container_id for p in state.ship.placements} == {a}:
                problems.append(Violation(rule_id='port.stability.cargo_operations',
                    severity='hard', call_index=inst.call_index, move_index=index,
                    message='Controlled D64 acceptance: A alone fails the lift condition.'))
        return PartReport(name='in_port', violations=problems)

    monkeypatch.setattr(full, '_in_port', in_port)
    monkeypatch.setattr(scalable, '_candidates', lambda inst, node, *args:
                        (action for action in actions if node.ex.where[action.box][0] != 'ship'))
    monkeypatch.setattr(scalable, '_action_rank', lambda inst, node, action, pods:
                        (actions.index(action), action.slot))
    cfg = scalable.ScalableConfig(beam_width=2, branch_factor=2, early_beam_width=2,
                                 backtrack_limit=2, max_restarts=0, max_neighborhoods=0)
    metrics = dict(candidate_probes=0, legal_candidates=0, search_dead_ends=0, backtracks=0)
    with rule_freeze_v1(enabled=True):
        engine = make_feasibility_engine(instance, get_action_space(),
                         config=FeasibilityConfig(extra={'global_screen': 0}))
        root = scalable._Node(engine, Expander(instance))
        completed, _ = scalable._construct_attempt(instance, root, [], cfg,
            monotonic()+5., random.Random(0), metrics, scalable._pod_map(instance))
        reports = []
        for node in completed:
            plan = node.engine.to_plan()
            assert apply_plan(instance, plan).legal
            reports.append(check(instance, plan))
        assert any(report.parts['in_port'].violations for report in reports)
        assert any(not report.parts['in_port'].violations for report in reports), (
            'Dominance discarded the only ordering that the in-port checker accepts')
        assert {tuple(action.box for action in node.engine.actions()) for node in completed} == {
            (a, b), (b, a)}


@pytest.mark.parametrize('enabled', [False, True])
def test_history_key_activation_and_bounded_cache(enabled):
    instance, (a, b) = sequence_fixture()
    left, right = Expander(instance), Expander(instance)
    for action in (a, b):
        left.step(action)
    for action in (b, a):
        right.step(action)
    assert left.occ == right.occ
    seen, metrics = {}, {}
    with rule_freeze_v1(enabled=enabled):
        assert (scalable._state_key(left) != scalable._state_key(right)) is enabled
        assert scalable._admit_state(left, 0, seen, 1, metrics)
        assert scalable._admit_state(right, 0, seen, 1, metrics) is enabled
        assert not scalable._admit_state(right.copy(), 0, seen, 1, metrics)
    assert len(seen) == metrics['dominance_cache_peak'] == 1
    assert metrics.get('dominance_evictions', 0) == int(enabled)
