"""Expander private replay preserves dispatch, lift order and atomic rejection."""
import pytest

import stowage.actions.expander as module
from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.frozen import FROZEN, stub_override
from stowage.model.moves import Load
from stowage.moves import apply_move
from stowage.rules.catalogue import rule_freeze_v1


def trial_pair():
    instance = tiny_instance(61, n_load=1, n_other=1, p40=0)
    ids = [box.id for box in instance.containers]
    from stowage.model.yard import YardStack
    instance.yard.stacks = [YardStack(key='A-001-01', length_ft=20, boxes=ids)]
    ex = Expander(instance)
    trial = ex.copy()
    trial._relocate(ids[1], 'A-002-01')
    trial._place(ids[0], '0010182')
    return ex, trial, list(trial.moves)


def replay_counter(monkeypatch):
    import stowage.moves._physical as physical
    original = physical.Replay
    seen = dict(construct=0, state=0, moves=[])

    class Counted(original):
        def __init__(self, *args, **kwargs):
            seen['construct'] += 1
            super().__init__(*args, **kwargs)

        def move(self, move, *args, **kwargs):
            seen['moves'].append(move)
            return super().move(move, *args, **kwargs)

        def state(self):
            seen['state'] += 1
            return super().state()

    monkeypatch.setattr(physical, 'Replay', Counted)
    return seen


def test_expander_commit_reuses_one_replay_without_discarded_worlds(monkeypatch):
    with rule_freeze_v1():
        ex, trial, moves = trial_pair()
        expected = ex.world_state()
        for move in moves:
            expected = apply_move(ex.instance, expected, move)
        seen = replay_counter(monkeypatch)
        assert ex._validated_commit(trial, moves) == moves
        assert seen == dict(construct=1, state=0, moves=moves)
        assert ex.world_state() == expected


@pytest.mark.parametrize('dispatch', ['imported_alias', 'public_alias', 'frozen_override'])
def test_expander_commit_preserves_public_dispatch(monkeypatch, dispatch):
    from contextlib import nullcontext
    import stowage.moves as public
    with rule_freeze_v1():
        ex, trial, moves = trial_pair()
        calls = []

        def observed(*args, **kwargs):
            calls.append(args[2])
            return FROZEN['moves.apply_move'].func(*args, **kwargs)

        context = nullcontext()
        if dispatch == 'imported_alias':
            monkeypatch.setattr(module, 'apply_move', observed)
        elif dispatch == 'public_alias':
            # Public replacements are authoritative even after this module
            # captured its imported alias.
            monkeypatch.setattr(public, 'apply_move', observed)
        else:
            context = stub_override('moves.apply_move', observed, reason='transaction dispatch regression')
        seen = replay_counter(monkeypatch)
        with context:
            ex._validated_commit(trial, moves)
        assert seen == dict(construct=len(moves), state=len(moves), moves=moves)
        assert calls == moves


def test_expander_commit_rejection_is_atomic_and_wraps_illegal_move(monkeypatch):
    with rule_freeze_v1():
        ex, trial, moves = trial_pair()
        before = ex.world_state()
        # The second lift is unsupported. The first relocation must remain
        # private even though it was individually valid.
        bad = Load(call_index=ex.call, container_id=moves[-1].container_id,
                   src=moves[-1].src, dst='0010184')
        with pytest.raises(ValueError, match='unexpandable: expert.moves.bottom_up'):
            ex._validated_commit(trial, [moves[0], bad])
        assert ex.world_state() == before and ex.moves == []
