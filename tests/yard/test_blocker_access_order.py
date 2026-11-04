"""D69 tries accessible crossing blockers before reporting no yard capacity."""
import pytest

from stowage.conventions import make_container_id
from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import GateOut
from stowage.model.ship import WorldState
from stowage.model.yard import YardStack, YardState
from stowage.moves import apply_move, count_moves
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard.dig_out import NearestStackDigOut, validate_relocation


def _blocked_reefer_case(lane, length):
    instance = build_instance()
    boxes = [instance.containers[2].model_copy(update={
        "id": make_container_id("QZXU", 800 + index), "dg": None,
        "reefer": False, "reefer_setpoint_c": None,
        "special_stowage": [], "oog": None, "direct_delivery": False,
        "length_ft": length, "iso_type": "22G1" if length == 20 else "42G1",
    }) for index in range(5)]
    boxes[2].reefer = True
    boxes[2].reefer_setpoint_c = -18
    instance.containers = boxes
    instance.load_list = []
    instance.discharge_list = []
    instance.port.max_crane_height_tiers = instance.port.yard_max_tiers = 2
    for equipment in instance.port.equipment:
        equipment.stack_tiers = 2
    target, far, near, free = (1, 2, 3, 4) if lane == "high" else (4, 3, 2, 1)
    block = instance.port.yard.blocks[0]
    block.n_bays, block.n_rows, block.max_tiers = length // 20, 4, 2
    block.reefer_rows, block.truck_lane_side = [far, free], lane
    distant = block.model_copy(update={"id": "Z", "n_rows": 1,
                                       "reefer_rows": [1], "x_m": 100.0})
    instance.port.yard.blocks = [block, distant]
    stacks = [YardStack(key=f"A-001-{row:02d}", length_ft=length, boxes=ids)
              for row, ids in [(target, [boxes[0].id]),
                               (far, [boxes[1].id, boxes[2].id]),
                               (near, [boxes[3].id, boxes[4].id])]]
    world = WorldState(call_index=0, ship=instance.onboard,
                       yard=YardState(yard_id=instance.port.yard.id, stacks=stacks))
    instance.yard = world.yard
    return instance, world, boxes


@pytest.mark.parametrize("lane", ["high", "low"])
@pytest.mark.parametrize("length", [20, 40])
@pytest.mark.parametrize("enabled", [True, False])
def test_accessible_blocker_is_tried_after_far_reefer_cannot_relocate(lane, length, enabled):
    instance, world, boxes = _blocked_reefer_case(lane, length)
    before = world.model_dump_json()
    with rule_freeze_v1(enabled=enabled):
        result = NearestStackDigOut().retrieve(instance, world, boxes[0].id)
        assert [move.container_id for move in result.moves] == (
            [boxes[4].id, boxes[2].id] if enabled else [])
        replay = world
        for move in result.moves:
            validate_relocation(instance, replay, move)
            replay = apply_move(instance, replay, move)
        assert replay == result.state
        apply_move(instance, replay, GateOut(call_index=0, container_id=boxes[0].id,
                                             src=result.source))
        plan = MovePlan(id="access-order", instance_id=instance.id,
                        call_index=0, moves=list(result.moves))
        assert count_moves(instance, plan).relocations == (2 if enabled else 0)
    assert world.model_dump_json() == before
