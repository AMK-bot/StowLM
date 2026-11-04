"""A8 operational profiles keep yard storage/crossing separate from quay H."""
import pytest

from stowage.conventions import make_container_id
from stowage.model.fixtures import build_instance
from stowage.model.moves import GateOut
from stowage.model.ship import WorldState
from stowage.model.yard import YardStack, YardState
from stowage.moves import IllegalMoveError, apply_move
from stowage.ports import effective_yard_tiers, load_port, load_simulator_params
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import ShipCall, simulate_port


def _case(archetype, count):
    instance = build_instance()
    instance.port = load_port(archetype)
    base = instance.containers[2]
    instance.containers = [base.model_copy(update={
        "id": make_container_id("QZXU", 900 + i), "pol": instance.port.locode,
        "pod": "SGSIN", "special_stowage": [],
    }) for i in range(count)]
    instance.load_list = [box.id for box in instance.containers]
    instance.discharge_list = []
    instance.yard = YardState(yard_id=instance.port.yard.id)
    return instance


@pytest.mark.parametrize("archetype,yard_limit,quay_h", [
    ("laem_chabang_like", 5, 8), ("small_feeder", 3, 4),
])
def test_active_profile_gate_in_fills_yard_limit_and_queues_overflow(archetype, yard_limit, quay_h):
    with rule_freeze_v1():
        instance = _case(archetype, yard_limit + 1)
        assert instance.port.max_crane_height_tiers == quay_h
        assert all(effective_yard_tiers(instance.port, block) == yard_limit
                   for block in instance.port.yard.blocks)
        instance.port.yard.blocks = instance.port.yard.blocks[:1]
        instance.port.yard.blocks[0].n_bays = instance.port.yard.blocks[0].n_rows = 1
        call = ShipCall("a8-profile", instance, 1000, 900,
                        {cid: 0 for cid in instance.load_list})
        result = simulate_port([call], load_simulator_params(archetype), seed=7, noise=0, until_s=1)
        assert len(result.final_yard.stacks) == 1
        assert len(result.final_yard.stacks[0].boxes) == yard_limit
        assert len(result.external_queue) == 1
        assert sum(record.move.kind == "gate_in" for record in result.moves) == yard_limit


@pytest.mark.parametrize("archetype,yard_limit,quay_h", [
    ("laem_chabang_like", 5, 8), ("small_feeder", 3, 4),
])
def test_active_profile_retrieval_crossing_blocks_at_yard_limit(archetype, yard_limit, quay_h):
    with rule_freeze_v1():
        instance = _case(archetype, yard_limit + 1)
        assert instance.port.max_crane_height_tiers == quay_h
        block = instance.port.yard.blocks[0]
        block.truck_lane_side = "low"
        target, *blockers = instance.load_list
        state = WorldState(call_index=instance.call_index, ship=instance.onboard, yard=YardState(
            yard_id=instance.port.yard.id, stacks=[
                YardStack(key=f"{block.id}-001-03", length_ft=20, boxes=[target]),
                YardStack(key=f"{block.id}-001-01", length_ft=20, boxes=blockers),
            ]))
        retrieval = GateOut(call_index=instance.call_index, container_id=target,
                            src=f"{block.id}-001-03-01")
        with pytest.raises(IllegalMoveError) as error:
            apply_move(instance, state, retrieval)
        assert error.value.violation.rule_id == "port.e3.yard_crossing"
        assert error.value.violation.limit == yard_limit
        cleared = apply_move(instance, state, GateOut(
            call_index=instance.call_index, container_id=blockers[-1],
            src=f"{block.id}-001-01-{yard_limit:02d}"))
        assert apply_move(instance, cleared, retrieval)
