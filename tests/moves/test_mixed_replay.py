"""Mixed crane operations checked against hand-authored state transitions.

The oracle only updates literal locations; it does not ask replay to derive legality,
support, cover access, or the expected state. Independent chains may interleave.
"""
from __future__ import annotations

import random

import pytest

from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import (
    Discharge, HatchCoverOff, HatchCoverOn, Load, Relocate, RestowOff, RestowOn,
    RestowShift,
)
from stowage.moves import IllegalMoveError, apply_move, apply_plan, count_moves, initial_state


def mixed_scenario():
    instance = build_instance(layover=True)
    forty, a, b, c, discharge, through = [box.id for box in instance.containers]
    # Each effect is a literal location update chosen when hand-counting this plan.
    operations = {
        "hold_open": (HatchCoverOff(call_index=0, cover_id="HC-02-1"),
                      [("cover_put", "HC-02-1", "quay")]),
        "through_off": (RestowOff(call_index=0, container_id=through, src="0010104"),
                        [("ship_take", through), ("buffer_put", through)]),
        "discharge": (Discharge(call_index=0, container_id=discharge, src="0010102", dst="B-002-01-01"),
                      [("ship_take", discharge), ("yard_put", "B-002-01", discharge)]),
        "through_on": (RestowOn(call_index=0, container_id=through, dst="0010102"),
                       [("buffer_take", through), ("ship_put", through, "0010102")]),
        "hold_load": (Load(call_index=0, container_id=b, src="A-003-01-01", dst="0030102"),
                      [("yard_take", "A-003-01", b), ("ship_put", b, "0030102")]),
        "hold_close": (HatchCoverOn(call_index=0, cover_id="HC-02-1"),
                       [("cover_take", "HC-02-1")]),
        "span_load": (Load(call_index=0, container_id=forty, src="A-001-01-01", dst="0020182"),
                      [("yard_take", "A-001-01", forty), ("ship_put", forty, "0020182")]),
        "deck_load": (Load(call_index=0, container_id=a, src="B-001-01-01", dst="0010282"),
                      [("yard_take", "B-001-01", a), ("ship_put", a, "0010282")]),
        "deck_shift": (RestowShift(call_index=0, container_id=a, src="0010282", dst="0030282"),
                       [("ship_take", a), ("ship_put", a, "0030282")]),
        "deck_off": (RestowOff(call_index=0, container_id=a, src="0030282"),
                     [("ship_take", a), ("buffer_put", a)]),
        "deck_on": (RestowOn(call_index=0, container_id=a, dst="0010282"),
                    [("buffer_take", a), ("ship_put", a, "0010282")]),
        "relocate": (Relocate(call_index=0, container_id=c, src="A-004-01-01", dst="A-005-01-01"),
                     [("yard_take", "A-004-01", c), ("yard_put", "A-005-01", c)]),
        "deck_load_c": (Load(call_index=0, container_id=c, src="A-005-01-01", dst="0030282"),
                        [("yard_take", "A-005-01", c), ("ship_put", c, "0030282")]),
    }
    return instance, operations


def make_plan(instance, moves):
    return MovePlan(id="mixed.replay", instance_id=instance.id, call_index=0, moves=moves)


def assert_literal_state(state, ship, yard, buffer, covers, instance):
    assert {p.container_id: p.slot for p in state.ship.placements} == ship
    assert {s.key: s.boxes for s in state.yard.stacks} == yard
    assert set(state.yard.quay_buffer) == buffer
    assert state.ship.covers_off == covers
    assert state.ship.tank_masses_kg == instance.onboard.tank_masses_kg
    assert state.yard.time_s == instance.yard.time_s
    assert state.call_index == instance.call_index
    located = list(ship) + [cid for boxes in yard.values() for cid in boxes] + list(buffer)
    assert len(located) == len(set(located)) == len(instance.containers)


@pytest.mark.parametrize("seed", range(12))
def test_random_mixed_actions_match_independent_state_after_every_lift(seed):
    instance, operations = mixed_scenario()
    before = instance.model_dump_json()
    rng = random.Random(seed)
    chains = [list(operations)[:7], list(operations)[7:11], ["relocate"]]
    order = []
    while any(chains):
        chain = rng.choice([chain for chain in chains if chain])
        order.append(chain.pop(0))
    order.append("deck_load_c")
    ship = {p.container_id: p.slot for p in instance.onboard.placements}
    yard = {s.key: list(s.boxes) for s in instance.yard.stacks}
    buffer, covers = set(), {}
    state, moves = initial_state(instance), []
    for label in order:
        move, effects = operations[label]
        moves.append(move)
        for effect in effects:
            kind, key, *value = effect
            if kind == "ship_take":
                del ship[key]
            elif kind == "ship_put":
                ship[key] = value[0]
            elif kind == "yard_take":
                assert yard[key].pop() == value[0]
                if not yard[key]:
                    del yard[key]
            elif kind == "yard_put":
                yard.setdefault(key, []).append(value[0])
            elif kind == "buffer_put":
                buffer.add(key)
            elif kind == "buffer_take":
                buffer.remove(key)
            elif kind == "cover_put":
                covers[key] = value[0]
            elif kind == "cover_take":
                del covers[key]
            else:
                raise AssertionError(f"Unknown oracle effect: {kind}")
        previous_state = state
        previous = previous_state.model_dump_json()
        state = apply_move(instance, state, move)
        assert_literal_state(state, ship, yard, buffer, covers, instance)
        # A previous snapshot remains independent of the following lift.
        assert previous_state.model_dump_json() == previous
        assert previous != state.model_dump_json()
    result = apply_plan(instance, make_plan(instance, moves))
    assert result.legal, result.violations
    assert_literal_state(result.final_state, ship, yard, buffer, covers, instance)
    assert result.final_state == state
    assert (result.count.loads, result.count.discharges, result.count.relocations,
            result.count.restow_lifts, result.count.hatch_cover_lifts) == (4, 1, 1, 5, 2)
    assert result.count.total_lifts_now == result.executed == 13
    assert count_moves(instance, make_plan(instance, moves)) == result.count
    assert instance.model_dump_json() == before


@pytest.mark.parametrize("case", [
    "load_closed_hold", "restow_blocked_box", "shift_without_support", "return_to_occupied_slot",
    "discharge_wrong_pod", "discharge_twice", "return_twice", "cover_off_twice",
    "load_deck_without_cover", "open_cover_under_cargo", "relocate_with_gap",
    "shift_with_gap", "remove_buffer_box_again", "close_cover_twice",
])
def test_illegal_insertions_in_mixed_plan_are_atomic_and_diagnostic_replay_recovers(case):
    instance, operations = mixed_scenario()
    _, a, _, c, discharge, through = [box.id for box in instance.containers]
    moves = [move for move, _ in operations.values()]
    top, hatch, bottom, occupied = ("expert.moves.top_access", "expert.moves.hatch_clear",
                                   "expert.moves.bottom_up", "expert.physical.slot_occupancy")
    cases = {
        "load_closed_hold": (0, operations["hold_load"][0], hatch),
        "restow_blocked_box": (1, RestowOff(call_index=0, container_id=discharge, src="0010102"), top),
        "shift_without_support": (1, RestowShift(call_index=0, container_id=through,
                                                src="0010104", dst="0030104"), bottom),
        "return_to_occupied_slot": (2, RestowOn(call_index=0, container_id=through, dst="0010102"), occupied),
        "discharge_wrong_pod": (2, Discharge(call_index=0, container_id=through,
                                             src="0010104", dst="B-003-01-01"), top),
        "discharge_twice": (3, operations["discharge"][0], top),
        "return_twice": (4, operations["through_on"][0], top),
        "cover_off_twice": (5, operations["hold_open"][0], hatch),
        "load_deck_without_cover": (5, operations["span_load"][0], hatch),
        "open_cover_under_cargo": (7, operations["hold_open"][0], hatch),
        "relocate_with_gap": (8, Relocate(call_index=0, container_id=c,
                                          src="A-004-01-01", dst="A-005-01-02"), bottom),
        "shift_with_gap": (9, RestowShift(call_index=0, container_id=a,
                                         src="0030282", dst="0010284"), bottom),
        "remove_buffer_box_again": (10, operations["deck_off"][0], top),
        "close_cover_twice": (11, operations["hold_close"][0], hatch),
    }
    index, bad, expected_rule = cases[case]
    state = initial_state(instance)
    for move in moves[:index]:
        state = apply_move(instance, state, move)
    before = state.model_dump_json()
    with pytest.raises(IllegalMoveError) as error:
        apply_move(instance, state, bad, move_index=index)
    assert error.value.violation.rule_id == expected_rule
    assert error.value.violation.move_index == index
    assert state.model_dump_json() == before
    invalid = make_plan(instance, moves[:index] + [bad] + moves[index:])
    stopped = apply_plan(instance, invalid)
    assert not stopped.legal
    assert stopped.final_state == state
    assert stopped.count.total_lifts_now == index
    assert [(v.move_index, v.rule_id) for v in stopped.violations if v.move_index is not None] == [
        (index, expected_rule),
    ]
    # An interrupted legal prefix can also have unfinished departure work.
    assert all(v.rule_id in (top, hatch) for v in stopped.violations if v.move_index is None)
    continued = apply_plan(instance, invalid, stop_at_first=False)
    assert not continued.legal
    assert [(v.move_index, v.rule_id) for v in continued.violations] == [(index, expected_rule)]
    valid = apply_plan(instance, make_plan(instance, moves))
    assert valid.legal, valid.violations
    assert continued.final_state == valid.final_state
    assert continued.count == valid.count
    with pytest.raises(ValueError):
        count_moves(instance, invalid)
