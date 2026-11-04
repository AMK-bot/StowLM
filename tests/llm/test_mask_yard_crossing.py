"""Binding A8 mask witnesses: yard height five is independent of quay H eight.

Native geometry only: these prove local move/screen agreement, not a full
physics-checker completion. C2 remains off until final requalification.
"""
from __future__ import annotations

import pytest

from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.mask import MaskConfig, MaskedDecoder
from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import Instance
from stowage.model.moves import Load, Relocate
from stowage.model.yard import YardStack
from stowage.moves import IllegalMoveError, apply_move, initial_state
from stowage.ports import load_port, yard_blocking_tiers
from stowage.rules.catalogue import rule_freeze_v1


SPACE = get_action_space("boxslot")
NO_C2 = FeasibilityConfig(extra={"global_screen": 0})


@pytest.fixture(scope="module")
def tokenizer():
    return load_tokenizer()


def _case(length=20, lane="low", *, full=False):
    instance = tiny_instance(37, n_load=1, n_other=9 if full else 5, p40=0, max_tiers=5)
    instance.port = load_port("laem_chabang_like")
    # Fix both independent limits even when profile loading uses legacy rules.
    instance.port.max_crane_height_tiers = 8
    instance.port.quay_crane.max_on_deck_tiers = 8
    instance.port.yard_max_tiers = 5
    for equipment in instance.port.equipment:
        equipment.stack_tiers = 5
    instance.port.yard.blocks = instance.port.yard.blocks[:1]
    block = instance.port.yard.blocks[0]
    block.max_tiers = 5
    block.n_bays, block.n_rows = (1 if full else 4), 2
    block.reefer_rows, block.truck_lane_side = [], lane
    ids = [box.id for box in instance.containers]
    # Avoid the unrelated legacy E6 heavy-on-deck hard gate in this witness.
    for box in instance.containers:
        box.gross_mass_kg = 9000
    instance.containers[0].length_ft = length
    instance.containers[0].iso_type = {20: "22G1", 40: "42G1", 45: "L2G1"}[length]
    source_row, lane_row = (2, 1) if lane == "low" else (1, 2)
    source = f"{block.id}-001-{source_row:02d}"
    # For long boxes, the obstacle is in the secondary 20-foot sub-bay.
    obstacle = f"{block.id}-{1 if length == 20 else 2:03d}-{lane_row:02d}"
    instance.yard.yard_id = instance.port.yard.id
    instance.yard.stacks = [
        YardStack(key=source, length_ft=length,
                  boxes=ids[1:5] + [ids[0]] if full else ids[:1]),
        YardStack(key=obstacle, length_ft=20, boxes=ids[5:] if full else ids[1:]),
    ]
    instance = Instance.model_validate(instance.model_dump())
    assert instance.port.max_crane_height_tiers == 8
    assert yard_blocking_tiers(instance.port, instance.port.yard.blocks[0]) == 5
    return instance, ids, source, obstacle


def _targets(box, length):
    # Exhaustive independent domain: every empty ship stack's supported base.
    bays = (1, 3) if length == 20 else (2,)
    return tuple(Place(box, f"{bay:03d}{row:02d}{tier:02d}")
                 for bay in bays for row in (1, 2) for tier in (2, 82))


def _assert_mask_domain(instance, tokenizer, expected):
    engine = make_feasibility_engine(instance, SPACE, config=NO_C2)
    assert engine.legal_actions() == expected
    # A fresh engine keeps the real decoder's lazy box/slot path independent
    # of the eager reference enumeration above.
    decoder = MaskedDecoder(make_feasibility_engine(instance, SPACE, config=NO_C2),
                            ActionCodec(tokenizer, [box.id for box in instance.containers]),
                            MaskConfig(lookahead=0))
    codec = decoder.codec
    trie = codec.trie(expected, allow_end=False)
    if not expected:
        assert decoder.allowed_token_ids() == ()
        assert decoder.status == "dead_end"
        return
    for action in expected:
        prefix = []
        for token in codec.encode_action(action):
            assert decoder.allowed_token_ids() == tuple(sorted(trie.allowed_next_token_ids(prefix)))
            decoder.advance(token)
            prefix.append(token)
        assert decoder.actions[-1] == action
        assert decoder.engine.is_complete()
        assert decoder.pop_action() == action


@pytest.mark.parametrize("enabled", [False, True], ids=["legacy", "activated"])
@pytest.mark.parametrize("length", [20, 40, 45])
@pytest.mark.parametrize("lane", ["low", "high"])
def test_yard_five_blocks_naked_load_but_independent_rehandle_clears_it(enabled, length, lane):
    with rule_freeze_v1(enabled=enabled):
        instance, ids, source, obstacle = _case(length, lane)
        destination = "0010182" if length == 20 else "0020182"
        load = Load(call_index=instance.call_index, container_id=ids[0],
                    src=f"{source}-01", dst=destination)
        state = initial_state(instance)
        if enabled:
            with pytest.raises(IllegalMoveError) as error:
                apply_move(instance, state, load)
            assert error.value.violation.rule_id == "port.e3.yard_crossing"
        else:
            apply_move(instance, state, load)
        # Specify this witness without asking the expander where to relocate.
        lane_row = 1 if lane == "low" else 2
        clear = Relocate(call_index=instance.call_index, container_id=ids[-1],
                         src=f"{obstacle}-05", dst=f"{instance.port.yard.blocks[0].id}-004-{lane_row:02d}-01")
        state = apply_move(instance, state, clear)
        state = apply_move(instance, state, load)
        assert state.ship.placements[-1].container_id == ids[0]


@pytest.mark.parametrize("enabled", [False, True], ids=["legacy", "activated"])
@pytest.mark.parametrize("length", [20, 40, 45])
@pytest.mark.parametrize("lane", ["low", "high"])
def test_real_mask_complete_target_domain_requires_exactly_one_yard_rehandle(tokenizer, enabled, length, lane):
    with rule_freeze_v1(enabled=enabled):
        instance, ids, _, obstacle = _case(length, lane)
        targets = _targets(ids[0], length)
        _assert_mask_domain(instance, tokenizer, targets)
        for action in targets:
            ex = Expander(instance)
            assert ex.can_expand(action)
            moves = ex.step(action)
            relocations = [move for move in moves if move.kind == "relocate"]
            assert len(relocations) == int(enabled)
            if enabled:
                assert relocations[0].container_id == ids[-1]
                assert relocations[0].src == f"{obstacle}-05"
            assert sum(move.kind == "load" for move in moves) == 1
            state = initial_state(instance)
            for move in moves:
                state = apply_move(instance, state, move)
            assert state == ex.world_state()


@pytest.mark.parametrize("enabled", [False, True], ids=["legacy", "activated"])
@pytest.mark.parametrize("lane", ["low", "high"])
def test_real_mask_no_free_yard_row_rejects_all_targets_atomically(tokenizer, enabled, lane):
    with rule_freeze_v1(enabled=enabled):
        instance, ids, source, _ = _case(lane=lane, full=True)
        # Both possible stacks are full. The load is on top, so the only
        # obstacle is the yard-five crossing; quay-eight must not bypass it.
        assert [len(stack.boxes) for stack in instance.yard.stacks] == [5, 5]
        targets = _targets(ids[0], 20)
        _assert_mask_domain(instance, tokenizer, () if enabled else targets)
        for action in targets:
            ex = Expander(instance)
            before = ex.world_state()
            assert ex.can_expand(action) is not enabled
            if enabled:
                with pytest.raises(ValueError, match="no legal yard position to clear crossing stacks"):
                    ex.step(action)
                assert ex.world_state() == before and ex.moves == []
                if action.slot.endswith("82"):
                    load = Load(call_index=instance.call_index, container_id=ids[0],
                                src=f"{source}-05", dst=action.slot)
                    with pytest.raises(IllegalMoveError) as error:
                        apply_move(instance, before, load)
                    assert error.value.violation.rule_id == "port.e3.yard_crossing"
            else:
                state = before
                for move in ex.step(action):
                    state = apply_move(instance, state, move)
                assert state == ex.world_state()
