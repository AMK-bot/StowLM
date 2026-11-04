"""A8: yard clearance uses per-block yard limits independently of ship H."""
import pytest

from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.model import Instance
from stowage.model.yard import YardStack
from stowage.moves import apply_move, initial_state
from stowage.ports import load_port, yard_blocking_tiers
from stowage.rules.catalogue import rule_freeze_v1


def laem_case(length=20):
    instance = tiny_instance(37, n_load=1, n_other=5, p40=0, max_tiers=5)
    instance.port = load_port('laem_chabang_like')
    ids = [box.id for box in instance.containers]
    instance.containers[0].length_ft = length
    instance.containers[0].iso_type = {20: '22G1', 40: '42G1', 45: 'L2G1'}[length]
    block = instance.port.yard.blocks[0]
    blocker_bay = 1 if length == 20 else 2
    instance.yard.yard_id = instance.port.yard.id
    instance.yard.stacks = [
        YardStack(key=f'{block.id}-001-02', length_ft=length, boxes=[ids[0]]),
        YardStack(key=f'{block.id}-{blocker_bay:03d}-01', length_ft=20, boxes=ids[1:]),
    ]
    return Instance.model_validate(instance.model_dump()), ids


@pytest.mark.parametrize('length', [20, 40, 45])
@pytest.mark.parametrize('enabled', [False, True])
def test_laem_yard_five_is_cleared_with_ship_height_eight(length, enabled):
    with rule_freeze_v1(enabled=enabled):
        instance, ids = laem_case(length)
        assert yard_blocking_tiers(instance.port, instance.port.yard.blocks[0]) == 5
        if enabled:
            assert instance.port.max_crane_height_tiers == 8
        ex = Expander(instance)
        action = Place(ids[0], '0010182' if length == 20 else '0020182')
        assert ex.can_expand(action)
        moves = ex.step(action)
        assert [move.kind for move in moves] == (['relocate', 'load'] if enabled else ['load'])
        state = initial_state(instance)
        for move in moves:
            state = apply_move(instance, state, move)
        assert state == ex.world_state()


def test_known_yard_limit_is_cleared_when_ship_height_unknown():
    with rule_freeze_v1():
        instance, ids = laem_case()
        instance.port.max_crane_height_tiers = None
        moves = Expander(instance).step(Place(ids[0], '0010182'))
        assert [move.kind for move in moves] == ['relocate', 'load']
        state = initial_state(instance)
        for move in moves:
            state = apply_move(instance, state, move)


def test_relocation_does_not_rebuild_yard_limit_on_retrieval_path():
    with rule_freeze_v1():
        instance, ids = laem_case()
        block = instance.port.yard.blocks[0]
        source, lane = f'{block.id}-001-02', f'{block.id}-001-01'
        instance.yard.stacks = [
            YardStack(key=source, length_ft=20, boxes=ids[:2]),
            YardStack(key=lane, length_ft=20, boxes=ids[2:]),
        ]
        ex = Expander(instance)
        target = ex.yard_target(ids[1], source, avoid_ground=frozenset({(block.id, 1, 1)}))
        assert target is not None and target != lane


def test_lower_ship_height_does_not_cause_unneeded_yard_clearance():
    with rule_freeze_v1():
        instance, ids = laem_case()
        instance.port.max_crane_height_tiers = 2
        instance.port.quay_crane.max_on_deck_tiers = 2
        block = instance.port.yard.blocks[0]
        instance.yard.stacks = [
            YardStack(key=f'{block.id}-001-02', length_ft=20, boxes=[ids[0]]),
            YardStack(key=f'{block.id}-001-01', length_ft=20, boxes=ids[1:3]),
            YardStack(key=f'{block.id}-003-01', length_ft=20, boxes=ids[3:]),
        ]
        instance = Instance.model_validate(instance.model_dump())
        moves = Expander(instance).step(Place(ids[0], '0010182'))
        assert [move.kind for move in moves] == ['load']
        apply_move(instance, initial_state(instance), moves[0])


def test_engine_witness_and_expansion_count_exactly_one_rehandle_at_yard_five_quay_eight():
    from stowage.actions import get_action_space
    from stowage.model import MovePlan
    from stowage.model.moves import Load, Relocate
    from stowage.moves import IllegalMoveError, apply_plan

    with rule_freeze_v1():
        instance, ids = laem_case()
        block = instance.port.yard.blocks[0]
        assert instance.port.max_crane_height_tiers == 8
        assert yard_blocking_tiers(instance.port, block) == 5
        load = Load(call_index=instance.call_index, container_id=ids[0],
                    src=f'{block.id}-001-02-01', dst='0010182')
        # This witness is specified directly, independently of the expander's
        # chosen destination. The naked load must fail before clearance.
        state = initial_state(instance)
        with pytest.raises(IllegalMoveError) as error:
            apply_move(instance, state, load)
        assert error.value.violation.rule_id == 'port.e3.yard_crossing'
        clear = Relocate(call_index=instance.call_index, container_id=ids[-1],
                         src=f'{block.id}-001-01-05', dst=f'{block.id}-003-01-01')
        state = apply_move(instance, state, clear)
        state = apply_move(instance, state, load)
        witness = MovePlan(id='independent-laem-one-rehandle', instance_id=instance.id,
                           call_index=instance.call_index, moves=[clear, load])
        result = apply_plan(instance, witness)
        assert result.legal and result.final_state == state
        assert result.count.relocations == 1 and result.count.loads == 1
        assert result.count.total_lifts_now == 2
        ex = Expander(instance)
        assert ex.can_expand(Place(ids[0], load.dst))
        expanded = ex.step(Place(ids[0], load.dst))
        expanded_plan = witness.model_copy(update={'id': 'expanded-laem-one-rehandle', 'moves': expanded})
        expanded_result = apply_plan(instance, expanded_plan)
        assert expanded_result.legal
        assert expanded_result.count.relocations == 1 and expanded_result.count.loads == 1
        assert expanded_result.count.total_lifts_now == 2
        space = get_action_space()
        reproduced = space.expand(instance, space.from_plan(instance, witness))
        assert reproduced.moves == witness.moves
