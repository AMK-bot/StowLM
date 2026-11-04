"""A8: independent quay H and per-block effective yard crossing heights."""
from __future__ import annotations

import pytest

from stowage.conventions import SYNTHETIC_OWNER_CODES, format_slot, make_container_id
from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import Load, Relocate
from stowage.model.ship import Placement
from stowage.model.yard import YardStack
from stowage.moves import IllegalMoveError, apply_move, apply_plan, crossing_paths, initial_state
from stowage.moves._port_rules import port_move_violation
from stowage.moves._sequencing import pending_lift_rows
from stowage.ports import effective_yard_tiers, load_port
from stowage.rules.catalogue import rule_freeze_v1


def scenario(*, ship_height=8, yard_height=5):
    instance = build_instance()
    template = instance.containers[2]
    instance.containers = [template.model_copy(update={
        "id": make_container_id(SYNTHETIC_OWNER_CODES[0], serial), "dg": None,
        "special_stowage": [], "oog": None, "pod": "THBKK"}) for serial in range(1, 33)]
    instance.load_list = [box.id for box in instance.containers]
    instance.yard.stacks = []
    instance.port.max_crane_height_tiers = ship_height
    instance.port.quay_crane.max_on_deck_tiers = ship_height if ship_height is not None else 8
    instance.port.yard_max_tiers = yard_height
    instance.port.equipment[0].stack_tiers = yard_height
    for block in instance.port.yard.blocks:
        block.max_tiers = yard_height
        block.n_rows = 5
    for stack in instance.vessel.stacks:
        if not stack.on_deck:
            continue
        source = next(cell for cell in instance.vessel.cells if cell.id == stack.cell_ids[0])
        for index, tier in enumerate(range(86, 98, 2), 2):
            new_id = format_slot(stack.bay, stack.row, tier)
            instance.vessel.cells.append(source.model_copy(update={
                "id": new_id, "vcg_base_m": stack.base_z_m + index * 2.591}))
            stack.cell_ids.append(new_id)
        stack.max_height_m = 25.0
        stack.max_mass_kg = 200000.0
    return instance


def cid(instance, index):
    return instance.containers[index].id


def yard(instance, indices, key):
    instance.yard.stacks.append(YardStack(key=key, length_ft=20, boxes=[cid(instance, i) for i in indices]))


def plan(instance, *moves):
    return MovePlan(id="yard.height.amendment", instance_id=instance.id, call_index=0, moves=list(moves))


def assert_blocked(instance, move, *, rule, limit):
    with pytest.raises(IllegalMoveError) as caught:
        apply_move(instance, initial_state(instance), move)
    assert caught.value.violation.rule_id == rule
    assert caught.value.violation.limit == limit


@pytest.mark.parametrize("enabled", [False, True])
def test_laem_profile_keeps_yard_five_independent_of_quay_eight(enabled):
    with rule_freeze_v1(enabled=enabled):
        port = load_port("laem_chabang_like")
        assert port.max_crane_height_tiers == (8 if enabled else None)
        assert port.quay_crane.max_on_deck_tiers == (8 if enabled else 7)
        assert port.yard_max_tiers == 5
        assert {effective_yard_tiers(port, block) for block in port.yard.blocks} == {5}
        assert all(equipment.stack_tiers == 5 for equipment in port.equipment)


@pytest.mark.parametrize("enabled", [False, True])
def test_small_feeder_profile_retains_quay_four_and_yard_three(enabled):
    with rule_freeze_v1(enabled=enabled):
        port = load_port("small_feeder")
        assert port.max_crane_height_tiers == port.quay_crane.max_on_deck_tiers == 4
        assert port.yard_max_tiers == 3
        assert {effective_yard_tiers(port, block) for block in port.yard.blocks} == {3}


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("kind", ["load", "relocate"])
@pytest.mark.parametrize("ship_height,yard_height", [(8, 5), (4, 3)])
def test_yard_stack_at_its_own_limit_blocks_below_quay_H(enabled, kind, ship_height, yard_height):
    instance = scenario(ship_height=ship_height, yard_height=yard_height)
    yard(instance, [0], "A-001-03")
    yard(instance, range(1, yard_height + 1), "A-001-02")
    move = (Load(call_index=0, container_id=cid(instance, 0), src="A-001-03-01", dst="0010282")
            if kind == "load" else Relocate(call_index=0, container_id=cid(instance, 0),
                                             src="A-001-03-01", dst="A-001-01-01"))
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            assert_blocked(instance, move, rule="port.e3.yard_crossing", limit=yard_height)
        else:
            apply_move(instance, initial_state(instance), move)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("height", [7, 8])
def test_quay_crossing_still_uses_H_eight_instead_of_yard_five(enabled, height):
    instance = scenario()
    yard(instance, [0], "A-001-01")
    for index, tier in zip(range(1, height + 1), range(82, 98, 2)):
        instance.onboard.placements.append(Placement(container_id=cid(instance, index), slot=format_slot(1, 2, tier)))
    move = Load(call_index=0, container_id=cid(instance, 0), src="A-001-01-01", dst="0010182")
    with rule_freeze_v1(enabled=enabled):
        if enabled and height == 8:
            assert_blocked(instance, move, rule="port.e3.quay_crossing", limit=8)
        else:
            apply_move(instance, initial_state(instance), move)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("cap,expected", [("none", 5), ("block", 3), ("port", 4), ("equipment", 2)])
def test_shared_effective_yard_helper_keeps_all_yard_caps_and_omits_H(enabled, cap, expected):
    instance = scenario(ship_height=1)
    if cap == "block":
        instance.port.yard.blocks[0].max_tiers = 3
    elif cap == "port":
        instance.port.yard_max_tiers = 4
    elif cap == "equipment":
        instance.port.equipment[0].stack_tiers = 2
    with rule_freeze_v1(enabled=enabled):
        block = instance.port.yard.blocks[0]
        assert effective_yard_tiers(instance.port, block) == expected
        assert effective_yard_tiers(instance.port, block.id) == expected


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("a_height,b_height,blocking_limit", [(3, 4, 3), (2, 5, 5), (2, 4, None)])
def test_between_blocks_uses_each_crossed_blocks_own_yard_threshold(enabled, a_height, b_height, blocking_limit):
    instance = scenario()
    instance.port.yard.blocks[0].max_tiers = 3
    yard(instance, [0], "A-001-03")
    yard(instance, range(1, a_height + 1), "A-001-02")
    yard(instance, range(10, 10 + b_height), "B-001-02")
    move = Relocate(call_index=0, container_id=cid(instance, 0), src="A-001-03-01", dst="B-001-03-01")
    with rule_freeze_v1(enabled=enabled):
        if enabled and blocking_limit is not None:
            assert_blocked(instance, move, rule="port.e3.yard_crossing", limit=blocking_limit)
        else:
            apply_move(instance, initial_state(instance), move)


@pytest.mark.parametrize("enabled", [False, True])
def test_known_yard_crossing_applies_even_when_ship_H_is_unknown(enabled):
    instance = scenario(ship_height=None)
    yard(instance, [0], "A-001-03")
    yard(instance, range(1, 6), "A-001-02")
    move = Load(call_index=0, container_id=cid(instance, 0), src="A-001-03-01", dst="0010282")
    with rule_freeze_v1(enabled=enabled):
        path = crossing_paths(instance, move)
        assert path.ship_stack_ids == ()
        if enabled:
            assert ("A", 1, 2) in path.yard_ground_slots
            assert_blocked(instance, move, rule="port.e3.yard_crossing", limit=5)
            result = apply_plan(instance, plan(instance, move))
            assert result.d62_crossing.status == "applicable"
            assert result.d62_crossing.yard_lifts_at_or_above_h == 1
            assert result.d62_crossing.ship_lifts_at_or_above_h == 0
        else:
            assert path.yard_ground_slots == ()
            apply_move(instance, initial_state(instance), move)


def test_five_high_yard_blocker_rehandle_counts_with_quay_H_eight():
    instance = scenario()
    yard(instance, [0], "A-001-03")
    yard(instance, range(1, 6), "A-001-02")
    relocate = Relocate(call_index=0, container_id=cid(instance, 5), src="A-001-02-05", dst="A-002-02-01")
    load = Load(call_index=0, container_id=cid(instance, 0), src="A-001-03-01", dst="0010282")
    with rule_freeze_v1():
        result = apply_plan(instance, plan(instance, relocate, load))
    assert result.legal and result.count.relocations == result.count.loads == 1
    assert result.d62_crossing.yard_h_blocking_rehandles == 1
    assert result.d62_crossing.yard_lifts_at_or_above_h == result.d62_crossing.rejected_crossing_lifts == 0


@pytest.mark.parametrize("ship_blocked", [False, True])
def test_observations_use_five_for_yard_and_eight_for_ship(ship_blocked):
    instance = scenario()
    yard(instance, [0], "A-001-03")
    yard(instance, range(1, 6), "A-001-02")
    if ship_blocked:
        for index, tier in zip(range(6, 14), range(82, 98, 2)):
            instance.onboard.placements.append(Placement(
                container_id=cid(instance, index), slot=format_slot(1, 2, tier)))
    load = Load(call_index=0, container_id=cid(instance, 0), src="A-001-03-01", dst="0010182")
    with rule_freeze_v1():
        result = apply_plan(instance, plan(instance, load))
    assert not result.legal and result.count.total_lifts_now == 0
    assert result.d62_crossing.yard_lifts_at_or_above_h == 1
    assert result.d62_crossing.ship_lifts_at_or_above_h == int(ship_blocked)
    assert result.d62_crossing.rejected_crossing_lifts == 1
    assert result.violations[0].limit == (8 if ship_blocked else 5)


@pytest.mark.parametrize("ship_height", [8, None])
def test_yard_amendment_does_not_turn_C5_helper_into_a_crossing_checker(ship_height):
    instance = scenario(ship_height=ship_height)
    yard(instance, [0], "A-001-03")
    yard(instance, range(1, 6), "A-001-02")
    move = Load(call_index=0, container_id=cid(instance, 0), src="A-001-03-01", dst="0010282")
    with rule_freeze_v1():
        assert port_move_violation(instance, move) is None
        assert_blocked(instance, move, rule="port.e3.yard_crossing", limit=5)


@pytest.mark.parametrize("ship_height", [8, None])
def test_pending_lift_rows_stays_ship_only_with_independent_yard_height(ship_height):
    instance = scenario(ship_height=ship_height)
    yard(instance, [0], "A-001-03")
    yard(instance, range(1, 6), "A-001-02")
    domains = {cid(instance, 0): ["0010182"]}
    with rule_freeze_v1():
        expected = (1,) if ship_height is not None else ()
        assert pending_lift_rows(instance, initial_state(instance), 1, 2, pending_load_targets=domains) == expected
