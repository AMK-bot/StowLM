"""D69 yard rehandles use the same crossing paths as physical replay."""
import pytest

from stowage.conventions import make_container_id
from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import GateOut, Relocate
from stowage.model.ship import Placement, WorldState
from stowage.model.yard import YardStack, YardState
from stowage.moves import apply_move, count_moves
from stowage.ports import load_simulator_params
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import NearestStackDigOut, ShipCall, simulate_port


def case(*, lane="low", long_target=False, buried=False):
    instance = build_instance()
    base = instance.containers[2]
    boxes = [base.model_copy(update={
        "id": make_container_id("QZXU", 700 + i), "dg": None,
        "reefer": False, "reefer_setpoint_c": None,
        "special_stowage": [], "oog": None, "direct_delivery": False,
    }) for i in range(4)]
    if long_target:
        boxes[0] = boxes[0].model_copy(update={"length_ft": 40, "iso_type": "42G1"})
    instance.containers = boxes
    instance.load_list = instance.discharge_list = []
    instance.port.max_crane_height_tiers = 2
    block = instance.port.yard.blocks[0]
    block.n_bays, block.n_rows, block.max_tiers = 4, 4, 2
    block.truck_lane_side = lane
    instance.port.yard.blocks = [block]
    instance.port.yard_max_tiers = 2
    for equipment in instance.port.equipment:
        equipment.stack_tiers = 2
    source_row, blocker_row = (3, 1) if lane == "low" else (2, 4)
    target_key = f"{block.id}-001-{source_row:02d}"
    blocker_key = f"{block.id}-{2 if long_target else 1:03d}-{blocker_row:02d}"
    stacks = [YardStack(key=target_key, length_ft=40 if long_target else 20,
                        boxes=[boxes[0].id] + ([boxes[3].id] if buried else [])),
              YardStack(key=blocker_key, length_ft=20, boxes=[boxes[1].id, boxes[2].id])]
    instance.yard = YardState(yard_id=instance.port.yard.id, stacks=stacks)
    return instance, WorldState(call_index=0, yard=instance.yard, ship=instance.onboard), boxes[0].id


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("lane", ["low", "high"])
@pytest.mark.parametrize("long_target", [False, True])
def test_retrieval_clears_h_high_lane_stack_with_one_counted_rehandle(enabled, lane, long_target):
    instance, state, target = case(lane=lane, long_target=long_target)
    before = state.model_dump_json()
    with rule_freeze_v1(enabled=enabled):
        result = NearestStackDigOut().retrieve(instance, state, target)
        assert len(result.moves) == int(enabled)
        assert all(isinstance(move, Relocate) for move in result.moves)
        replay = state
        for move in result.moves:
            replay = apply_move(instance, replay, move)
        assert replay == result.state
        apply_move(instance, replay, GateOut(call_index=0, container_id=target, src=result.source))
        plan = MovePlan(id="crane-dig-out", instance_id=instance.id,
                        call_index=0, moves=list(result.moves))
        assert count_moves(instance, plan).relocations == int(enabled)
    assert state.model_dump_json() == before


def test_buried_retrieval_does_not_rebuild_h_high_outbound_blocker():
    instance, state, target = case(buried=True)
    with rule_freeze_v1():
        result = NearestStackDigOut().retrieve(instance, state, target)
        assert len(result.moves) == 2
        replay = state
        for move in result.moves:
            replay = apply_move(instance, replay, move)
        apply_move(instance, replay, GateOut(call_index=0, container_id=target, src=result.source))


def test_multiple_h_high_lane_stacks_stay_cleared():
    instance, state, target = case()
    extra = instance.containers[3].model_copy(update={"id": make_container_id("QZXU", 710)})
    instance.containers.append(extra)
    state.yard.stacks.append(YardStack(key="A-001-02", length_ft=20,
        boxes=[instance.containers[3].id, extra.id]))
    with rule_freeze_v1():
        result = NearestStackDigOut().retrieve(instance, state, target)
        assert len(result.moves) == 2
        replay = state
        for move in result.moves:
            replay = apply_move(instance, replay, move)
        assert all(len(stack.boxes) < 2 for stack in replay.yard.stacks
                   if stack.key in {"A-001-01", "A-001-02"})
        apply_move(instance, replay, GateOut(call_index=0, container_id=target, src=result.source))


def test_nearest_crossing_blocked_destination_falls_back_to_farther_legal_stack():
    instance, _, target = case()
    block = instance.port.yard.blocks[0]
    block.n_bays, block.reefer_rows = 1, [4]
    distant = block.model_copy(update={"id": "Z", "n_rows": 1, "reefer_rows": [1], "x_m": 100.0})
    instance.port.yard.blocks.append(distant)
    instance.containers[3].reefer = True
    instance.containers[3].reefer_setpoint_c = -18
    state = WorldState(call_index=0, ship=instance.onboard, yard=YardState(
        yard_id=instance.port.yard.id, stacks=[
            YardStack(key="A-001-01", length_ft=20, boxes=[target, instance.containers[3].id]),
            YardStack(key="A-001-02", length_ft=20,
                      boxes=[instance.containers[1].id, instance.containers[2].id]),
        ]))
    with rule_freeze_v1():
        result = NearestStackDigOut().retrieve(instance, state, target)
        assert len(result.moves) == 1
        assert result.moves[0].dst == "Z-001-01-01"
        assert apply_move(instance, state, result.moves[0]) == result.state


def test_simulator_records_export_crossing_rehandle():
    instance, state, target = case()
    instance.load_list = [target]
    call = ShipCall("crane-export", instance, 100, 90, {target: 0})
    with rule_freeze_v1():
        result = simulate_port([call], load_simulator_params("laem_chabang_like"),
                               seed=2, initial_yard=state.yard)
        assert not result.unserved_loads
        assert sum(record.move.kind == "relocate" for record in result.moves) == 1
        assert sum(record.move.kind == "load" for record in result.moves) == 1


def test_simulator_records_import_exit_crossing_rehandle(monkeypatch):
    """A near-row gate-in after discharge can obstruct a dwelling import."""
    import stowage.yard.simulator as simulator

    instance, state, target = case()
    bottom, top = instance.containers[1:3]
    inbound = instance.model_copy(deep=True)
    inbound.containers = [inbound.containers[0], inbound.containers[1]]
    inbound.containers[0].pod = inbound.port.locode
    inbound.discharge_list = [target]
    inbound.onboard.placements = [Placement(slot="0010182", container_id=target)]
    outbound = instance.model_copy(deep=True)
    outbound.containers = [top]
    outbound.load_list = [top.id]
    initial = YardState(yard_id=state.yard.yard_id, stacks=[
        YardStack(key="A-001-01", length_ft=20, boxes=[bottom.id])])
    original = simulator.destinations

    def place_in_fixed_rows(context, world, cid, **kwargs):
        candidates = original(context, world, cid, **kwargs)
        prefix = "A-001-03-" if cid == target else "A-001-01-"
        return [dst for dst in candidates if dst.startswith(prefix)]

    monkeypatch.setattr(simulator, "destinations", place_in_fixed_rows)
    calls = [ShipCall("inbound", inbound, 100, 90, {}),
             ShipCall("outbound", outbound, 1e9, 1e9 - 1, {top.id: 101})]
    with rule_freeze_v1():
        result = simulate_port(calls, load_simulator_params("laem_chabang_like"),
                               seed=2, initial_yard=initial, until_s=1e8)
        assert not result.unserved_discharges
        assert not result.retained_import_exits
        assert sum(record.move.kind == "relocate" for record in result.moves) == 1
        assert any(record.move.kind == "gate_out" and record.move.container_id == target
                   for record in result.moves)
