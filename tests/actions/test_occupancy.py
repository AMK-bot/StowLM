"""Occupied destinations cannot corrupt the incremental action expander."""

from __future__ import annotations

import copy

import pytest

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Place, RestowOff
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves import apply_move, initial_state


INCOMING, OCCUPANT = "QZXU0000014", "QZXU0000020"


def _fixture(incoming_length: int, occupant_length: int) -> Instance:
    data = build_instance().model_dump()
    for box in data["containers"]:
        if box["id"] in (INCOMING, OCCUPANT):
            length = incoming_length if box["id"] == INCOMING else occupant_length
            box["length_ft"] = length
            box["height_m"] = 2.591
            box["iso_type"] = ("22" if length == 20 else "42") + ("R1" if box["reefer"] else "G1")
    for stack in data["yard"]["stacks"]:
        if INCOMING in stack["boxes"]:
            stack["length_ft"] = incoming_length
        if OCCUPANT in stack["boxes"]:
            stack["length_ft"] = occupant_length
    return Instance.model_validate(data)


@pytest.mark.parametrize("origin", ["yard", "quay", "ship"])
@pytest.mark.parametrize("incoming_length, occupant_length, occupied_slot, target", [
    (20, 20, "0010182", "0010182"),
    (40, 20, "0010182", "0020182"),
    (40, 20, "0030182", "0020182"),
    (20, 40, "0020182", "0010182"),
    (20, 40, "0020182", "0030182"),
    (40, 40, "0020182", "0020182"),
])
def test_occupied_place_rejects_before_mutating(origin, incoming_length, occupant_length,
                                               occupied_slot, target):
    expander = Expander(_fixture(incoming_length, occupant_length))
    expander.step(Place(OCCUPANT, occupied_slot))
    if origin != "yard":
        source = "0010282" if incoming_length == 20 else "0020282"
        expander.step(Place(INCOMING, source))
        if origin == "quay":
            expander.step(RestowOff(INCOMING))
    before = copy.deepcopy(expander.__dict__)

    with pytest.raises(ValueError, match="occupied"):
        expander.step(Place(INCOMING, target))

    assert expander.__dict__ == before
    # The existing occupant remains safely removable after a rejected action.
    expander.step(RestowOff(OCCUPANT))
    assert expander.location(OCCUPANT) == ("quay", "quay")


def test_reported_overlap_sequence_raises_value_error():
    with pytest.raises(ValueError, match="occupied"):
        get_action_space().expand(_fixture(20, 20), [
            Place(INCOMING, "0010182"), Place(OCCUPANT, "0010182"),
            RestowOff(INCOMING), RestowOff(OCCUPANT),
        ])


def test_occupied_place_does_not_dig_out_yard_or_open_cover():
    data = build_instance().model_dump()
    buried, blocker = "QZXU0000035", "QZXU0000040"
    data["yard"]["stacks"] = [s for s in data["yard"]["stacks"] if blocker not in s["boxes"]]
    next(s for s in data["yard"]["stacks"] if buried in s["boxes"])["boxes"].append(blocker)
    expander = Expander(Instance.model_validate(data))
    expander.step(Place(OCCUPANT, "0010102"))
    expander.finish()
    before = copy.deepcopy(expander.__dict__)

    with pytest.raises(ValueError, match="occupied"):
        expander.step(Place(buried, "0010102"))

    assert expander.__dict__ == before


@pytest.mark.parametrize("length, slot", [(20, "0010182"), (40, "0020182")])
def test_same_slot_shift_keeps_self_occupancy_semantics(length, slot):
    instance = _fixture(length, 20)
    instance.stage = "B"  # D69: Stage B enables Place of an already aboard box.
    expander = Expander(instance)
    expander.step(Place(INCOMING, slot))
    moves = expander.step(Place(INCOMING, slot))
    assert [move.kind for move in moves] == ["restow_shift"]
    state = initial_state(instance)
    for move in expander.moves:
        state = apply_move(instance, state, move)
    assert [(p.container_id, p.slot) for p in state.ship.placements] == [(INCOMING, slot)]
