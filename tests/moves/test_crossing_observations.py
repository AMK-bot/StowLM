"""D62 crossing observations count attempted lifts, corrective executed rehandles."""
from __future__ import annotations

import json

import pytest

from stowage.conventions import SYNTHETIC_OWNER_CODES, format_slot, make_container_id
from stowage.model import ApplyResult, MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import Load, Relocate
from stowage.model.ship import Placement
from stowage.model.yard import YardStack
from stowage.moves import ObservedApplyResult, apply_plan, count_moves
from stowage.rules.catalogue import rule_freeze_v1


COUNTERS = ("ship_lifts_at_or_above_h", "yard_lifts_at_or_above_h", "rejected_crossing_lifts",
            "yard_h_blocking_rehandles")


def scenario():
    instance = build_instance()
    template = instance.containers[2]
    instance.containers = [template.model_copy(update={
        "id": make_container_id(SYNTHETIC_OWNER_CODES[0], index), "dg": None,
        "special_stowage": [], "oog": None, "pod": "THBKK"}) for index in range(1, 33)]
    instance.load_list = [box.id for box in instance.containers]
    instance.yard.stacks = []
    instance.onboard.placements = []
    instance.port.max_crane_height_tiers = 4
    instance.port.quay_crane.max_on_deck_tiers = 4
    instance.port.yard_max_tiers = 4
    instance.port.equipment[0].stack_tiers = 4
    for block in instance.port.yard.blocks:
        block.max_tiers = 4
        block.n_rows = 5
    # Four real deck positions, counted from the stack base rather than tier82.
    for stack in instance.vessel.stacks:
        if not stack.on_deck:
            continue
        source = next(cell for cell in instance.vessel.cells if cell.id == stack.cell_ids[0])
        for index, tier in enumerate((86, 88), 2):
            new_id = format_slot(stack.bay, stack.row, tier)
            instance.vessel.cells.append(source.model_copy(update={
                "id": new_id, "vcg_base_m": stack.base_z_m + index * 2.591}))
            stack.cell_ids.append(new_id)
        stack.max_height_m = 12.0
    return instance


def cid(instance, index):
    return instance.containers[index].id


def yard(instance, indices, key, *, length=20):
    instance.yard.stacks.append(YardStack(key=key, boxes=[cid(instance, index) for index in indices],
                                        length_ft=length))


def deck(instance, indices, *, bay=1, row=2, length=20):
    for index, tier in zip(indices, (82, 84, 86, 88)):
        instance.containers[index].length_ft = length
        instance.onboard.placements.append(Placement(container_id=cid(instance, index),
                                                     slot=format_slot(bay, row, tier)))


def plan(instance, *moves):
    return MovePlan(id="crossing.observations", instance_id=instance.id, call_index=0, moves=list(moves))


def observations(result, *, applicable=True, ship=0, yard=0, rejected=0, rehandles=0):
    metric = result.d62_crossing
    assert metric.status == ("applicable" if applicable else "not_applicable")
    assert metric.source == "migrated_move_engine"
    expected = (ship, yard, rejected, rehandles)
    assert tuple(getattr(metric, name) for name in COUNTERS) == expected
    serialized = json.loads(result.model_dump_json())["d62_crossing"]
    assert serialized["status"] == metric.status and serialized["source"] == metric.source
    assert tuple(serialized[name] for name in COUNTERS) == expected


@pytest.mark.parametrize("enabled", [False, True])
def test_one_failed_lift_observes_ship_and_yard_blockers_independently(enabled):
    instance = scenario()
    yard(instance, [0], "A-001-04")
    yard(instance, [1, 2, 3, 4], "A-001-02")
    yard(instance, [9, 10, 11, 12], "A-001-01")
    deck(instance, [5, 6, 7, 8])
    move = Load(call_index=0, container_id=cid(instance, 0), src="A-001-04-01", dst="0010182")
    with rule_freeze_v1(enabled=enabled):
        result = apply_plan(instance, plan(instance, move))
    if enabled:
        assert not result.legal and result.count.total_lifts_now == 0
        observations(result, ship=1, yard=1, rejected=1)
    else:
        assert result.legal and result.count.loads == 1
        observations(result, applicable=False)


@pytest.mark.parametrize("length", [40, 45])
def test_long_footprint_multiple_blockers_count_once_per_domain_and_attempt(length):
    instance = scenario()
    instance.containers[0].length_ft = length
    yard(instance, [0], "A-001-04", length=length)
    for key, indices in (("A-001-01", range(1, 5)), ("A-002-01", range(5, 9)),
                         ("A-001-02", range(9, 13)), ("A-002-02", range(13, 17))):
        yard(instance, indices, key)
    deck(instance, range(17, 21), bay=2, length=length)
    move = Load(call_index=0, container_id=cid(instance, 0), src="A-001-04-01", dst="0020182")
    with rule_freeze_v1():
        result = apply_plan(instance, plan(instance, move))
    assert not result.legal
    observations(result, ship=1, yard=1, rejected=1)


@pytest.mark.parametrize("enabled", [False, True])
def test_successful_H_blocker_rehandle_then_load_is_counted_once(enabled):
    instance = scenario()
    yard(instance, [0], "A-001-03")
    yard(instance, [1, 2, 3, 4], "A-001-02")
    move = Relocate(call_index=0, container_id=cid(instance, 4), src="A-001-02-04", dst="A-002-02-01")
    load = Load(call_index=0, container_id=cid(instance, 0), src="A-001-03-01", dst="0010282")
    with rule_freeze_v1(enabled=enabled):
        result = apply_plan(instance, plan(instance, move, load))
    assert result.legal and result.count.relocations == 1 and result.count.loads == 1
    observations(result, applicable=enabled, rehandles=int(enabled))


def test_rehandle_of_unrelated_H_stack_is_not_a_blocker_rehandle():
    instance = scenario()
    yard(instance, [0], "A-001-03")
    yard(instance, [1, 2, 3, 4], "A-003-02")
    move = Relocate(call_index=0, container_id=cid(instance, 4), src="A-003-02-04", dst="A-004-02-01")
    load = Load(call_index=0, container_id=cid(instance, 0), src="A-001-03-01", dst="0010282")
    with rule_freeze_v1():
        result = apply_plan(instance, plan(instance, move, load))
    assert result.legal and result.count.relocations == 1
    observations(result)


def test_rejected_H_blocker_rehandle_never_counts_as_executed_correction():
    instance = scenario()
    yard(instance, [0], "A-001-05")
    yard(instance, [1, 2, 3, 4], "A-001-02")
    yard(instance, [5, 6, 7, 8], "A-001-03")
    move = Relocate(call_index=0, container_id=cid(instance, 4), src="A-001-02-04", dst="A-001-04-01")
    with rule_freeze_v1():
        result = apply_plan(instance, plan(instance, move))
    assert not result.legal and result.count.relocations == 0
    observations(result, yard=1, rejected=1)


def test_diagnostic_replay_counts_repeated_attempts_but_only_successful_rehandle():
    instance = scenario()
    yard(instance, [0], "A-001-05")
    yard(instance, [1, 2, 3, 4], "A-001-02")
    yard(instance, [5, 6, 7, 8], "A-001-03")
    blocked = Relocate(call_index=0, container_id=cid(instance, 4), src="A-001-02-04", dst="A-001-04-01")
    clear = Relocate(call_index=0, container_id=cid(instance, 4), src="A-001-02-04", dst="A-002-02-01")
    with rule_freeze_v1():
        stopped = apply_plan(instance, plan(instance, blocked, blocked, clear))
        continued = apply_plan(instance, plan(instance, blocked, blocked, clear), stop_at_first=False)
    assert not stopped.legal and stopped.count.relocations == 0
    observations(stopped, yard=1, rejected=1)
    assert not continued.legal and continued.count.relocations == 1
    observations(continued, yard=2, rejected=2, rehandles=1)


def test_non_crossing_rejection_does_not_increment_crossing_rejections():
    instance = scenario()
    yard(instance, [0], "A-001-03")
    yard(instance, [1, 2, 3, 4], "A-001-02")
    # It is on an H-blocked lane, but the incorrect source tier causes a
    # top-access rejection before there is any valid crane lift to observe.
    wrong = Load(call_index=0, container_id=cid(instance, 0), src="A-001-03-02", dst="0010182")
    with rule_freeze_v1():
        result = apply_plan(instance, plan(instance, wrong))
    assert not result.legal and result.violations[0].rule_id == "expert.moves.top_access"
    assert result.d62_crossing.rejected_crossing_lifts == 0


def test_unknown_ship_H_retains_known_yard_observations_and_serialization_is_pure():
    instance = scenario()
    instance.port.max_crane_height_tiers = None
    yard(instance, [0], "A-001-01")
    moves = plan(instance, Load(call_index=0, container_id=cid(instance, 0), src="A-001-01-01", dst="0010182"))
    before = instance.model_dump_json(), moves.model_dump_json()
    with rule_freeze_v1():
        first = apply_plan(instance, moves)
        second = apply_plan(instance, moves)
    assert first.legal and second.legal
    observations(first)
    assert first.model_dump_json() == second.model_dump_json()
    assert (instance.model_dump_json(), moves.model_dump_json()) == before


def test_active_observations_are_deterministic_and_leave_plan_instance_untouched():
    instance = scenario()
    yard(instance, [0], "A-001-03")
    yard(instance, [1, 2, 3, 4], "A-001-02")
    move = Load(call_index=0, container_id=cid(instance, 0), src="A-001-03-01", dst="0010182")
    moves = plan(instance, move)
    before = instance.model_dump_json(), moves.model_dump_json()
    with rule_freeze_v1():
        first = apply_plan(instance, moves)
        second = apply_plan(instance, moves)
    observations(first, yard=1, rejected=1)
    assert first.model_dump_json() == second.model_dump_json()
    assert (instance.model_dump_json(), moves.model_dump_json()) == before


def test_observed_result_roundtrips_without_changing_objective_counts():
    instance = scenario()
    yard(instance, [0], "A-001-03")
    yard(instance, [1, 2, 3, 4], "A-001-02")
    moves = plan(instance,
        Relocate(call_index=0, container_id=cid(instance, 4), src="A-001-02-04", dst="A-002-02-01"),
        Load(call_index=0, container_id=cid(instance, 0), src="A-001-03-01", dst="0010282"))
    with rule_freeze_v1():
        result = apply_plan(instance, moves)
        assert count_moves(instance, moves) == result.count
    assert isinstance(result, ApplyResult)
    assert ObservedApplyResult.model_validate_json(result.model_dump_json()) == result
    legacy = ApplyResult.model_validate(result.model_dump(exclude={"d62_crossing"}))
    assert legacy.count == result.count and legacy.final_state == result.final_state


@pytest.mark.parametrize("length", [40, 45])
def test_long_H_source_rehandle_clears_pending_export_through_second_footprint_bay(length):
    instance = scenario()
    yard(instance, [0], "A-002-03")
    for index in (1, 2, 3, 4):
        instance.containers[index].length_ft = length
    yard(instance, [1, 2, 3, 4], "A-001-02", length=length)
    move = Relocate(call_index=0, container_id=cid(instance, 4), src="A-001-02-04", dst="A-003-02-01")
    load = Load(call_index=0, container_id=cid(instance, 0), src="A-002-03-01", dst="0010282")
    with rule_freeze_v1():
        result = apply_plan(instance, plan(instance, move, load))
    assert result.legal and result.count.relocations == result.count.loads == 1
    # Bay2 is owned by the long stack addressed in bay1. Lowering that single
    # physical stack is one correction, irrespective of its two-cell footprint.
    observations(result, rehandles=1)


def test_digout_of_exports_own_H_stack_is_not_a_crossing_blocker_rehandle():
    instance = scenario()
    yard(instance, [0, 1, 2, 3], "A-001-02")
    move = Relocate(call_index=0, container_id=cid(instance, 3), src="A-001-02-04", dst="A-002-02-01")
    with rule_freeze_v1():
        result = apply_plan(instance, plan(instance, move))
    assert result.legal and result.count.relocations == 1
    # Exposing the export below is an ordinary top-access dig-out: the source
    # stack itself is excluded from the export's truck-lane crossing path.
    observations(result)


def test_empty_active_plan_has_applicable_zero_observations():
    instance = scenario()
    with rule_freeze_v1():
        result = apply_plan(instance, plan(instance))
    assert result.legal and result.count.total_lifts_now == 0
    observations(result)


def test_wrong_source_before_crossing_check_has_zero_exposures_in_both_domains():
    instance = scenario()
    yard(instance, [0], "A-001-03")
    yard(instance, [1, 2, 3, 4], "A-001-02")
    deck(instance, [5, 6, 7, 8])
    wrong = Load(call_index=0, container_id=cid(instance, 0), src="A-001-03-02", dst="0010182")
    with rule_freeze_v1():
        result = apply_plan(instance, plan(instance, wrong))
    assert not result.legal and result.violations[0].rule_id == "expert.moves.top_access"
    assert result.count.total_lifts_now == 0
    observations(result)
