"""Replay every emitted lift, without a move-engine prefilter on candidate actions."""
from __future__ import annotations

from collections import Counter
import hashlib
import random

import pytest

from stowage.actions.expander import Expander
from stowage.actions.samples import legal_slots, random_actions, tiny_instance
from stowage.actions.space import Discharge, Place, Relocate, RestowOff
from stowage.moves import apply_move, initial_state
from stowage.rules.catalogue import rule_freeze_v1


def _candidates(instance, ex):
    for box in instance.load_list:
        if ex.location(box) is not None and ex.location(box)[0] == "yard":
            for slot in legal_slots(instance, ex, box):
                yield Place(box, slot)
    for box in ex.buffer:
        for slot in legal_slots(instance, ex, box):
            yield Place(box, slot)
    for box in instance.discharge_list:
        if ex.location(box) is not None and ex.location(box)[0] == "ship":
            yield Discharge(box)
    for box, location in sorted(ex.where.items()):
        if location[0] == "ship":
            yield RestowOff(box)
    for source, stack in sorted(ex.yard.items()):
        block = ex.blocks[source.split("-")[0]]
        for bay in range(1, block.n_bays + 1):
            for row in range(1, block.n_rows + 1):
                target = f"{block.id}-{bay:03d}-{row:02d}"
                if target != source:
                    yield Relocate(stack[-1], target)


def _snapshot(ex):
    return (repr(ex.yard), repr(ex.ylen), repr(ex.foot), repr(ex.where), repr(ex.pending),
            repr(ex.occ), repr(ex.buffer), repr(ex.covers_off), repr(ex.moves))


@pytest.mark.parametrize("n_load,n_other", [(4, 3), (10, 8)], ids=["tiny", "small"])
@pytest.mark.parametrize("layover", [False, True], ids=["stage_a", "stage_b"])
def test_seeded_active_expander_engine_agreement(n_load, n_other, layover, record_property):
    accepted, rejected, emitted = Counter(), Counter(), Counter()
    with rule_freeze_v1():
        for seed in (2, 7):
            instance = tiny_instance(seed, n_load=n_load, n_other=n_other, layover=layover, max_tiers=2)
            instance.port.max_crane_height_tiers = 2
            ex, state = Expander(instance), initial_state(instance)
            rng = random.Random(seed)
            # Branch across every candidate at each prefix; advance only after
            # checking all siblings, so a rejection never hides illegal output.
            for depth in range(3):
                advances = []
                for action in _candidates(instance, ex):
                    trial = ex.copy()
                    before = _snapshot(trial)
                    try:
                        moves = trial.step(action)
                    except ValueError as error:
                        rejected[f"{type(action).__name__}: {error}"] += 1
                        assert _snapshot(trial) == before, (seed, depth, action, "partial mutation")
                        continue
                    assert moves, (seed, depth, action)
                    replayed = state
                    for move in moves:
                        # This is deliberately after step, never a filter.
                        replayed = apply_move(instance, replayed, move)
                        emitted[move.kind] += 1
                    assert trial.world_state() == replayed, (seed, depth, action)
                    accepted[type(action).__name__] += 1
                    advances.append((action, trial, replayed))
                assert advances, (seed, depth, rejected)
                productive = [entry for entry in advances if isinstance(entry[0], Discharge)]
                productive = productive or [entry for entry in advances if isinstance(entry[0], Place)]
                _, ex, state = rng.choice(productive or advances)
    record_property("accepted_action_types", dict(accepted))
    record_property("emitted_move_kinds", dict(emitted))
    record_property("rejected_candidates", dict(rejected))
    assert accepted["Place"] > 0 and accepted["Relocate"] > 0
    assert rejected, "the crossing/physical mask boundary must be exercised"
    assert emitted["load"] > 0 and emitted["relocate"] > 0
    if layover:
        assert accepted["Discharge"] > 0 and accepted["RestowOff"] > 0
        assert emitted["discharge"] > 0 and emitted["restow_off"] > 0


@pytest.mark.parametrize("layover", [False, True])
def test_switch_off_seeded_legacy_moves_are_byte_identical(layover):
    serialised = []
    with rule_freeze_v1(enabled=False):
        for seed in (2, 7):
            instance = tiny_instance(seed, n_load=4, n_other=3, layover=layover)
            ex, state = Expander(instance), initial_state(instance)
            for action in random_actions(instance, seed):
                moves = ex.step(action)
                for move in moves:
                    state = apply_move(instance, state, move)
                serialised.extend(move.model_dump_json() for move in moves)
            for move in ex.finish():
                state = apply_move(instance, state, move)
                serialised.append(move.model_dump_json())
    digest = hashlib.sha256("\n".join(serialised).encode()).hexdigest()
    assert digest == LEGACY_DIGESTS[layover]


# Captured from the pre-wave expander with the activation switch off. Keep the
# exact lift sequence stable, including lazy cover timing and yard tie breaks.
LEGACY_DIGESTS = {
    False: "bc3c4a4cc4c88c68cab00eb04dbbe699f77ff28c646c083467d0a4cd04e33a4f",
    True: "4c2b282f5faea13143af1b908beafb333572e417ec8e546e2f3bcfcd77fbc216",
}
