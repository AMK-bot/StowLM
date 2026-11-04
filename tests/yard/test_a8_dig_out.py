"""A8 yard crossing uses each block's storage limit, independently of quay H."""
import pytest

from stowage.conventions import make_container_id
from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import GateOut
from stowage.model.ship import WorldState
from stowage.model.yard import YardStack, YardState
from stowage.moves import IllegalMoveError, apply_move, count_moves, crossing_paths
from stowage.ports import effective_yard_tiers, load_port
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import NearestStackDigOut


def _case(profile, height, *, lane, length, blocker_height=None, near_height=0):
    instance = build_instance()
    instance.port = load_port(profile)
    block = instance.port.yard.blocks[0]
    block.id, block.n_bays, block.n_rows = "A", 4, 4
    block.truck_lane_side = lane
    block.reefer_rows = []
    instance.port.yard.blocks = [block]
    target_row, blocker_row, near_row = (4, 2, 1) if lane == "low" else (1, 3, 4)
    blocker_height = height if blocker_height is None else blocker_height
    boxes = [instance.containers[2].model_copy(update={
        "id": make_container_id("QZXU", 920 + index), "dg": None,
        "reefer": False, "reefer_setpoint_c": None,
        "special_stowage": [], "oog": None, "direct_delivery": False,
    }) for index in range(1 + blocker_height + near_height)]
    boxes[0] = boxes[0].model_copy(update={
        "length_ft": length, "iso_type": "22G1" if length == 20 else "42G1"})
    instance.containers = boxes
    instance.load_list = instance.discharge_list = []
    # A long retrieval must also clear a blocker under its second ground bay.
    blocker_bay = 1 if length == 20 else 2
    stacks = [YardStack(key=f"A-001-{target_row:02d}", length_ft=length, boxes=[boxes[0].id]),
              YardStack(key=f"A-{blocker_bay:03d}-{blocker_row:02d}", length_ft=20,
                        boxes=[box.id for box in boxes[1:1 + blocker_height]])]
    if near_height:
        stacks.append(YardStack(key=f"A-{blocker_bay:03d}-{near_row:02d}", length_ft=20,
                               boxes=[box.id for box in boxes[1 + blocker_height:]]))
    instance.yard = YardState(yard_id=instance.port.yard.id, stacks=stacks)
    world = WorldState(call_index=0, yard=instance.yard, ship=instance.onboard)
    return instance, world, boxes[0].id


@pytest.mark.parametrize("profile,quay_height,yard_height", [
    ("small_feeder", 4, 3), ("laem_chabang_like", 8, 5),
])
@pytest.mark.parametrize("lane", ["low", "high"])
@pytest.mark.parametrize("length", [20, 40])
@pytest.mark.parametrize("enabled", [False, True])
def test_yard_limit_blocker_gets_one_counted_engine_replayed_rehandle(
        profile, quay_height, yard_height, lane, length, enabled):
    with rule_freeze_v1(enabled=enabled):
        instance, world, target = _case(profile, yard_height, lane=lane, length=length,
                                       near_height=yard_height - 1)
        if enabled:
            assert instance.port.max_crane_height_tiers == quay_height
        assert effective_yard_tiers(instance.port, "A") == yard_height
        before = world.model_dump_json()
        if enabled:
            target_stack = next(stack for stack in world.yard.stacks if target in stack.boxes)
            with pytest.raises(IllegalMoveError) as error:
                apply_move(instance, world, GateOut(call_index=0, container_id=target,
                           src=target_stack.key + "-01"))
            assert error.value.violation.rule_id == "port.e3.yard_crossing"
        result = NearestStackDigOut().retrieve(instance, world, target)
        assert len(result.moves) == int(enabled)
        replay = world
        for move in result.moves:
            replay = apply_move(instance, replay, move)
        assert replay == result.state
        gate_out = GateOut(call_index=0, container_id=target, src=result.source)
        apply_move(instance, replay, gate_out)
        if enabled:
            lane_cells = set(crossing_paths(instance, gate_out).yard_ground_slots)
            for stack in replay.yard.stacks:
                block, bay, row = stack.key.split("-")
                if (block, int(bay), int(row)) in lane_cells:
                    assert len(stack.boxes) < yard_height
        plan = MovePlan(id="a8-yard-limit", instance_id=instance.id,
                        call_index=0, moves=list(result.moves))
        assert count_moves(instance, plan).relocations == int(enabled)
        assert world.model_dump_json() == before


def test_lower_limit_in_unrelated_block_does_not_shorten_crossed_block_limit():
    with rule_freeze_v1():
        instance, world, target = _case("laem_chabang_like", 5, lane="low", length=20,
                                       blocker_height=3)
        unrelated = instance.port.yard.blocks[0].model_copy(update={"id": "Z", "max_tiers": 3})
        instance.port.yard.blocks.append(unrelated)
        result = NearestStackDigOut().retrieve(instance, world, target)
        assert result.moves == ()
        apply_move(instance, world, GateOut(call_index=0, container_id=target, src=result.source))


def test_quay_h_below_yard_limit_does_not_create_a_yard_crossing_blocker():
    with rule_freeze_v1():
        instance, world, target = _case("small_feeder", 3, lane="low", length=20,
                                       blocker_height=2)
        instance.port.max_crane_height_tiers = 2
        result = NearestStackDigOut().retrieve(instance, world, target)
        assert result.moves == ()
        apply_move(instance, world, GateOut(call_index=0, container_id=target, src=result.source))


def test_unknown_quay_h_still_clears_a_full_yard_crossing_stack():
    with rule_freeze_v1():
        instance, world, target = _case("small_feeder", 3, lane="low", length=20)
        instance.port.max_crane_height_tiers = None
        result = NearestStackDigOut().retrieve(instance, world, target)
        assert len(result.moves) == 1
        replay = apply_move(instance, world, result.moves[0])
        assert replay == result.state
        apply_move(instance, replay, GateOut(call_index=0, container_id=target, src=result.source))
