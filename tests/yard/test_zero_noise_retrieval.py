"""D72's idealized zero-noise separation also governs retrieval rehandles."""
from dataclasses import dataclass

import pytest

from stowage.conventions import make_container_id
from stowage.model.fixtures import build_instance
from stowage.model.moves import Relocate
from stowage.model.ship import ShipState, WorldState
from stowage.model.yard import YardStack, YardState
from stowage.moves import apply_move
from stowage.ports import load_simulator_params
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import Retrieval, ShipCall, simulate_port


def retrieval_case(*, bays=2, one_slot=True):
    base = build_instance()
    base.port.yard.blocks = base.port.yard.blocks[:1]
    base.port.yard.blocks[0].n_bays = bays
    base.port.yard.blocks[0].n_rows = 1
    boxes = [base.containers[2].model_copy(update={
        "id": make_container_id("QZXU", 9900 + i), "pod": "SGSIN",
        "category": None, "special_stowage": [], "oog": None,
        "weight_class": "light",
    }) for i in range(3)]
    a = base.model_copy(deep=True)
    a.containers = boxes[:2]
    a.load_list = [box.id for box in boxes[:2]]
    a.discharge_list = []
    a.onboard = ShipState(vessel_id=a.vessel.id)
    b = a.model_copy(deep=True)
    b.containers = boxes[2:]
    b.load_list = [boxes[2].id]
    if one_slot:
        for cell in a.vessel.cells:
            cell.accepts_20 = cell.id == "0010102"
    calls = [ShipCall("a", a, 100, 90, {boxes[0].id: 0, boxes[1].id: 1}),
             ShipCall("b", b, 2000, 1900, {boxes[2].id: 2})]
    return calls, boxes


def assert_replay_separated(calls, result, *, initial_yard=None):
    """Check every transient world, not only the final yard after loading."""
    exports = {cid: call.id for call in calls for cid in call.instance.load_list}
    contexts = {call.id: call.instance.model_copy(update={
        "containers": [box for c in calls for box in c.instance.containers]}) for call in calls}
    ships = {call.id: call.instance.onboard for call in calls}
    yard = initial_yard or YardState(yard_id=calls[0].instance.port.yard.id)
    for record in result.moves:
        instance = contexts[record.call_id]
        state = WorldState(call_index=instance.call_index, yard=yard, ship=ships[record.call_id])
        if record.move.kind == "relocate":
            destination = next((s for s in yard.stacks
                                if s.key == record.move.dst.rsplit("-", 1)[0]), None)
            assert not destination or all(
                below not in exports or exports.get(record.move.container_id) == exports[below]
                for below in destination.boxes)
        state = apply_move(instance, state, record.move)
        yard, ships[record.call_id] = state.yard, state.ship
    yard.time_s = result.final_yard.time_s
    assert yard == result.final_yard


@pytest.mark.parametrize("one_slot", [True, False], ids=["later-load-unserved", "transient-only"])
@pytest.mark.parametrize("explicit_noise", [True, False], ids=["override", "configured"])
def test_review_empty_yard_relocation_does_not_mix_foreign_exports(one_slot, explicit_noise):
    calls, boxes = retrieval_case(one_slot=one_slot)
    parameters = load_simulator_params("laem_chabang_like")
    parameters = parameters.model_copy(update={"placement": parameters.placement.model_copy(
        update={"cluster_misplacement_probability": 0})})
    with rule_freeze_v1():
        result = simulate_port(calls, parameters, seed=1,
                               noise=0 if explicit_noise else None,
                               congestion_mix_share=1, until_s=101)
        assert_replay_separated(calls, result)
    assert boxes[0].id in result.unserved_loads  # No safe dig-out destination.


def test_default_retrieval_tries_separate_destination_past_nearer_foreign_export():
    calls, boxes = retrieval_case(bays=3, one_slot=False)
    with rule_freeze_v1():
        result = simulate_port(calls, load_simulator_params("laem_chabang_like"),
                               seed=1, noise=0, until_s=101)
        assert_replay_separated(calls, result)
    relocations = [record.move for record in result.moves if record.move.kind == "relocate"]
    assert relocations and relocations[0].dst == "A-003-01-01"
    assert not result.unserved_loads


@dataclass
class ForeignBurialHook:
    blocker: str

    def retrieve(self, instance, state, container_id):
        if container_id != instance.load_list[0]:
            return Retrieval(state, (), "A-001-01-02")
        # A fabricated returned state cannot authorize a forbidden destination.
        move = Relocate(call_index=instance.call_index, container_id=self.blocker,
                        src="A-001-01-02", dst="A-002-01-02")
        return Retrieval(state.model_copy(update={"yard": YardState(yard_id=state.yard.yard_id)}),
                         (move,), "A-001-01-01")


def test_custom_hook_cannot_commit_foreign_burial_at_zero_noise():
    calls, boxes = retrieval_case(one_slot=False)
    with rule_freeze_v1():
        result = simulate_port(calls, load_simulator_params("laem_chabang_like"), seed=1,
                               noise=0, until_s=101, dig_out=ForeignBurialHook(boxes[1].id))
        assert_replay_separated(calls, result)
    assert boxes[0].id in result.unserved_loads
    assert not any(record.move.kind == "relocate" for record in result.moves)


def test_switch_off_retains_legacy_foreign_relocation():
    calls, boxes = retrieval_case()
    with rule_freeze_v1(enabled=False):
        result = simulate_port(calls, load_simulator_params("laem_chabang_like"),
                               seed=1, noise=0, until_s=101)
    relocations = [record.move for record in result.moves if record.move.kind == "relocate"]
    assert relocations[0].container_id == boxes[1].id
    assert relocations[0].dst == "A-002-01-02"


def test_custom_hook_checks_each_evolving_destination_before_committing():
    calls, boxes = retrieval_case(bays=3, one_slot=False)
    imported = boxes[1]
    target, foreign = boxes[0], boxes[2]
    first = calls[0].instance
    first.load_list = [target.id]
    calls[0] = ShipCall("a", first, 100, 90, {target.id: 0})
    yard = YardState(yard_id=first.port.yard.id, stacks=[
        YardStack(key="A-001-01", length_ft=20,
                  boxes=[target.id, imported.id, foreign.id])])

    class TwoStepHook:
        def retrieve(self, instance, state, container_id):
            moves = (Relocate(call_index=instance.call_index, container_id=foreign.id,
                              src="A-001-01-03", dst="A-002-01-01"),
                     Relocate(call_index=instance.call_index, container_id=imported.id,
                              src="A-001-01-02", dst="A-002-01-02"))
            # Both moves are physically legal. The second mixes only after the
            # first is applied; inspecting the initial yard would miss this.
            for move in moves:
                state = apply_move(instance, state, move)
            return Retrieval(state, moves, "A-001-01-01")

    with rule_freeze_v1():
        result = simulate_port(calls, load_simulator_params("laem_chabang_like"),
                               seed=1, noise=0, until_s=101, initial_yard=yard,
                               dig_out=TwoStepHook())
        assert_replay_separated(calls, result, initial_yard=yard)
    relocations = [record.move for record in result.moves if record.move.kind == "relocate"]
    assert len(relocations) == 1
    assert relocations[0].container_id == foreign.id
    assert target.id in result.unserved_loads


def test_import_relocation_cannot_bury_own_export_hidden_below_another_import():
    from stowage.yard.dig_out import preserves_export_separation

    calls, boxes = retrieval_case()
    target, incoming, prior = boxes
    yard = YardState(yard_id=calls[0].instance.port.yard.id, stacks=[
        YardStack(key="A-001-01", length_ft=20, boxes=[target.id, prior.id])])
    state = WorldState(call_index=0, yard=yard, ship=calls[0].instance.onboard)
    # incoming is registered as an import, even though it belongs to target's call.
    assert not preserves_export_separation(state, incoming.id, "A-001-01-03",
                                           export_owners={target.id: "a"})
    assert preserves_export_separation(state, incoming.id, "A-002-01-01",
                                       export_owners={target.id: "a"})
    # D25 still permits same-call export clusters.
    assert preserves_export_separation(state, incoming.id, "A-001-01-03",
                                       export_owners={target.id: "a", incoming.id: "a"})


def test_zero_noise_admission_keeps_unserved_exports_separate_under_capacity_pressure():
    calls, boxes = retrieval_case(bays=1, one_slot=False)
    calls[1] = ShipCall("b", calls[1].instance, 2000, 1900, {boxes[2].id: 110})
    with rule_freeze_v1():
        result = simulate_port(calls, load_simulator_params("laem_chabang_like"),
                               seed=1, noise=0, until_s=111)
    assert boxes[0].id in result.unserved_loads
    assert result.external_queue == [boxes[2].id]
    assert [stack.boxes for stack in result.final_yard.stacks] == [[boxes[0].id]]
    assert not any(record.move.kind == "gate_in" and record.move.container_id == boxes[2].id
                   for record in result.moves)
