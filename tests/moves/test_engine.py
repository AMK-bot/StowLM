"""Hand-counted crane lifts; these test physical replay, not departure stowage approval."""
from __future__ import annotations

import random
from time import perf_counter

import pytest

from stowage.model import Instance, MovePlan, ShipState
from stowage.model.fixtures import build_instance
from stowage.model.ship import Placement
from stowage.model.moves import (
    Discharge, GateIn, GateOut, HatchCoverOff, HatchCoverOn, Load, Relocate, RestowOff, RestowOn,
    RestowShift,
)
from stowage.moves.engine import (
    IllegalMoveError, apply_move, apply_plan, count_moves, initial_state, project_future_moves,
)


def plan(instance, *moves):
    return MovePlan(id="moves.test", instance_id=instance.id, call_index=0, moves=list(moves))


def validate(instance):
    return Instance.model_validate(instance.model_dump())


def ids(instance):
    return [box.id for box in instance.containers]


def assert_illegal(instance, state, move, rule):
    before = state.model_dump_json()
    with pytest.raises(IllegalMoveError) as caught:
        apply_move(instance, state, move, move_index=7)
    assert caught.value.violation.rule_id == rule
    assert caught.value.violation.move_index == 7
    assert state.model_dump_json() == before


def test_yard_rehandle_chain_has_exact_count_and_is_pure():
    instance = build_instance()
    _, a, b, c = ids(instance)
    instance.yard.stacks = [
        instance.yard.stacks[0].model_copy(update={"key": "A-003-01", "length_ft": 20,
                                                  "boxes": [a, b, c]}),
        next(s for s in instance.yard.stacks if s.length_ft == 40),
    ]
    instance = validate(instance)
    before = instance.model_dump_json()
    moves = plan(instance,
        Relocate(call_index=0, container_id=c, src="A-003-01-03", dst="A-004-01-01"),
        Relocate(call_index=0, container_id=b, src="A-003-01-02", dst="A-004-01-02"),
        Load(call_index=0, container_id=a, src="A-003-01-01", dst="0010182"),
    )
    result = apply_plan(instance, moves)
    assert result.legal, result.violations
    assert (result.count.loads, result.count.relocations, result.count.total_lifts_now) == (1, 2, 3)
    assert result.count.weighted_nonproductive == 2
    assert count_moves(instance, moves) == result.count
    assert instance.model_dump_json() == before
    assert_illegal(instance, initial_state(instance), moves.moves[2], "expert.moves.top_access")


def test_hold_load_requires_deck_restow_and_cover_lifts():
    instance = build_instance()
    _, a, b, _ = ids(instance)
    deck_stack = next(s for s in instance.yard.stacks if a in s.boxes)
    instance.yard.stacks.remove(deck_stack)
    instance.load_list.remove(a)
    instance.onboard.placements = [Placement(slot="0010182", container_id=a)]
    instance = validate(instance)
    hold_load = Load(call_index=0, container_id=b, src="A-003-01-01", dst="0010102")
    assert_illegal(instance, initial_state(instance), hold_load, "expert.moves.hatch_clear")
    assert_illegal(instance, initial_state(instance), HatchCoverOff(call_index=0, cover_id="HC-02-1"),
                   "expert.moves.hatch_clear")
    moves = plan(instance,
        RestowOff(call_index=0, container_id=a, src="0010182"),
        HatchCoverOff(call_index=0, cover_id="HC-02-1"), hold_load,
        HatchCoverOn(call_index=0, cover_id="HC-02-1"),
        RestowOn(call_index=0, container_id=a, dst="0010182"),
    )
    result = apply_plan(instance, moves)
    assert result.legal, result.violations
    # One productive load, one temporary box off/back, and cover off/back: five lifts.
    assert (result.count.loads, result.count.restow_lifts, result.count.hatch_cover_lifts) == (1, 2, 2)
    assert result.count.total_lifts_now == 5
    # Four now, then the later hold POD forces deck off/back and cover off/back.
    assert result.count.weighted_nonproductive == 8


def test_forty_over_two_twenty_cells_and_twenty_on_forty_rejected():
    instance = build_instance()
    forty, a, b, c = ids(instance)
    moves = plan(instance,
        Load(call_index=0, container_id=a, src="B-001-01-01", dst="0010182"),
        Load(call_index=0, container_id=b, src="A-003-01-01", dst="0030182"),
        Load(call_index=0, container_id=forty, src="A-001-01-01", dst="0020184"),
    )
    result = apply_plan(instance, moves)
    assert result.legal, result.violations
    assert result.count.loads == result.count.total_lifts_now == 3
    assert_illegal(instance, result.final_state,
        RestowOff(call_index=0, container_id=a, src="0010182"), "expert.moves.top_access")
    state = apply_move(instance, initial_state(instance),
                       Load(call_index=0, container_id=forty, src="A-001-01-01", dst="0020182"))
    assert_illegal(instance, state,
        Load(call_index=0, container_id=c, src="A-004-01-01", dst="0010184"),
        "expert.physical.no_20_on_40")


def test_gaps_and_cell_size_fit_are_rejected():
    instance = build_instance()
    forty, a, _, _ = ids(instance)
    assert_illegal(instance, initial_state(instance),
        Load(call_index=0, container_id=a, src="B-001-01-01", dst="0010184"),
        "expert.moves.bottom_up")
    instance.vessel.cells = [c.model_copy(update={"accepts_40": False}) if c.id == "0030182" else c
                             for c in instance.vessel.cells]
    assert_illegal(instance, initial_state(instance),
        Load(call_index=0, container_id=forty, src="A-001-01-01", dst="0020182"),
        "expert.physical.size_fit")


@pytest.mark.parametrize("limit", ["port", "equipment", "block"])
def test_effective_yard_tier_limit(limit):
    instance = build_instance()
    _, a, b, _ = ids(instance)
    if limit == "port":
        instance.port.yard_max_tiers = 1
    elif limit == "equipment":
        instance.port.equipment[0].stack_tiers = 1
    else:
        next(block for block in instance.port.yard.blocks if block.id == "A").max_tiers = 1
    assert_illegal(instance, initial_state(instance),
        Relocate(call_index=0, container_id=a, src="B-001-01-01", dst="A-003-01-02"),
        "port.e3.yard_height")


def test_current_port_quay_tier_limit_overrides_rotation():
    instance = build_instance()
    _, a, b, _ = ids(instance)
    instance.port.quay_crane.max_on_deck_tiers = 1
    instance.rotation.calls[0].max_on_deck_tiers = 2
    state = apply_move(instance, initial_state(instance),
                       Load(call_index=0, container_id=a, src="B-001-01-01", dst="0010182"))
    assert_illegal(instance, state,
        Load(call_index=0, container_id=b, src="A-003-01-01", dst="0010184"),
        "port.e3.quay_height")


def test_layover_discharge_and_restow_exact_counts():
    instance = build_instance(layover=True)
    *_, discharge, through = ids(instance)
    moves = plan(instance,
        HatchCoverOff(call_index=0, cover_id="HC-02-1"),
        RestowOff(call_index=0, container_id=through, src="0010104"),
        Discharge(call_index=0, container_id=discharge, src="0010102", dst="B-002-01-01"),
        RestowOn(call_index=0, container_id=through, dst="0010102"),
        HatchCoverOn(call_index=0, cover_id="HC-02-1"),
    )
    result = apply_plan(instance, moves)
    assert result.legal, result.violations
    assert (result.count.discharges, result.count.restow_lifts, result.count.hatch_cover_lifts) == (1, 2, 2)
    assert result.count.total_lifts_now == 5
    assert result.count.weighted_nonproductive == 6  # four now plus the later hold-opening pair.
    assert result.final_state.yard.quay_buffer == []
    assert result.final_state.ship.covers_off == {}
    assert [(p.slot, p.container_id) for p in result.final_state.ship.placements] == [("0010102", through)]


def test_projection_three_call_rotation_deduplicates_span_and_hatch_blockers():
    instance = build_instance(layover=True)
    forty, a, b, deck, low, high = ids(instance)
    instance.load_list = []
    instance.discharge_list = []
    instance.yard.stacks = []
    for box in instance.containers:
        box.pod = "THBKK" if box.id in (a, b, low) else "THSGZ"
    instance.onboard = ShipState(vessel_id=instance.vessel.id, placements=[
        dict(slot=slot, container_id=box) for slot, box in [
            ("0010102", a), ("0030102", b), ("0020104", forty),
            ("0010182", deck), ("0010202", low), ("0010204", high),
        ]])
    instance = validate(instance)
    before = instance.onboard.model_dump_json()
    future = project_future_moves(instance, instance.onboard, from_call=0)
    # Call 1: 40' blocks BOTH bottom 20s, counted once; deck box blocks that hatch;
    # second cover's upper 20 blocks its bottom 20. Three restows => six lifts;
    # both covers off/back => four lifts. Call 2: all remaining cargo is due,
    # so zero restows, and the two hold covers still require four lifts.
    assert [(f.call_index, f.restowed_boxes, f.restow_lifts, f.hatch_cover_lifts) for f in future] == [
        (1, 3, 6, 4), (2, 0, 0, 4),
    ]
    assert instance.onboard.model_dump_json() == before
    assert project_future_moves(instance, instance.onboard, from_call=2) == []
    result = apply_plan(instance, plan(instance))
    assert result.legal
    assert result.count.weighted_nonproductive == 14
    instance.objective.future_restow_lifts_per_box = 4
    instance.objective.future_restow = 3
    instance.objective.future_hatch_cover = 5
    weighted = count_moves(instance, plan(instance))
    assert weighted.future[0].restow_lifts == 12
    assert weighted.weighted_nonproductive == 12 * 3 + 8 * 5


def test_diagnostic_replay_skips_invalid_move_without_partial_mutation():
    instance = build_instance()
    _, box, _, _ = ids(instance)
    bad = Relocate(call_index=0, container_id=box, src="B-001-01-01", dst="A-005-01-02")
    good = Relocate(call_index=0, container_id=box, src="B-001-01-01", dst="A-005-01-01")
    moves = plan(instance, bad, good)
    stopped = apply_plan(instance, moves)
    assert not stopped.legal
    assert stopped.final_state == initial_state(instance)
    assert stopped.count.total_lifts_now == 0
    continued = apply_plan(instance, moves, stop_at_first=False)
    assert not continued.legal
    assert [(v.move_index, v.rule_id) for v in continued.violations] == [(0, "expert.moves.bottom_up")]
    assert continued.count.relocations == continued.count.total_lifts_now == 1
    assert continued.executed == 2
    assert continued.final_state == apply_move(instance, initial_state(instance), good)


@pytest.mark.parametrize("seed", range(12))
def test_random_legal_relocation_plans_and_one_illegal_move(seed):
    instance = build_instance()
    _, a, b, c = ids(instance)
    rng = random.Random(seed)
    keys = ["B-001-01", "A-003-01", "A-004-01", "A-005-01", "A-006-01"]
    stacks = {key: [] for key in keys}
    stacks[keys[0]], stacks[keys[1]], stacks[keys[2]] = [a], [b], [c]
    moves = []
    for _ in range(40):
        src = rng.choice([k for k in keys if stacks[k]])
        dst = rng.choice([k for k in keys if k != src and len(stacks[k]) < 3])
        box = stacks[src][-1]
        moves.append(Relocate(call_index=0, container_id=box,
                              src=f"{src}-{len(stacks[src]):02}", dst=f"{dst}-{len(stacks[dst])+1:02}"))
        stacks[src].pop()
        stacks[dst].append(box)
    result = apply_plan(instance, plan(instance, *moves))
    assert result.legal, result.violations
    assert result.executed == result.count.relocations == 40
    assert {s.key: s.boxes for s in result.final_state.yard.stacks if s.length_ft == 20} == {
        k: boxes for k, boxes in stacks.items() if boxes}
    # Corrupt one known legal move by inserting an unsupported target tier.
    bad = moves[0].model_copy(update={"dst": moves[0].dst[:-2] + "03"})
    assert_illegal(instance, initial_state(instance), bad, "expert.moves.bottom_up")
    illegal_plan = plan(instance, bad, *moves[1:])
    assert not apply_plan(instance, illegal_plan).legal
    with pytest.raises(ValueError):
        count_moves(instance, illegal_plan)


def test_thousand_move_replay_records_timing(record_property):
    instance = build_instance()
    _, _, box, _ = ids(instance)
    moves = []
    for index in range(1000):
        src, dst = ("A-003-01-01", "A-005-01-01") if index % 2 == 0 else ("A-005-01-01", "A-003-01-01")
        moves.append(Relocate(call_index=0, container_id=box, src=src, dst=dst))
    replay = plan(instance, *moves)
    started = perf_counter()
    result = apply_plan(instance, replay)
    elapsed = perf_counter() - started
    assert result.legal, result.violations
    assert result.count.relocations == result.executed == 1000
    # Timing is evidence for a controlled benchmark, not a shared-host correctness gate.
    record_property("replay_1000_moves_ms", elapsed * 1000)


def test_simulator_gate_out_then_gate_in_roundtrip_is_pure():
    instance = build_instance()
    _, box, _, _ = ids(instance)
    before = instance.model_dump_json()
    initial = initial_state(instance)
    outside = apply_move(instance, initial,
                         GateOut(call_index=0, container_id=box, src="B-001-01-01"))
    returned = apply_move(instance, outside,
                          GateIn(call_index=0, container_id=box, dst="B-001-01-01"))
    assert returned == initial
    assert initial == initial_state(instance)
    assert all(box not in stack.boxes for stack in outside.yard.stacks)
    assert instance.model_dump_json() == before
    assert_illegal(instance, initial_state(instance),
                   GateIn(call_index=0, container_id=box, dst="B-002-01-01"),
                   "expert.physical.slot_occupancy")


def test_restow_shift_is_one_lift_with_custom_weight():
    instance = build_instance()
    _, box, _, _ = ids(instance)
    instance.objective.restow = 7
    result = apply_plan(instance, plan(instance,
        Load(call_index=0, container_id=box, src="B-001-01-01", dst="0010182"),
        RestowShift(call_index=0, container_id=box, src="0010182", dst="0010282"),
    ))
    assert result.legal, result.violations
    assert (result.count.loads, result.count.restow_lifts, result.count.total_lifts_now) == (1, 1, 2)
    assert result.count.weighted_nonproductive == 7
    assert result.final_state.ship.placements == [Placement(slot="0010282", container_id=box)]
    assert_illegal(instance, result.final_state,
        Discharge(call_index=0, container_id=box, src="0010282", dst="B-001-01-01"),
        "expert.moves.top_access")


def test_cover_staged_on_cover_blocks_crane_access_until_removed():
    instance = build_instance()
    _, box, _, _ = ids(instance)
    off = HatchCoverOff(call_index=0, cover_id="HC-02-1", dst="HC-02-2")
    state = apply_move(instance, initial_state(instance), off)
    assert state.ship.covers_off == {"HC-02-1": "HC-02-2"}
    assert_illegal(instance, state, HatchCoverOff(call_index=0, cover_id="HC-02-2"),
                   "expert.moves.hatch_clear")
    assert_illegal(instance, state,
        Load(call_index=0, container_id=box, src="B-001-01-01", dst="0010282"),
        "expert.moves.hatch_clear")
    assert_illegal(instance, state,
        HatchCoverOff(call_index=0, cover_id="HC-02-2", dst="HC-02-1"),
        "expert.moves.hatch_clear")
    result = apply_plan(instance, plan(instance, off,
        HatchCoverOn(call_index=0, cover_id="HC-02-1"),
        HatchCoverOff(call_index=0, cover_id="HC-02-2", dst="HC-02-1"),
        HatchCoverOn(call_index=0, cover_id="HC-02-2"),
    ))
    assert result.legal, result.violations
    assert result.count.hatch_cover_lifts == result.count.total_lifts_now == 4
    assert result.count.weighted_nonproductive == 4


@pytest.mark.parametrize("unfinished", ["buffer", "cover", "discharge"])
def test_departure_rejects_unfinished_work(unfinished):
    instance = build_instance(layover=unfinished == "discharge")
    if unfinished == "buffer":
        _, box, _, _ = ids(instance)
        moves = [Load(call_index=0, container_id=box, src="B-001-01-01", dst="0010182"),
                 RestowOff(call_index=0, container_id=box, src="0010182")]
        expected = "expert.moves.top_access"
    elif unfinished == "cover":
        moves = [HatchCoverOff(call_index=0, cover_id="HC-02-1")]
        expected = "expert.moves.hatch_clear"
    else:
        moves = []
        expected = "expert.moves.top_access"
    result = apply_plan(instance, plan(instance, *moves))
    assert not result.legal
    assert [(v.rule_id, v.move_index) for v in result.violations] == [(expected, None)]
    with pytest.raises(ValueError):
        count_moves(instance, plan(instance, *moves))
