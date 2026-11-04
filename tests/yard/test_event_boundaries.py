"""Same-time capacity releases and ship-access dependencies are event semantics."""
import pytest

from stowage.conventions import make_container_id
from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.ship import Placement, ShipState, WorldState
from stowage.model.yard import YardState
from stowage.moves.engine import apply_move, apply_plan
from stowage.ports import load_simulator_params
from stowage.yard import ShipCall, simulate_port


def import_call(slots, *, through=False):
    instance = build_instance()
    base = instance.containers[2]
    boxes = [base.model_copy(update={
        "id": make_container_id("QZXU", 400 + i),
        "pol": "THBKK", "pod": "THLCH", "category": None,
        "special_stowage": [], "oog": None,
        "length_ft": 40 if slot.startswith("002") else 20,
        "iso_type": "42G1" if slot.startswith("002") else "22G1",
    }) for i, slot in enumerate(slots)]
    if through:
        boxes[-1].pod = "THBKK"
    instance = instance.model_copy(update={
        "containers": boxes, "load_list": [],
        "discharge_list": [b.id for b in boxes[:-1] if through] if through else [b.id for b in boxes],
        "yard": YardState(yard_id=instance.port.yard.id),
        "onboard": ShipState(vessel_id=instance.vessel.id,
                             placements=[Placement(slot=s, container_id=b.id) for s, b in zip(slots, boxes)],
                             tank_masses_kg=dict(instance.onboard.tank_masses_kg)),
    })
    return ShipCall("inbound", instance, 100, 90, {})


@pytest.mark.parametrize("slots", [
    ("0010182", "0030182", "0020184"),
    ("0030102", "0010182"),
], ids=["cross-bay-span", "hatch-dependency"])
def test_discharge_retries_after_cross_bay_or_hatch_blocker(slots):
    call = import_call(slots)
    result = simulate_port([call], load_simulator_params("laem_chabang_like"), seed=9, until_s=100)
    assert not result.unserved_discharges
    assert not result.final_ships[call.id].placements
    assert {cid for stack in result.final_yard.stacks for cid in stack.boxes} == set(call.instance.discharge_list)
    state = WorldState(call_index=0, yard=call.instance.yard, ship=call.instance.onboard)
    for record in result.moves:
        state = apply_move(call.instance, state, record.move)
    assert state.ship == result.final_ships[call.id]
    assert state.yard.stacks == result.final_yard.stacks


def test_discharge_stops_without_removing_through_cargo():
    call = import_call(("0030102", "0010182"), through=True)
    result = simulate_port([call], load_simulator_params("laem_chabang_like"), seed=9)
    assert result.unserved_discharges == call.instance.discharge_list
    assert result.final_ships[call.id] == call.instance.onboard
    assert not result.moves


def one_box_call(serial, name, arrival, cutoff, gate):
    instance = build_instance()
    box = instance.containers[2].model_copy(update={
        "id": make_container_id("QZXU", serial), "special_stowage": [], "oog": None,
    })
    port = instance.port.model_copy(deep=True)
    port.yard.blocks = port.yard.blocks[:1]
    block = port.yard.blocks[0]
    block.n_bays = block.n_rows = block.max_tiers = port.yard_max_tiers = 1
    instance = instance.model_copy(update={"port": port, "containers": [box],
        "load_list": [box.id], "yard": YardState(yard_id=port.yard.id)})
    return ShipCall(name, instance, arrival, cutoff, {box.id: gate})


@pytest.mark.parametrize("release", [999, 1000, 1001])
def test_gate_cutoff_is_inclusive_after_same_time_releases(release):
    first = one_box_call(501, "a", release, 900, 0)
    second = one_box_call(502, "b", 1100, 1000, 10)
    result = simulate_port([second, first], load_simulator_params("laem_chabang_like"), seed=3)
    cid = second.instance.load_list[0]
    if release <= second.cutoff_s:
        assert cid not in result.missed_cutoffs
        assert cid not in result.external_queue
        assert cid not in result.unserved_loads
        assert any(r.move.kind == "gate_in" and r.move.container_id == cid and r.time_s == release
                   for r in result.moves)
    else:
        assert result.missed_cutoffs == result.external_queue == result.unserved_loads == [cid]


def test_gate_exactly_at_cutoff_is_accepted():
    call = one_box_call(503, "at-cutoff", 1100, 1000, 1000)
    result = simulate_port([call], load_simulator_params("laem_chabang_like"), seed=3)
    assert not result.missed_cutoffs
    assert not result.unserved_loads


def test_simulator_trace_uses_single_moves_and_is_not_a_loading_plan():
    call = one_box_call(504, "gate-trace", 1100, 1000, 10)
    result = simulate_port([call], load_simulator_params("laem_chabang_like"), seed=3)
    state = WorldState(call_index=0, yard=call.instance.yard, ship=call.instance.onboard)
    for record in result.moves:
        state = apply_move(call.instance, state, record.move)
    assert state.ship == result.final_ships[call.id]
    plan = MovePlan(id="simulator-trace", instance_id=call.instance.id, call_index=0,
                    moves=[record.move for record in result.moves])
    replay = apply_plan(call.instance, plan)
    assert not replay.legal
    assert any("gate" in violation.message.lower() for violation in replay.violations)
