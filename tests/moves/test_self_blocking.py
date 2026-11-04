"""D69/A7 pending-lift geometry, checked against executable lift sequences."""
from __future__ import annotations

import pytest

from stowage.model.fixtures import build_instance
from stowage.conventions import format_slot, stack_id
from stowage.model.moves import Discharge, HatchCoverOff, HatchCoverOn, Load, RestowOn
from stowage.model.ship import Placement
from stowage.moves import IllegalMoveError, apply_move, initial_state
from stowage.moves._sequencing import pending_lift_rows
from stowage.rules.catalogue import rule_freeze_v1


def scenario(*, layover=False):
    instance = build_instance(layover=layover)
    instance.port.max_crane_height_tiers = 2
    instance.port.quay_crane.max_on_deck_tiers = 2
    instance.port.yard_max_tiers = 2
    instance.port.equipment[0].stack_tiers = 2
    for block in instance.port.yard.blocks:
        block.max_tiers = 2
    return instance


def cid(instance, index):
    return instance.containers[index].id


def aboard(instance, index, slot):
    box_id = cid(instance, index)
    instance.yard.stacks = [stack for stack in instance.yard.stacks if box_id not in stack.boxes]
    instance.onboard.placements.append(Placement(container_id=box_id, slot=slot))


def blocked(instance, state, move):
    with pytest.raises(IllegalMoveError) as error:
        apply_move(instance, state, move)
    assert error.value.violation.rule_id == "port.e3.quay_crossing"


@pytest.mark.parametrize("enabled", [False, True])
def test_pending_far_load_predicts_crossing_after_near_stack_reaches_H(enabled):
    instance = scenario()
    aboard(instance, 2, "0010282")
    near = Load(call_index=0, container_id=cid(instance, 3), src="A-004-01-01", dst="0010284")
    far = Load(call_index=0, container_id=cid(instance, 1), src="B-001-01-01", dst="0010182")
    targets = {cid(instance, 1): (far.dst,)}
    state = initial_state(instance)
    with rule_freeze_v1(enabled=enabled):
        expected = (1,) if enabled else ()
        assert pending_lift_rows(instance, state, 1, 2, pending_load_targets=targets,
                                 placing_container_id=near.container_id) == expected
        raised = apply_move(instance, state, near)
        assert pending_lift_rows(instance, raised, 1, 2, pending_load_targets=targets) == expected
        if enabled:
            blocked(instance, raised, far)
        else:
            apply_move(instance, raised, far)
        # Far before near is executable with the very same destination slots.
        far_first = apply_move(instance, state, far)
        assert pending_lift_rows(instance, far_first, 1, 2, pending_load_targets=targets) == ()
        apply_move(instance, far_first, near)


@pytest.mark.parametrize("side", ["port", "starboard"])
@pytest.mark.parametrize("swapped_coordinates", [False, True])
def test_pending_rows_follow_berthing_and_actual_geometry(side, swapped_coordinates):
    instance = scenario()
    instance.port.berthing_side = side
    if swapped_coordinates:
        for cell in instance.vessel.cells:
            cell.tcg_m *= -1
    near_row = 2 if (side == "port") != swapped_coordinates else 1
    far_row = 1 if near_row == 2 else 2
    target = f"001{far_row:02d}82"
    with rule_freeze_v1():
        assert pending_lift_rows(instance, initial_state(instance), 1, near_row,
                                 pending_load_targets={cid(instance, 1): [target]}) == (far_row,)
        assert pending_lift_rows(instance, initial_state(instance), 1, far_row,
                                 pending_load_targets={cid(instance, 1): [target]}) == ()


def test_default_state_infers_due_discharge_and_held_cargo_cover_opening():
    instance = scenario(layover=True)
    aboard(instance, 2, "0010282")
    near = Load(call_index=0, container_id=cid(instance, 3), src="A-004-01-01", dst="0010284")
    cover_off = HatchCoverOff(call_index=0, cover_id="HC-02-1")
    state = initial_state(instance)
    with rule_freeze_v1():
        assert pending_lift_rows(instance, state, 1, 2) == (1,)
        apply_move(instance, state, cover_off)
        blocked(instance, apply_move(instance, state, near), cover_off)
        # The helper reports obligations, not the top-access legality of a due
        # box currently covered by another through box.
        assert pending_lift_rows(instance, state, 3, 2) == (1,)  # Same cover spans both sub-bays.


@pytest.mark.parametrize("enabled", [False, True])
def test_already_open_cover_requires_closure_even_without_cargo_domains(enabled):
    instance = scenario()
    instance.onboard.covers_off = {"HC-02-1": "quay"}
    aboard(instance, 2, "0010282")
    near = Load(call_index=0, container_id=cid(instance, 3), src="A-004-01-01", dst="0010284")
    close = HatchCoverOn(call_index=0, cover_id="HC-02-1")
    state = initial_state(instance)
    with rule_freeze_v1(enabled=enabled):
        assert pending_lift_rows(instance, state, 1, 2) == ((1,) if enabled else ())
        if enabled:
            blocked(instance, apply_move(instance, state, near), close)
        closed = apply_move(instance, state, close)
        assert pending_lift_rows(instance, closed, 1, 2) == ()
        apply_move(instance, closed, near)


def test_hold_load_target_groups_required_cover_geometry_with_its_option():
    instance = scenario()
    # The hold endpoint is on the quay side of the queried stack, but its cover
    # edge remains farther away. Only the required cover lift crosses the query.
    for cell in instance.vessel.cells:
        if cell.stack_id == "00101H":
            cell.tcg_m = 4.0
    with rule_freeze_v1():
        assert pending_lift_rows(instance, initial_state(instance), 1, 2,
            pending_load_targets={cid(instance, 1): ["0010102"]}) == (1,)
        # A currently closed deck target in the other bay needs no cover lift;
        # its existence means this container does not force the queried row.
        assert pending_lift_rows(instance, initial_state(instance), 1, 2,
            pending_load_targets={cid(instance, 1): ["0010102", "0030182"]}) == ()
        # An other-bay HOLD target shares cover 1: that alternative still needs
        # the cover operation crossing bay 1, so it is not an escape.
        assert pending_lift_rows(instance, initial_state(instance), 1, 2,
            pending_load_targets={cid(instance, 1): ["0010102", "0030102"]}) == (1,)


@pytest.mark.parametrize("targets", [None, {}, "missing", "empty"])
def test_unknown_missing_or_empty_load_domains_do_not_create_obligations(targets):
    instance = scenario()
    if targets == "missing":
        targets = {cid(instance, 2): ["0030282"]}
    elif targets == "empty":
        targets = {cid(instance, 1): []}
    with rule_freeze_v1():
        assert pending_lift_rows(instance, initial_state(instance), 1, 2, pending_load_targets=targets) == ()


def test_optional_empty_slots_do_not_become_pending_lifts():
    instance = scenario()
    # Plenty of farther empty slots exist; without a pending box's exhaustive
    # target domain they establish no obligation.
    with rule_freeze_v1():
        assert pending_lift_rows(instance, initial_state(instance), 1, 2) == ()


def test_any_other_bay_alternative_suppresses_that_boxes_report():
    instance = scenario()
    state = initial_state(instance)
    with rule_freeze_v1():
        assert pending_lift_rows(instance, state, 1, 2,
            pending_load_targets={cid(instance, 1): ["0010182", "0030182"]}) == ()
        # Domains describe eventual viability, including a currently occupied
        # target. The helper must not filter it using current slot occupancy.
        state.ship.placements = [Placement(container_id=cid(instance, 2), slot="0030182")]
        assert pending_lift_rows(instance, state, 1, 2,
            pending_load_targets={cid(instance, 1): ["0010182", "0030182"]}) == ()


@pytest.mark.parametrize("length", [40, 45])
def test_long_candidate_queries_union_of_both_subbay_footprints(length):
    instance = scenario()
    instance.containers[0].length_ft = length
    next(stack for stack in instance.yard.stacks if cid(instance, 0) in stack.boxes).length_ft = length
    aboard(instance, 2, "0010282")
    aboard(instance, 3, "0030282")
    near = Load(call_index=0, container_id=cid(instance, 0), src="A-001-01-01", dst="0020284")
    domains = {cid(instance, 1): ["0010182", "0030182"]}
    state = initial_state(instance)
    with rule_freeze_v1():
        # Neither single support stack cuts every alternative. Their union does.
        assert pending_lift_rows(instance, state, 1, 2, pending_load_targets=domains) == ()
        assert pending_lift_rows(instance, state, 3, 2, pending_load_targets=domains) == ()
        assert pending_lift_rows(instance, state, 2, 2, pending_load_targets=domains,
                                 placing_container_id=cid(instance, 0)) == (1,)
        raised = apply_move(instance, state, near)
        for target in domains[cid(instance, 1)]:
            blocked(instance, raised, Load(call_index=0, container_id=cid(instance, 1),
                                            src="B-001-01-01", dst=target))
        far = Load(call_index=0, container_id=cid(instance, 1), src="B-001-01-01", dst="0010182")
        apply_move(instance, apply_move(instance, state, far), near)


def test_quay_buffer_returns_need_explicit_domains_and_are_pending_after_load_list_removal():
    instance = scenario()
    box_id = cid(instance, 1)
    instance.load_list.remove(box_id)
    instance.yard.stacks = [stack for stack in instance.yard.stacks if box_id not in stack.boxes]
    instance.yard.quay_buffer = [box_id]
    with rule_freeze_v1():
        state = initial_state(instance)
        assert pending_lift_rows(instance, state, 1, 2) == ()
        assert pending_lift_rows(instance, state, 1, 2, pending_load_targets={box_id: ["0010182"]}) == (1,)
        returned = apply_move(instance, state, RestowOn(call_index=0, container_id=box_id, dst="0010182"))
        assert pending_lift_rows(instance, returned, 1, 2, pending_load_targets={box_id: ["0010182"]}) == ()


def test_already_discharged_boxes_and_placing_container_are_ignored():
    instance = scenario()
    box_id = cid(instance, 1)
    aboard(instance, 1, "0010182")
    instance.load_list.remove(box_id)
    instance.containers[1].pod = instance.port.locode
    instance.discharge_list = [box_id]
    with rule_freeze_v1():
        state = initial_state(instance)
        assert pending_lift_rows(instance, state, 1, 2) == (1,)
        state = apply_move(instance, state, Discharge(call_index=0, container_id=box_id,
                                                     src="0010182", dst="B-002-01-01"))
        assert pending_lift_rows(instance, state, 1, 2) == ()
        other = cid(instance, 2)
        assert pending_lift_rows(instance, state, 1, 2,
            pending_load_targets={other: ["0010182"]}, placing_container_id=other) == ()


def test_report_is_sorted_deduplicated_pure_and_skips_unknown_height():
    instance = scenario()
    # Add one fixed-deck centre row as another physically farther endpoint.
    source_stack = next(stack for stack in instance.vessel.stacks if stack.id == "00101D")
    centre_ids = [format_slot(1, 0, int(cell[-2:])) for cell in source_stack.cell_ids]
    instance.vessel.stacks.append(source_stack.model_copy(update={
        "id": stack_id(1, 0, True), "row": 0, "cell_ids": centre_ids, "hatch_cover_id": None}))
    for old_id, new_id in zip(source_stack.cell_ids, centre_ids):
        source = next(cell for cell in instance.vessel.cells if cell.id == old_id)
        instance.vessel.cells.append(source.model_copy(update={
            "id": new_id, "stack_id": stack_id(1, 0, True), "tcg_m": 0.0}))
    state = initial_state(instance)
    # Distinct tiers produce the same pending endpoint row; the helper reports
    # row obligations once and never changes cargo, covers, or input domains.
    targets = {cid(instance, 1): ["0010184", "0010182"], cid(instance, 2): ["0010082"]}
    before = instance.model_dump_json(), state.model_dump_json(), repr(targets)
    with rule_freeze_v1():
        assert pending_lift_rows(instance, state, 1, 2, pending_load_targets=targets) == (0, 1)
        assert (instance.model_dump_json(), state.model_dump_json(), repr(targets)) == before
        instance.port.max_crane_height_tiers = None
        assert pending_lift_rows(instance, state, 1, 2, pending_load_targets=targets) == ()
