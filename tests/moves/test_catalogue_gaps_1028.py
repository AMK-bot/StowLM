"""Operational restow obligations and exact D15 projection accounting.

The synthetic geometry fixture certifies physical replay only. No hull approval
is inferred. FutureMoves is computed output, not caller-submitted accounting;
these tests therefore distinguish counting from rejection of an executable plan.
"""

import pytest

from stowage.model import Instance, MovePlan, ShipState
from stowage.model.fixtures import build_instance
from stowage.model.moves import (
    Discharge, HatchCoverOff, HatchCoverOn, RestowOff, RestowOn,
)
from stowage.moves import apply_plan, project_future_moves
from stowage.checker import check


def _two_blockers(hatch):
    instance = build_instance(layover=True)
    boxes = instance.containers[1:5]
    for index, box in enumerate(boxes):
        box.dg = None
        box.special_stowage = []
        box.oog = None
        box.pod = "THBKK" if index < 2 else "THSGZ"
    instance.containers = boxes
    instance.load_list = []
    instance.discharge_list = []
    instance.yard.stacks = []
    slots = ("0010102", "0030102", "0010182", "0030182") if hatch else (
        "0010102", "0030102", "0010104", "0030104")
    instance.onboard = ShipState(
        vessel_id=instance.vessel.id,
        placements=[dict(slot=slot, container_id=box.id) for slot, box in zip(slots, boxes)],
        tank_masses_kg=instance.onboard.tank_masses_kg,
    )
    return Instance.model_validate(instance.model_dump()), slots


def _arrival_and_moves(hatch):
    instance, slots = _two_blockers(hatch)
    instance.call_index = 1
    instance.port.locode = "THBKK"
    due_a, due_b, blocker_a, blocker_b = (box.id for box in instance.containers)
    instance.discharge_list = [due_a, due_b]
    instance = Instance.model_validate(instance.model_dump())
    off = [RestowOff(call_index=1, container_id=box, src=slot)
           for box, slot in zip((blocker_a, blocker_b), slots[2:])]
    discharges = [Discharge(call_index=1, container_id=box, src=slot, dst=destination)
                  for box, slot, destination in zip(
                      (due_a, due_b), slots[:2], ("A-003-01-01", "A-004-01-01"))]
    # Once a lower support is discharged, return stack blockers to the vacated
    # bottom cells. Returning to their original tier-04 cells would be illegal.
    return_slots = slots[2:] if hatch else slots[:2]
    on = [RestowOn(call_index=1, container_id=box, dst=slot)
          for box, slot in zip((blocker_a, blocker_b), return_slots)]
    opening = [HatchCoverOff(call_index=1, cover_id="HC-02-1")]
    closing = [HatchCoverOn(call_index=1, cover_id="HC-02-1")]
    moves = [*off, *opening, *discharges, *closing, *on] if hatch else (
        [*opening, *off, *discharges, *on, *closing])
    return instance, moves, off


def _plan(instance, moves):
    return MovePlan(id="catalogue.1028.restow", instance_id=instance.id,
                    call_index=instance.call_index, moves=moves)


@pytest.mark.parametrize("hatch", [False, True], ids=["stack", "hatch"])
def test_future_two_required_restows_are_fully_counted(hatch):
    instance, _ = _two_blockers(hatch)
    before = instance.model_dump_json()
    projected = project_future_moves(instance, instance.onboard, from_call=0)
    # Two distinct blockers require four lifts, not zero or one off/on pair.
    # Both due hold boxes use the same cover, so that cover is opened once.
    assert [(row.call_index, row.restowed_boxes, row.restow_lifts, row.hatch_cover_lifts)
            for row in projected] == [(1, 2, 4, 2), (2, 0, 0, 0 if hatch else 2)]
    replay = apply_plan(instance, _plan(instance, []))
    assert replay.legal, replay.violations
    assert replay.count.future == projected
    assert replay.count.weighted_nonproductive == (6 if hatch else 8)
    assert instance.model_dump_json() == before


@pytest.mark.parametrize("hatch", [False, True], ids=["stack", "hatch"])
def test_complete_required_restows_have_legal_supported_returns(hatch):
    instance, moves, _ = _arrival_and_moves(hatch)
    replay = apply_plan(instance, _plan(instance, moves))
    assert replay.legal, replay.violations
    assert (replay.count.discharges, replay.count.restow_lifts,
            replay.count.hatch_cover_lifts, replay.count.total_lifts_now) == (2, 4, 2, 8)
    assert replay.final_state.yard.quay_buffer == []
    assert replay.final_state.ship.covers_off == {}
    assert {p.container_id for p in replay.final_state.ship.placements} == {
        box.id for box in instance.containers[2:]}


@pytest.mark.parametrize("hatch", [False, True], ids=["stack", "hatch"])
@pytest.mark.parametrize("removed", [2, 1], ids=["absent", "undercounted"])
def test_absent_or_partial_required_restows_reject_discharge(hatch, removed):
    instance, moves, off = _arrival_and_moves(hatch)
    omitted = off[-removed:]
    incomplete = [move for move in moves if move not in omitted]
    before = instance.model_dump_json()
    replay = apply_plan(instance, _plan(instance, incomplete))
    assert not replay.legal
    # Physical access rejects the first blocked operation. The catalogue's
    # rotation IDs are not emitted here: access belongs to these move rules.
    rule = "expert.moves.hatch_clear" if hatch else "expert.moves.top_access"
    assert replay.violations[0].rule_id == rule
    assert replay.violations[0].move_index is not None
    assert all(v.rule_id in {"expert.moves.top_access", "expert.moves.hatch_clear"}
               and v.severity == "hard" for v in replay.violations)
    assert replay.executed < len(incomplete)
    assert replay.count.restow_lifts == (0 if removed == 2 else 1)
    assert instance.model_dump_json() == before


@pytest.mark.parametrize("hatch", [False, True], ids=["stack", "hatch"])
@pytest.mark.xfail(strict=True, reason='stage B: executable future restows (10.4/11.2), D15')
def test_future_overstow_without_explicit_restow_sequence_is_rejected(hatch):
    instance, _ = _two_blockers(hatch)
    # The single-call plan contains no restows at this or any later call. D15
    # computes four future restow lifts, but does not construct legal positions,
    # action order or an executable future policy. This remains a Stage B
    # requirement; the catalogue discloses the current D15 approximation.
    report = check(instance, _plan(instance, []))
    assert report.parts["moves"].ran
    assert not report.parts["moves"].violations
    assert report.count is not None
    assert report.count.future[0].restowed_boxes == 2
    assert report.count.future[0].restow_lifts == 4
    rule = "expert.rotation.hatch_overstow" if hatch else "expert.rotation.no_overstow"
    assert any(v.rule_id == rule and v.severity == "hard" for v in report.violations)
