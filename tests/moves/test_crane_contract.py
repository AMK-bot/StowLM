"""C6/A2 hand-authored paths and replay examples, both activation settings."""
from __future__ import annotations

import pytest

from stowage.conventions import SYNTHETIC_OWNER_CODES, format_slot, make_container_id, stack_id
from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import (
    Discharge, GateIn, GateOut, HatchCoverOff, HatchCoverOn, Load,
    Relocate, RestowOff, RestowOn, RestowShift,
)
from stowage.model.ship import Placement
from stowage.model.yard import YardStack
from stowage.moves import IllegalMoveError, apply_move, apply_plan, count_moves, initial_state
from stowage.moves._paths import crossing_paths
from stowage.rules.catalogue import rule_freeze_v1


def scenario(height=2):
    instance = build_instance()
    template = instance.containers[2]
    instance.containers = [template.model_copy(update={
        "id": make_container_id(SYNTHETIC_OWNER_CODES[0], i), "dg": None,
        "special_stowage": [], "oog": None, "pod": "THBKK"}) for i in range(1, 21)]
    instance.load_list = [box.id for box in instance.containers]
    instance.yard.stacks = []
    instance.port.max_crane_height_tiers = height
    instance.port.quay_crane.max_on_deck_tiers = height
    instance.port.yard_max_tiers = height
    instance.port.equipment[0].stack_tiers = height
    for block in instance.port.yard.blocks:
        block.max_tiers = height
        block.n_rows = 5
    # Explicit transverse geometry, including centre and additional outer rows.
    cells, stacks, covers = list(instance.vessel.cells), list(instance.vessel.stacks), list(instance.vessel.hatch_covers)
    for row, y in ((0, 0), (3, -2.6), (4, 2.6)):
        for stack in instance.vessel.stacks:
            if stack.row != 1:
                continue
            sid = stack_id(stack.bay, row, stack.on_deck)
            new_ids = [format_slot(stack.bay, row, int(cid[-2:])) for cid in stack.cell_ids]
            stacks.append(stack.model_copy(update={"id": sid, "row": row, "cell_ids": new_ids,
                                                    "hatch_cover_id": f"HC-02-{row}"}))
            for cid, new_id in zip(stack.cell_ids, new_ids):
                cell = next(cell for cell in instance.vessel.cells if cell.id == cid)
                cells.append(cell.model_copy(update={"id": new_id, "stack_id": sid, "tcg_m": y}))
        cover = instance.vessel.hatch_covers[0]
        covers.append(cover.model_copy(update={"id": f"HC-02-{row}", "tcg_m": y,
            "hold_stack_ids": [stack_id(bay, row, False) for bay in (1, 3)],
            "deck_stack_ids": [stack_id(bay, row, True) for bay in (1, 3)]}))
    instance.vessel.cells, instance.vessel.stacks, instance.vessel.hatch_covers = cells, stacks, covers
    return instance


def cid(instance, index):
    return instance.containers[index].id


def aboard(instance, index, slot):
    instance.onboard.placements.append(Placement(container_id=cid(instance, index), slot=slot))


def yard(instance, indices, key, length=20):
    instance.yard.stacks.append(YardStack(key=key, length_ft=length,
                                        boxes=[cid(instance, i) for i in indices]))


def rejected(instance, move, rule):
    state = initial_state(instance)
    before = state.model_dump_json()
    with pytest.raises(IllegalMoveError) as error:
        apply_move(instance, state, move, move_index=8)
    assert error.value.violation.rule_id == rule
    assert error.value.violation.move_index == 8
    assert state.model_dump_json() == before


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("kind", ["load", "discharge", "restow_off", "restow_on"])
def test_ship_quay_crossing_for_every_container_lift(enabled, kind):
    instance = scenario()
    aboard(instance, 1, "0010282")
    aboard(instance, 2, "0010284")
    if kind == "load":
        yard(instance, [0], "A-001-01")
        move = Load(call_index=0, container_id=cid(instance, 0), src="A-001-01-01", dst="0010182")
    elif kind == "restow_on":
        instance.yard.quay_buffer = [cid(instance, 0)]
        move = RestowOn(call_index=0, container_id=cid(instance, 0), dst="0010182")
    else:
        aboard(instance, 0, "0010182")
        if kind == "discharge":
            instance.containers[0].pod = instance.port.locode
            instance.discharge_list = [cid(instance, 0)]
            move = Discharge(call_index=0, container_id=cid(instance, 0), src="0010182", dst="A-001-01-01")
        else:
            move = RestowOff(call_index=0, container_id=cid(instance, 0), src="0010182")
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            rejected(instance, move, "port.e3.quay_crossing")
        else:
            assert crossing_paths(instance, move).ship_stack_ids == ()
            apply_move(instance, initial_state(instance), move)


def test_geometry_uses_y_coordinates_and_berthing_side():
    instance = scenario()
    # Deliberately swap the conventional row coordinates; the path must follow y.
    for cell in instance.vessel.cells:
        if cell.id[3:5] == "02":
            cell.tcg_m = -1.8
    move = RestowOff(call_index=0, container_id=cid(instance, 0), src="0010182")
    with rule_freeze_v1():
        assert crossing_paths(instance, move).ship_stack_ids == ("00100D", "00104D")
        instance.port.berthing_side = "starboard"
        assert crossing_paths(instance, move).ship_stack_ids == ("00102D", "00103D")


@pytest.mark.parametrize("length", [40, 45])
def test_ship_long_lift_checks_both_twenty_subbays(length):
    instance = scenario()
    instance.containers[0].length_ft = length
    yard(instance, [0], "A-001-01", length)
    aboard(instance, 1, "0030282")
    aboard(instance, 2, "0030284")
    move = Load(call_index=0, container_id=cid(instance, 0), src="A-001-01-01", dst="0020182")
    with rule_freeze_v1():
        assert {"00102D", "00302D"} <= set(crossing_paths(instance, move).ship_stack_ids)
        rejected(instance, move, "port.e3.quay_crossing")


@pytest.mark.parametrize("same_bay", [True, False])
def test_shift_direct_same_bay_and_via_quay_between_bays(same_bay):
    instance = scenario()
    move = RestowShift(call_index=0, container_id=cid(instance, 0), src="0010182",
                       dst="0010382" if same_bay else "0030382")
    with rule_freeze_v1():
        path = crossing_paths(instance, move).ship_stack_ids
        if same_bay:
            assert path == ()  # Adjacent starboard rows: no trip to the quay.
        else:
            assert "00102D" in path and "00302D" in path and "00301D" in path
    aboard(instance, 0, "0010182")
    aboard(instance, 1, "0010282")
    aboard(instance, 2, "0010284")
    with rule_freeze_v1():
        if same_bay:
            apply_move(instance, initial_state(instance), move)
        else:
            rejected(instance, move, "port.e3.quay_crossing")


def test_same_bay_shift_crosses_only_intermediate_rows_and_excludes_endpoints():
    instance = scenario()
    aboard(instance, 0, "0010182")
    aboard(instance, 1, "0010082")
    aboard(instance, 2, "0010084")
    move = RestowShift(call_index=0, container_id=cid(instance, 0), src="0010182", dst="0010282")
    with rule_freeze_v1():
        assert crossing_paths(instance, move).ship_stack_ids == ("00100D",)
        rejected(instance, move, "port.e3.quay_crossing")


@pytest.mark.parametrize("kind", ["hatch_cover_off", "hatch_cover_on"])
@pytest.mark.parametrize("enabled", [False, True])
def test_cover_crossing_uses_nearest_cover_edge(kind, enabled):
    instance = scenario()
    aboard(instance, 1, "0010282")
    aboard(instance, 2, "0010284")
    if kind == "hatch_cover_off":
        move = HatchCoverOff(call_index=0, cover_id="HC-02-1")
    else:
        instance.onboard.covers_off = {"HC-02-1": "quay"}
        move = HatchCoverOn(call_index=0, cover_id="HC-02-1")
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            rejected(instance, move, "port.e3.quay_crossing")
        else:
            apply_move(instance, initial_state(instance), move)


def test_hold_cargo_never_blocks_quay_crossing():
    instance = scenario()
    aboard(instance, 1, "0010202")
    aboard(instance, 2, "0010204")
    yard(instance, [0], "A-001-01")
    move = Load(call_index=0, container_id=cid(instance, 0), src="A-001-01-01", dst="0010182")
    with rule_freeze_v1():
        apply_move(instance, initial_state(instance), move)


@pytest.mark.parametrize("enabled", [False, True])
def test_expert_height_blocker_requires_one_extra_shift(enabled):
    instance = scenario()
    aboard(instance, 1, "0010282")
    aboard(instance, 2, "0010284")
    yard(instance, [0], "A-001-01")
    load = Load(call_index=0, container_id=cid(instance, 0), src="A-001-01-01", dst="0010182")
    shift = RestowShift(call_index=0, container_id=cid(instance, 2), src="0010284", dst="0010482")
    def plan(*moves):
        return MovePlan(id="worked", instance_id=instance.id, call_index=0, moves=list(moves))
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            failed = apply_plan(instance, plan(load))
            assert not failed.legal and failed.count.total_lifts_now == 0
            result = count_moves(instance, plan(shift, load))
            assert result.restow_lifts == 1 and result.loads == 1 and result.total_lifts_now == 2
        else:
            assert count_moves(instance, plan(load)).total_lifts_now == 1


@pytest.mark.parametrize("kind", ["load", "discharge", "gate_in", "gate_out"])
@pytest.mark.parametrize("side", ["low", "high"])
@pytest.mark.parametrize("enabled", [False, True])
def test_yard_truck_lane_paths_block_in_both_directions(kind, side, enabled):
    instance = scenario()
    instance.port.yard.blocks[0].truck_lane_side = side
    row = 1 if side == "low" else 5
    yard(instance, [1, 2], f"A-001-{row:02d}")
    address = "A-001-03-01"
    if kind in ("load", "gate_out"):
        yard(instance, [0], "A-001-03")
        move = (Load(call_index=0, container_id=cid(instance, 0), src=address, dst="0010482")
                if kind == "load" else GateOut(call_index=0, container_id=cid(instance, 0), src=address))
    elif kind == "gate_in":
        move = GateIn(call_index=0, container_id=cid(instance, 0), dst=address)
    else:
        aboard(instance, 0, "0010482")
        instance.containers[0].pod = instance.port.locode
        instance.discharge_list = [cid(instance, 0)]
        move = Discharge(call_index=0, container_id=cid(instance, 0), src="0010482", dst=address)
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            rejected(instance, move, "port.e3.yard_crossing")
        else:
            apply_move(instance, initial_state(instance), move)


@pytest.mark.parametrize("source,target,blocker", [
    ("A-001-01", "A-001-05", "A-001-03"),
    ("A-001-01", "A-005-05", "A-001-03"),
    ("A-001-01", "A-005-05", "A-003-05"),
    ("A-001-03", "B-005-03", "A-001-01"),
    ("A-001-03", "B-005-03", "B-005-01"),
])
@pytest.mark.parametrize("enabled", [False, True])
def test_yard_relocation_all_binding_paths(source, target, blocker, enabled):
    instance = scenario()
    yard(instance, [0], source)
    yard(instance, [1, 2], blocker)
    move = Relocate(call_index=0, container_id=cid(instance, 0), src=source + "-01", dst=target + "-01")
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            rejected(instance, move, "port.e3.yard_crossing")
        else:
            apply_move(instance, initial_state(instance), move)


@pytest.mark.parametrize("length", [40, 45])
@pytest.mark.parametrize("kind", ["relocate", "gate_out"])
def test_long_yard_paths_check_second_footprint_bay(length, kind):
    instance = scenario()
    instance.containers[0].length_ft = length
    yard(instance, [0], "A-001-03", length)
    yard(instance, [1, 2], "A-002-02")
    move = (Relocate(call_index=0, container_id=cid(instance, 0), src="A-001-03-01", dst="A-001-01-01")
            if kind == "relocate" else GateOut(call_index=0, container_id=cid(instance, 0), src="A-001-03-01"))
    with rule_freeze_v1():
        rejected(instance, move, "port.e3.yard_crossing")


def test_yard_footprint_resolves_long_obstruction_and_excludes_endpoints():
    instance = scenario()
    for index in (1, 2):
        instance.containers[index].length_ft = 40
    yard(instance, [0], "A-002-03")
    yard(instance, [1, 2], "A-001-02", 40)
    move = GateOut(call_index=0, container_id=cid(instance, 0), src="A-002-03-01")
    with rule_freeze_v1():
        rejected(instance, move, "port.e3.yard_crossing")
    instance.yard.stacks = []
    yard(instance, [1, 2], "A-001-02", 40)
    move = GateOut(call_index=0, container_id=cid(instance, 2), src="A-001-02-02")
    with rule_freeze_v1():
        apply_move(instance, initial_state(instance), move)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("limit", ["H", "block", "port", "equipment"])
def test_every_effective_yard_height_limit_remains_active(limit, enabled):
    instance = scenario()
    # A8 keeps every yard cap but removes quay H from their minimum.
    if limit == "H":
        instance.port.max_crane_height_tiers = 1
    elif limit == "block":
        instance.port.yard.blocks[0].max_tiers = 1
    elif limit == "port":
        instance.port.yard_max_tiers = 1
    else:
        instance.port.equipment[0].stack_tiers = 1
    yard(instance, [0], "A-001-01")
    yard(instance, [1], "A-002-01")
    move = Relocate(call_index=0, container_id=cid(instance, 0), src="A-001-01-01", dst="A-002-01-02")
    with rule_freeze_v1(enabled=enabled):
        if limit != "H":
            rejected(instance, move, "port.e3.yard_height")
        else:
            apply_move(instance, initial_state(instance), move)


@pytest.mark.parametrize("enabled", [False, True])
def test_ship_governing_height_uses_tiers_from_base(enabled):
    instance = scenario()
    instance.port.max_crane_height_tiers = 1
    aboard(instance, 1, "0010482")
    yard(instance, [0], "A-001-01")
    move = Load(call_index=0, container_id=cid(instance, 0), src="A-001-01-01", dst="0010484")
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            rejected(instance, move, "port.e3.quay_height")
        else:
            apply_move(instance, initial_state(instance), move)


def test_unknown_height_profiles_have_no_crossing_screen():
    instance = scenario()
    move = RestowOff(call_index=0, container_id=cid(instance, 0), src="0010182")
    unknown = instance.port.model_copy(update={"profile_kind": "next_call_environment",
        "max_crane_height_tiers": None, "quay_crane": None, "yard_max_tiers": None})
    with rule_freeze_v1():
        assert crossing_paths(instance, move, port=unknown).ship_stack_ids == ()


@pytest.mark.parametrize("domain", ["ship", "yard"])
@pytest.mark.parametrize("enabled", [False, True])
def test_governing_height_limits_source_reach_too(domain, enabled):
    instance = scenario()
    instance.port.max_crane_height_tiers = 1
    if domain == "ship":
        aboard(instance, 0, "0010482")
        aboard(instance, 1, "0010484")
        move = RestowOff(call_index=0, container_id=cid(instance, 1), src="0010484")
        rule = "port.e3.quay_height"
    else:
        yard(instance, [0, 1], "A-001-01")
        move = GateOut(call_index=0, container_id=cid(instance, 1), src="A-001-01-02")
        rule = "port.e3.yard_height"
    with rule_freeze_v1(enabled=enabled):
        if enabled and domain == "ship":
            rejected(instance, move, rule)
        else:
            apply_move(instance, initial_state(instance), move)


def test_same_bay_long_shift_unions_both_subbay_paths():
    instance = scenario()
    instance.containers[0].length_ft = 40
    aboard(instance, 0, "0020182")
    aboard(instance, 1, "0030082")
    aboard(instance, 2, "0030084")
    move = RestowShift(call_index=0, container_id=cid(instance, 0), src="0020182", dst="0020282")
    with rule_freeze_v1():
        assert crossing_paths(instance, move).ship_stack_ids == ("00100D", "00300D")
        rejected(instance, move, "port.e3.quay_crossing")


@pytest.mark.parametrize("domain", ["ship", "yard"])
def test_inherited_stack_above_H_also_blocks(domain):
    instance = scenario()
    instance.port.max_crane_height_tiers = 1
    if domain == "ship":
        aboard(instance, 0, "0010182")
        aboard(instance, 1, "0010282")
        aboard(instance, 2, "0010284")
        move = RestowOff(call_index=0, container_id=cid(instance, 0), src="0010182")
        rule = "port.e3.quay_crossing"
    else:
        instance.port.yard.blocks[0].max_tiers = 1
        yard(instance, [0], "A-001-03")
        yard(instance, [1, 2], "A-001-02")
        move = GateOut(call_index=0, container_id=cid(instance, 0), src="A-001-03-01")
        rule = "port.e3.yard_crossing"
    with rule_freeze_v1():
        rejected(instance, move, rule)


def fixed_row(instance, row, *, tiers=(82, 84), length=20):
    """Move one synthetic row into the immutable background geometry catalogue."""
    from stowage.model.vessel import FixedCargoBox, FixedLoad

    instance.vessel.fixed_load_cells = [cell for cell in instance.vessel.cells if parse_row(cell.id) == row]
    instance.vessel.cells = [cell for cell in instance.vessel.cells if parse_row(cell.id) != row]
    instance.vessel.fixed_load_stacks = [stack for stack in instance.vessel.stacks if stack.row == row]
    instance.vessel.stacks = [stack for stack in instance.vessel.stacks if stack.row != row]
    instance.vessel.fixed_load_hatch_covers = [cover for cover in instance.vessel.hatch_covers
                                             if cover.id == f"HC-02-{row}"]
    instance.vessel.hatch_covers = [cover for cover in instance.vessel.hatch_covers
                                  if cover.id != f"HC-02-{row}"]
    boxes = []
    for tier in tiers:
        supports = [cell for cell in instance.vessel.fixed_load_cells
                    if cell.id == format_slot(1, row, tier) or
                    (length != 20 and cell.id == format_slot(3, row, tier))]
        boxes.append(FixedCargoBox(cell_ids=[cell.id for cell in supports], length_ft=length,
            height_m=2.591, gross_mass_kg=10000.0,
            x_m=sum(cell.lcg_m for cell in supports) / len(supports),
            y_m=supports[0].tcg_m, z_m=supports[0].vcg_base_m + 2.591 / 2,
            stack_id=stack_id(1, row, tier >= 80), tier=tier))
    instance.vessel.fixed_loads.append(FixedLoad(kind="cargo", cargo_boxes=boxes,
        mass_kg=10000.0 * len(boxes), x_m=sum(box.x_m for box in boxes) / len(boxes),
        y_m=sum(box.y_m for box in boxes) / len(boxes), z_m=sum(box.z_m for box in boxes) / len(boxes)))


def parse_row(cell_id):
    return int(cell_id[3:5])


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("length", [20, 40, 45])
def test_fixed_background_deck_stack_at_H_blocks_crossing(enabled, length):
    instance = scenario()
    fixed_row(instance, 2, length=length)
    aboard(instance, 0, "0030182" if length != 20 else "0010182")
    move = RestowOff(call_index=0, container_id=cid(instance, 0),
                     src="0030182" if length != 20 else "0010182")
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            if length != 20:
                assert "00302D" in crossing_paths(instance, move).ship_stack_ids
            rejected(instance, move, "port.e3.quay_crossing")
        else:
            apply_move(instance, initial_state(instance), move)


def test_fixed_hold_cargo_does_not_block_crossing():
    instance = scenario()
    fixed_row(instance, 2, tiers=(2, 4))
    aboard(instance, 0, "0010182")
    move = RestowOff(call_index=0, container_id=cid(instance, 0), src="0010182")
    with rule_freeze_v1():
        apply_move(instance, initial_state(instance), move)


def test_hold_endpoint_uses_its_actual_transverse_coordinate():
    instance = scenario()
    for cell in instance.vessel.cells:
        if cell.stack_id == "00101H":
            cell.tcg_m = 4.0
    instance.onboard.covers_off = {"HC-02-1": "quay"}
    aboard(instance, 0, "0010102")
    aboard(instance, 1, "0010282")
    aboard(instance, 2, "0010284")
    move = RestowOff(call_index=0, container_id=cid(instance, 0), src="0010102")
    with rule_freeze_v1():
        assert crossing_paths(instance, move).ship_stack_ids == ()
        apply_move(instance, initial_state(instance), move)


def test_deck_obstruction_uses_its_own_coordinate_when_hold_differs():
    instance = scenario()
    for cell in instance.vessel.cells:
        if cell.stack_id == "00102H":
            cell.tcg_m = -5.0
    # Explicitly put hold geometry last, exposing a (bay,row)-keyed alias.
    instance.vessel.stacks.sort(key=lambda stack: not stack.on_deck)
    aboard(instance, 0, "0010182")
    aboard(instance, 1, "0010282")
    aboard(instance, 2, "0010284")
    move = RestowOff(call_index=0, container_id=cid(instance, 0), src="0010182")
    with rule_freeze_v1():
        assert "00102D" in crossing_paths(instance, move).ship_stack_ids
        rejected(instance, move, "port.e3.quay_crossing")


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("kind", ["off", "on"])
def test_activated_hatch_lifts_use_quay_landing_only(enabled, kind):
    instance = scenario()
    if kind == "off":
        move = HatchCoverOff(call_index=0, cover_id="HC-02-1", dst="HC-02-2")
    else:
        instance.onboard.covers_off = {"HC-02-1": "HC-02-2"}
        move = HatchCoverOn(call_index=0, cover_id="HC-02-1")
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            rejected(instance, move, "expert.moves.hatch_clear")
        else:
            apply_move(instance, initial_state(instance), move)


@pytest.mark.parametrize("enabled", [False, True])
def test_fixed_deck_cargo_on_same_named_cover_prevents_cover_lift(enabled):
    instance = scenario()
    fixed_row(instance, 2, tiers=(82,))  # Below H still makes the cover immovable.
    fixed_cover = instance.vessel.fixed_load_hatch_covers[0]
    fixed_cover.id = "HC-02-1"
    for stack in instance.vessel.fixed_load_stacks:
        stack.hatch_cover_id = "HC-02-1"
    move = HatchCoverOff(call_index=0, cover_id="HC-02-1")
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            rejected(instance, move, "expert.moves.hatch_clear")
        else:
            apply_move(instance, initial_state(instance), move)


def test_cover_path_uses_combined_decision_and_fixed_cover_nearest_edge():
    instance = scenario()
    fixed_row(instance, 2, tiers=(2,))  # Empty deck, physically part of cover 1.
    instance.vessel.fixed_load_hatch_covers[0].id = "HC-02-1"
    for stack in instance.vessel.fixed_load_stacks:
        stack.hatch_cover_id = "HC-02-1"
    move = HatchCoverOff(call_index=0, cover_id="HC-02-1")
    with rule_freeze_v1():
        # Row 2 is now part of the lifted cover: its edge replaces row 1's edge,
        # so centre row 0 lies inside the cover span, outside the crossing path.
        assert crossing_paths(instance, move).ship_stack_ids == ("00104D", "00304D")


def empty_geometry(instance, stack_ids):
    """Retain legal zero-cell Stack records while removing their physical cells."""
    removed = {cell for stack in instance.vessel.stacks if stack.id in stack_ids for cell in stack.cell_ids}
    for stack in instance.vessel.stacks:
        if stack.id in stack_ids:
            stack.cell_ids = []
    instance.vessel.cells = [cell for cell in instance.vessel.cells if cell.id not in removed]


@pytest.mark.parametrize("kind", ["off", "on"])
@pytest.mark.parametrize("enabled", [False, True])
def test_cover_partial_empty_deck_uses_nonempty_deck_geometry(kind, enabled):
    instance = scenario()
    cover = next(cover for cover in instance.vessel.hatch_covers if cover.id == "HC-02-1")
    outer = next(cover for cover in instance.vessel.hatch_covers if cover.id == "HC-02-2")
    cover.deck_stack_ids.extend(outer.deck_stack_ids)
    cover.hold_stack_ids.extend(outer.hold_stack_ids)
    empty_geometry(instance, {"00102D"})
    if kind == "on":
        instance.onboard.covers_off = {cover.id: "quay"}
    move = (HatchCoverOff(call_index=0, cover_id=cover.id) if kind == "off"
            else HatchCoverOn(call_index=0, cover_id=cover.id))
    with rule_freeze_v1(enabled=enabled):
        expected = ("00100D", "00104D", "00304D") if enabled else ()
        assert crossing_paths(instance, move).ship_stack_ids == expected
        state = apply_move(instance, initial_state(instance), move)
        assert (cover.id in state.ship.covers_off) == (kind == "off")
        moves = [move, HatchCoverOn(call_index=0, cover_id=cover.id)] if kind == "off" else [move]
        plan = MovePlan(id="cover.partial.empty", instance_id=instance.id, call_index=0, moves=moves)
        assert apply_plan(instance, plan).legal


@pytest.mark.parametrize("kind", ["off", "on"])
@pytest.mark.parametrize("enabled", [False, True])
def test_cover_all_deck_geometry_empty_falls_back_to_nonempty_hold(kind, enabled):
    instance = scenario()
    cover = next(cover for cover in instance.vessel.hatch_covers if cover.id == "HC-02-1")
    empty_geometry(instance, set(cover.deck_stack_ids))
    if kind == "on":
        instance.onboard.covers_off = {cover.id: "quay"}
    move = (HatchCoverOff(call_index=0, cover_id=cover.id) if kind == "off"
            else HatchCoverOn(call_index=0, cover_id=cover.id))
    with rule_freeze_v1(enabled=enabled):
        expected = ("00100D", "00102D", "00104D", "00300D", "00302D", "00304D") if enabled else ()
        assert crossing_paths(instance, move).ship_stack_ids == expected
        state = apply_move(instance, initial_state(instance), move)
        assert (cover.id in state.ship.covers_off) == (kind == "off")
        moves = [move, HatchCoverOn(call_index=0, cover_id=cover.id)] if kind == "off" else [move]
        plan = MovePlan(id="cover.empty.deck", instance_id=instance.id, call_index=0, moves=moves)
        assert apply_plan(instance, plan).legal


@pytest.mark.parametrize("kind", ["off", "on"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("geometry", ["empty_stacks", "no_members", "one_bay_empty"])
def test_cover_no_member_geometry_has_hard_public_diagnostic(kind, enabled, geometry):
    instance = scenario()
    cover = next(cover for cover in instance.vessel.hatch_covers if cover.id == "HC-02-1")
    members = set(cover.deck_stack_ids + cover.hold_stack_ids)
    if geometry == "no_members":
        cover.deck_stack_ids = []
        cover.hold_stack_ids = []
    else:
        empty_geometry(instance, {sid for sid in members if geometry != "one_bay_empty" or sid.startswith("001")})
    if kind == "on":
        instance.onboard.covers_off = {cover.id: "quay"}
    move = (HatchCoverOff(call_index=0, cover_id=cover.id) if kind == "off"
            else HatchCoverOn(call_index=0, cover_id=cover.id))
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            with pytest.raises(IllegalMoveError) as error:
                crossing_paths(instance, move)
            violation = error.value.violation
            assert violation.rule_id == "expert.moves.hatch_clear"
            assert violation.severity == "hard"
            assert violation.call_index == 0
            assert cover.id in violation.message and "geometry" in violation.message.lower()
            rejected(instance, move, "expert.moves.hatch_clear")
            plan = MovePlan(id="cover.no.geometry", instance_id=instance.id, call_index=0, moves=[move])
            result = apply_plan(instance, plan)
            assert not result.legal and result.count.total_lifts_now == 0
            assert result.violations[0].rule_id == "expert.moves.hatch_clear"
            assert result.violations[0].move_index == 0
            assert cover.id in result.violations[0].message
        else:
            assert crossing_paths(instance, move).ship_stack_ids == ()
            apply_move(instance, initial_state(instance), move)
