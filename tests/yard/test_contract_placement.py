"""Activated C1/C5/C6 yard placement and seeded legacy regression evidence."""
from dataclasses import asdict
import hashlib
import json

from pydantic_core import to_jsonable_python
import pytest

from stowage.conventions import make_container_id
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.model.ship import Placement, ShipState
from stowage.model.yard import YardStack, YardState
from stowage.ports import load_simulator_params
from stowage.ports.profiles import effective_yard_tiers
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import ShipCall, simulate_port
from stowage.yard.simulator import yard_statistics


def _case(n=6):
    instance = build_instance()
    base = instance.containers[2]
    boxes = [base.model_copy(update={"id": make_container_id("QZXU", 100 + i),
             "pod": "SGSIN", "category": None, "special_stowage": [], "oog": None})
             for i in range(n)]
    from stowage.model.port import PortCall, Rotation
    instance.rotation = Rotation(service="straits", calls=[PortCall(index=i, locode=p)
                                for i, p in enumerate(("THLCH", "SGSIN", "MYPKG", "THLCH"))])
    instance.containers = boxes
    instance.load_list = sorted(b.id for b in boxes)
    instance.yard = YardState(yard_id=instance.port.yard.id, stacks=[
        YardStack(key=f"A-{i+1:03d}-01", length_ft=20, boxes=[box.id])
        for i, box in enumerate(boxes)])
    return Instance.model_validate(instance.model_dump())


def _call(instance, *, simultaneous=False):
    return ShipCall("ship-1", instance, 1000, 900,
                    {cid: 0 if simultaneous else i * 10 for i, cid in enumerate(instance.load_list)})


@pytest.mark.parametrize("seed,noise,expected", [
    (14, .5, "daeb3de7ed151b76613dc8d8e7358bbe02d583903f68140e15d8d633ab4ce5b4"),
    (32, 0, "c911621f05e240852af8d61f1c99ab6fbf17b1d95ce072dcb95d45e16eced33b"),
    (32, 1, "4805229a6d4077c04cee2bdb8de87d1669ea756d6a77bee67eec8f1afe03771e"),
])
def test_legacy_seeded_simulator_output(seed, noise, expected):
    with rule_freeze_v1(enabled=False):
        result = simulate_port([_call(_case())], load_simulator_params("laem_chabang_like"),
                               seed=seed, noise=noise)
    payload = asdict(result)
    encoded = json.dumps(to_jsonable_python(payload), sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected


@pytest.mark.parametrize("limiting", ["block", "yard", "equipment"])
def test_gate_placements_respect_every_effective_height_limit(limiting):
    with rule_freeze_v1():
        instance = _case()
        instance.port.yard.blocks = instance.port.yard.blocks[:1]
        block = instance.port.yard.blocks[0]
        block.n_bays = block.n_rows = 1
        block.max_tiers = instance.port.yard_max_tiers = 4
        instance.port.max_crane_height_tiers = 4
        instance.port.equipment[0].stack_tiers = 4
        if limiting == "block":
            block.max_tiers = 2
        elif limiting == "yard":
            instance.port.yard_max_tiers = 2
        else:
            instance.port.equipment[0].stack_tiers = 2
        result = simulate_port([_call(instance, simultaneous=True)],
                               load_simulator_params("laem_chabang_like"), seed=7, noise=0, until_s=1)
        assert effective_yard_tiers(instance.port, block) == 2
        assert max(len(s.boxes) for s in result.final_yard.stacks) == 2
        assert len(result.external_queue) == 4


@pytest.mark.parametrize("height", [1, 8])
def test_gate_placements_use_yard_limit_independently_of_h(height):
    """A8 keeps quay H separate, both below and above the yard limit."""
    with rule_freeze_v1():
        instance = _case(4)
        instance.port.max_crane_height_tiers = height
        instance.port.yard.blocks = instance.port.yard.blocks[:1]
        block = instance.port.yard.blocks[0]
        block.n_bays = block.n_rows = 1
        block.max_tiers = instance.port.yard_max_tiers = 3
        instance.port.equipment[0].stack_tiers = 3
        result = simulate_port([_call(instance, simultaneous=True)],
                               load_simulator_params("laem_chabang_like"), seed=7, noise=0, until_s=1)
        assert effective_yard_tiers(instance.port, block) == 3
        assert len(result.final_yard.stacks[0].boxes) == 3
        assert len(result.external_queue) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_initial_yard_cannot_publish_an_overheight_stack_when_active(enabled):
    with rule_freeze_v1(enabled=enabled):
        instance = _case(3)
        instance.port.max_crane_height_tiers = 2
        instance.port.yard_max_tiers = 2
        yard = YardState(yard_id=instance.port.yard.id, stacks=[
            YardStack(key="A-001-01", length_ft=20, boxes=instance.load_list)])
        args = ([_call(instance)], load_simulator_params("laem_chabang_like"))
        if enabled:
            with pytest.raises(ValueError, match="initial yard.*height"):
                simulate_port(*args, seed=3, initial_yard=yard, until_s=1)
        else:
            assert simulate_port(*args, seed=3, initial_yard=yard, until_s=1).final_yard.stacks == yard.stacks


@pytest.mark.parametrize("enabled", [False, True])
def test_explicit_h_does_not_change_yard_placement_or_capacity(enabled):
    with rule_freeze_v1(enabled=enabled):
        instance = _case(3)
        instance.port.max_crane_height_tiers = 1
        instance.port.yard.blocks = instance.port.yard.blocks[:1]
        block = instance.port.yard.blocks[0]
        block.n_bays = block.n_rows = 1
        result = simulate_port([_call(instance, simultaneous=True)], load_simulator_params("laem_chabang_like"),
                               seed=3, noise=0, until_s=1)
        assert len(result.final_yard.stacks[0].boxes) == 3
        assert yard_statistics(instance, result.final_yard, instance.load_list)["occupancy_teu_fraction"] == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_gate_clustering_keeps_d25_classes_despite_different_exact_mass(enabled):
    with rule_freeze_v1(enabled=enabled):
        instance = _case(3)
        for box, mass, weight_class in zip(instance.containers, [10000, 12000, 11900], ["light", "heavy", "light"]):
            box.gross_mass_kg = mass
            box.weight_class = weight_class
        parameters = load_simulator_params("laem_chabang_like")
        assert "weight_class" in parameters.placement.cluster_keys
        yard = YardState(yard_id=instance.port.yard.id, stacks=[
            YardStack(key="A-001-01", length_ft=20, boxes=instance.load_list[:1]),
            YardStack(key="A-002-01", length_ft=20, boxes=instance.load_list[1:2])])
        result = simulate_port([_call(instance, simultaneous=True)], parameters, seed=4, noise=0,
                               initial_yard=yard, until_s=1)
        stacks = [s.boxes for s in result.final_yard.stacks]
        assert [instance.load_list[0], instance.load_list[2]] in stacks
        assert [instance.load_list[1]] in stacks


def _direct_case(*, enabled, imported):
    instance = _case(1)
    box = instance.containers[0]
    box.dg = build_instance().containers[1].dg.model_copy(update={"imdg_class": "1.4S"})
    box.direct_delivery = enabled
    from stowage.model.port import PortRuleRef
    instance.port.port_rules = [PortRuleRef(rule_id="port.dg.direct_delivery", params={"classes": ["1"]})]
    if imported:
        box.pod = instance.port.locode
        instance.stage = "B"
        instance.load_list = []
        instance.discharge_list = [box.id]
        instance.onboard = ShipState(vessel_id=instance.vessel.id,
                                    placements=[Placement(slot="0010182", container_id=box.id)])
    if imported or enabled:
        instance.yard = YardState(yard_id=instance.port.yard.id)
    else:
        instance.yard.stacks[0].key = "B-001-01"
    instance = Instance.model_validate(instance.model_dump())
    return _call(instance), box.id


def test_direct_delivery_cannot_enter_through_initial_yard_inventory():
    with rule_freeze_v1():
        call, cid = _direct_case(enabled=True, imported=False)
        yard = YardState(yard_id=call.instance.port.yard.id,
                         stacks=[YardStack(key="B-001-01", length_ft=20, boxes=[cid])])
        with pytest.raises(ValueError, match="initial yard.*direct-delivery"):
            simulate_port([call], load_simulator_params("laem_chabang_like"), seed=3, initial_yard=yard)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("imported", [False, True])
def test_direct_delivery_skips_yard_and_has_one_productive_lift(enabled, imported):
    with rule_freeze_v1(enabled=enabled):
        call, cid = _direct_case(enabled=enabled, imported=imported)
        result = simulate_port([call], load_simulator_params("laem_chabang_like"), seed=4, noise=0)
    assert not result.external_queue
    assert not result.unserved_loads
    assert not result.unserved_discharges
    cargo_moves = [record.move for record in result.moves if getattr(record.move, "container_id", None) == cid]
    productive = [move for move in cargo_moves if move.kind == ("discharge" if imported else "load")]
    assert len(productive) == 1
    if enabled:
        assert len(cargo_moves) == 1
        assert getattr(productive[0], "dst" if imported else "src") == "truck"
        assert all(cid not in stack.boxes for snapshot in result.snapshots for stack in snapshot.yard.stacks)
        assert all(cid not in stack.boxes for stack in result.final_yard.stacks)
    else:
        assert any(move.kind == ("gate_out" if imported else "gate_in") for move in cargo_moves)
