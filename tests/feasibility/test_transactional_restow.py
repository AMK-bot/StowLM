"""Private hatch-prefix replay keeps dispatch and every-lift D64 semantics."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.frozen import FROZEN, stub_override
from stowage.model.ship import Placement
from stowage.model.yard import YardStack
from stowage.rules.catalogue import rule_freeze_v1


def case(monkeypatch):
    instance = tiny_instance(67, n_load=1, n_other=2, p40=0)
    ids = [box.id for box in instance.containers]
    instance.yard.stacks = [YardStack(key='A-001-01', length_ft=20, boxes=[ids[0]])]
    instance.onboard.placements = [Placement(container_id=ids[1], slot='0010182'),
                                   Placement(container_id=ids[2], slot='0010184')]
    engine = make_feasibility_engine(instance, get_action_space(),
                                     config=FeasibilityConfig(extra={'global_screen': 0}))
    b, s = engine.T.box_index[ids[0]], engine.T.slot_index['0010102']

    def evaluate(state, *, allowed_boxes, allowed_slots, include_discharges):
        # Isolate the hatch-prefix replay from unrelated child candidate replay.
        state.mask = np.array([int(x) * len(engine.T.slot_ids) + int(y)
                               for x in allowed_boxes for y in allowed_slots], dtype=np.int64)

    monkeypatch.setattr(engine, '_evaluate', evaluate)
    return engine, np.array([b]), np.array([s])


def counter(monkeypatch):
    import stowage.moves._physical as physical
    original = physical.Replay
    counts = dict(construct=0, states=0, moves=[])

    class Counted(original):
        def __init__(self, *args, **kwargs):
            counts['construct'] += 1
            super().__init__(*args, **kwargs)

        def move(self, move, *args, **kwargs):
            counts['moves'].append(move)
            return super().move(move, *args, **kwargs)

        def state(self):
            counts['states'] += 1
            return super().state()

    monkeypatch.setattr(physical, 'Replay', Counted)
    return counts


@pytest.mark.parametrize('enabled', [False, True])
def test_feasibility_restow_reuses_one_private_replay(monkeypatch, enabled):
    with rule_freeze_v1(enabled=enabled):
        engine, boxes, slots = case(monkeypatch)
        before = engine._cur.ex.world_state()
        counts = counter(monkeypatch)
        assert engine._restow_places(engine._cur, boxes, slots) == [(int(boxes[0]), int(slots[0]))]
        assert counts['construct'] == 1 and counts['states'] == 0
        assert [m.kind for m in counts['moves']] == ['restow_off', 'restow_off', 'hatch_cover_off']
        assert engine._cur.ex.world_state() == before


@pytest.mark.parametrize('dispatch', ['public_alias', 'frozen_override'])
def test_feasibility_restow_preserves_public_dispatch(monkeypatch, dispatch):
    from contextlib import nullcontext
    import stowage.moves as moves
    with rule_freeze_v1():
        engine, boxes, slots = case(monkeypatch)
        called = []

        def observed(*args, **kwargs):
            called.append(args[2])
            return FROZEN['moves.apply_move'].func(*args, **kwargs)

        context = nullcontext()
        if dispatch == 'public_alias':
            monkeypatch.setattr(moves, 'apply_move', observed)
        else:
            context = stub_override('moves.apply_move', observed, reason='restow dispatch regression')
        counts = counter(monkeypatch)
        with context:
            assert engine._restow_places(engine._cur, boxes, slots)
        assert called == counts['moves'] and len(called) == 3
        assert counts['construct'] == counts['states'] == 3


@pytest.mark.parametrize('reject_after', [None, 2])
def test_feasibility_restow_d64_evaluates_each_lift_in_order(monkeypatch, reject_after):
    import stowage.feasibility.in_port as in_port
    observed = []

    def evaluate(ship):
        observed.append((len(ship.placements), dict(ship.covers_off)))
        return SimpleNamespace(status='reject' if len(observed) == reject_after else 'feasible',
                               rule_id='port.stability.cargo_operations')

    with rule_freeze_v1():
        engine, boxes, slots = case(monkeypatch)
        monkeypatch.setattr(in_port, 'in_port_screen_enabled', lambda: True)
        monkeypatch.setattr(in_port, 'InPortPrefixScreen',
                            lambda instance, ship: SimpleNamespace(evaluate=evaluate))
        before = engine._cur.ex.world_state()
        counts = counter(monkeypatch)
        result = engine._restow_places(engine._cur, boxes, slots)
        expected = [(1, {}), (0, {})]
        if reject_after is None:
            expected.append((0, {'HC-02-1': 'quay'}))
        assert observed == expected
        assert bool(result) == (reject_after is None)
        assert counts['construct'] == 1 and counts['states'] == len(expected)
        assert engine._cur.ex.world_state() == before
