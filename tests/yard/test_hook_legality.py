"""Custom retrieval hooks remain subject to yard restrictions on every lift."""
from dataclasses import dataclass

import pytest

from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.conventions import make_container_id
from stowage.model.moves import GateOut, Relocate
from stowage.model.ship import Placement, WorldState
from stowage.model.yard import YardStack, YardState
from stowage.moves.engine import IllegalMoveError, apply_move
from stowage.ports import load_simulator_params
from stowage.yard import Retrieval, ShipCall, YardCapacityError, simulate_port
from stowage.yard.dig_out import destinations, location, validate_relocation


def _case(kind, *, import_exit=False, two_blockers=False):
    instance = build_instance()
    target = instance.containers[0 if import_exit else 2].model_copy(update={
        "special_stowage": [], "oog": None, "reefer": False, "reefer_setpoint_c": None,
    })
    blocker = instance.containers[1].model_copy(update={
        "reefer": kind == "reefer", "reefer_setpoint_c": -18 if kind == "reefer" else None,
        "dg": instance.containers[1].dg if kind == "dg" else None,
    })
    boxes = [target, blocker]
    if two_blockers:
        boxes.append(instance.containers[3].model_copy(update={
            "reefer": kind == "reefer", "reefer_setpoint_c": -18 if kind == "reefer" else None,
            "dg": blocker.dg, "oog": None,
        }))
    instance.containers = boxes
    inventory = [box.id for box in boxes[1:]] if import_exit else [box.id for box in boxes]
    yard = YardState(yard_id=instance.port.yard.id, stacks=[
        YardStack(key="B-001-01", length_ft=20, boxes=inventory),
    ])
    instance.yard = yard
    if import_exit:
        target.pod = instance.port.locode
        instance.stage = "B"
        instance.load_list = []
        instance.discharge_list = [target.id]
        instance.onboard.placements = [Placement(slot="0020182", container_id=target.id)]
    else:
        instance.load_list = [target.id]
    instance = Instance.model_validate(instance.model_dump())
    call = ShipCall("hook-call", instance, 100, 90,
                    {} if import_exit else {target.id: 0})
    return call, yard, target.id, [box.id for box in boxes[1:]]


@dataclass
class ScriptedHook:
    blocker_ids: list[str]
    landing_addresses: list[str]

    def retrieve(self, instance, state, container_id):
        moves = []
        for blocker, dst in zip(reversed(self.blocker_ids), self.landing_addresses):
            move = Relocate(call_index=instance.call_index, container_id=blocker,
                            src=location(state, blocker), dst=dst)
            # This intentionally checks physical legality only. The simulator
            # must independently enforce yard restrictions before committing.
            state = apply_move(instance, state, move)
            moves.append(move)
        return Retrieval(state, tuple(moves), location(state, container_id))


@pytest.mark.parametrize("import_exit", [False, True], ids=["export-hook", "import-exit-hook"])
@pytest.mark.parametrize("kind,forbidden", [("reefer", "A-003-02-01"), ("dg", "A-003-01-01")])
def test_custom_hook_rejects_yard_restriction_before_commit(kind, forbidden, import_exit):
    call, yard, target, blockers = _case(kind, import_exit=import_exit)
    before = yard.model_dump_json()
    initial = WorldState(call_index=0, yard=yard, ship=call.instance.onboard)
    assert forbidden not in destinations(call.instance, initial, blockers[0], exclude="B-001-01")
    # The physical engine accepts exactly the move that the yard must reject.
    apply_move(call.instance, initial, Relocate(call_index=0, container_id=blockers[0],
               src=location(initial, blockers[0]), dst=forbidden))
    result = simulate_port([call], load_simulator_params("laem_chabang_like"), seed=2,
                          initial_yard=yard, dig_out=ScriptedHook(blockers, [forbidden]))
    assert not any(record.move.kind == "relocate" for record in result.moves)
    assert yard.model_dump_json() == before
    assert target in (result.retained_import_exits if import_exit else result.unserved_loads)
    assert location(WorldState(call_index=0, yard=result.final_yard,
                               ship=result.final_ships[call.id]), blockers[0]) == location(initial, blockers[0])


@pytest.mark.parametrize("kind", ["reefer", "dg"])
def test_custom_hook_validates_against_evolving_destination_stack(kind):
    call, yard, target, blockers = _case(kind, two_blockers=True)
    hook = ScriptedHook(blockers, ["B-003-01-01", "B-003-01-02"])
    result = simulate_port([call], load_simulator_params("laem_chabang_like"), seed=2,
                          initial_yard=yard, dig_out=hook)
    assert not result.unserved_loads
    assert sum(record.move.kind == "relocate" for record in result.moves) == 2
    state = WorldState(call_index=0, yard=yard, ship=call.instance.onboard)
    for record in result.moves:
        move = record.move
        if move.kind == "relocate":
            assert move.dst in destinations(call.instance, state, move.container_id,
                                            exclude=move.src.rsplit("-", 1)[0])
        state = apply_move(call.instance, state, move)
    state.yard.time_s = result.final_yard.time_s
    assert state.yard == result.final_yard
    assert state.ship == result.final_ships[call.id]


def test_custom_hook_rejects_second_relocation_after_first_commits():
    call, yard, target, blockers = _case("reefer", two_blockers=True)
    result = simulate_port([call], load_simulator_params("laem_chabang_like"), seed=2,
                          initial_yard=yard, dig_out=ScriptedHook(
                              blockers, ["B-003-01-01", "A-003-02-01"]))
    relocations = [record for record in result.moves if record.move.kind == "relocate"]
    assert len(relocations) == 1
    assert relocations[0].move.dst == "B-003-01-01"
    assert target in result.unserved_loads
    state = WorldState(call_index=0, yard=yard, ship=call.instance.onboard)
    for record in result.moves:
        state = apply_move(call.instance, state, record.move)
    state.yard.time_s = result.final_yard.time_s
    assert state.yard == result.final_yard


@pytest.mark.parametrize("bad_kind", ["gate_out", "unknown_container"])
def test_hook_validation_rejects_nonrelocation_and_unknown_container(bad_kind):
    call, yard, _, blockers = _case("reefer")
    state = WorldState(call_index=0, yard=yard, ship=call.instance.onboard)
    move = (GateOut(call_index=0, container_id=blockers[0], src="B-001-01-02")
            if bad_kind == "gate_out" else Relocate(
                call_index=0, container_id=make_container_id("QZXU", 999),
                src="B-001-01-02", dst="B-003-01-01"))
    with pytest.raises(YardCapacityError):
        validate_relocation(call.instance, state, move)


def test_destination_validation_retains_engine_source_access_check():
    call, yard, target, _ = _case("reefer")
    state = WorldState(call_index=0, yard=yard, ship=call.instance.onboard)
    before = state.model_dump_json()
    # A permissible destination never authorizes lifting the buried target.
    move = Relocate(call_index=0, container_id=target, src="B-001-01-01", dst="B-003-01-01")
    validate_relocation(call.instance, state, move)
    with pytest.raises(IllegalMoveError, match="top container"):
        apply_move(call.instance, state, move)
    assert state.model_dump_json() == before
