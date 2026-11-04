"""Scalable yard policy: departure order, capacity, purity and crane replay."""
import pytest

from stowage.conventions import make_container_id
from stowage.model.fixtures import build_instance
from stowage.model.moves import GateOut
from stowage.model.ship import WorldState
from stowage.model.yard import YardStack, YardState
from stowage.moves.engine import apply_move
from stowage.solver.scalable_yard import ScalableDigOutPolicy, dig_out
from stowage.yard.dig_out import YardCapacityError


def setup_yard(contents, *, tiers=4, bays=4):
    instance = build_instance()
    base = instance.containers[2]
    count = max(value for stack in contents for value in stack) + 1
    boxes = [base.model_copy(update={"id": make_container_id("QZXU", 700 + i),
             "special_stowage": [], "oog": None, "category": None}) for i in range(count)]
    instance.containers = boxes
    instance.load_list = [box.id for box in boxes]
    instance.port.yard.blocks = instance.port.yard.blocks[:1]
    block = instance.port.yard.blocks[0]
    block.n_bays, block.n_rows, block.max_tiers = bays, 1, tiers
    instance.port.yard_max_tiers = tiers
    yard = YardState(yard_id=instance.port.yard.id, stacks=[
        YardStack(key=f"A-{i+1:03d}-01", length_ft=20, boxes=[boxes[j].id for j in stack])
        for i, stack in enumerate(contents) if stack])
    instance.yard = yard
    state = WorldState(call_index=instance.call_index, yard=yard, ship=instance.onboard)
    return instance, state, [box.id for box in boxes]


def test_safe_departure_best_fit_beats_near_urgent_stack():
    instance, state, ids = setup_yard([[0, 1], [2], [3], []])
    ranks = {ids[0]: 0, ids[1]: 2, ids[2]: 1, ids[3]: 3}
    before = state.model_dump_json()
    result = dig_out(instance, state, ids[0], departure_ranks=ranks)
    assert result.moves[0].dst == "A-003-01-02"
    assert result.source == "A-001-01-01"
    assert state.model_dump_json() == before
    assert result == dig_out(instance, state, ids[0], departure_ranks=ranks)
    replay = state
    for move in result.moves:
        replay = apply_move(instance, replay, move)
    assert replay == result.state
    apply_move(instance, replay, GateOut(call_index=state.call_index, container_id=ids[0], src=result.source))


def test_all_unsafe_destinations_cover_latest_departure():
    instance, state, ids = setup_yard([[0, 1], [2], [3]], bays=3)
    result = ScalableDigOutPolicy({ids[1]: 5, ids[2]: 1, ids[3]: 3}).retrieve(instance, state, ids[0])
    assert result.moves[0].dst == "A-003-01-02"


def test_minimum_rank_of_whole_destination_stack_matters():
    instance, state, ids = setup_yard([[0, 1], [2, 3], [4]], bays=3)
    ranks = {ids[1]: 3, ids[2]: 1, ids[3]: 10, ids[4]: 4}
    result = dig_out(instance, state, ids[0], departure_ranks=ranks)
    assert result.moves[0].dst == "A-003-01-02"


def test_multiple_blockers_replay_and_accessible_is_noop():
    instance, state, ids = setup_yard([[0, 1, 2]])
    result = dig_out(instance, state, ids[0])
    assert len(result.moves) == 2
    replay = state
    for move in result.moves:
        replay = apply_move(instance, replay, move)
    assert replay == result.state
    assert not dig_out(instance, replay, ids[0]).moves


def test_capacity_failure_keeps_input_pure():
    instance, state, ids = setup_yard([[0, 1]], bays=1)
    before = state.model_dump_json()
    with pytest.raises(YardCapacityError, match="expert.moves.top_access"):
        dig_out(instance, state, ids[0])
    assert state.model_dump_json() == before
    with pytest.raises(YardCapacityError, match="absent"):
        dig_out(instance, state, make_container_id("QZXU", 999))


def test_reject_nan_priorities():
    with pytest.raises(ValueError, match="NaN"):
        ScalableDigOutPolicy({"unused": float("nan")})


def test_cross_block_transport_is_not_counted_as_one_crane_lift():
    instance, state, ids = setup_yard([[0, 1]], bays=1)
    # B has storage, but getting there requires transport outside the one-lift
    # yard relocation model shared with boxslot's expander.
    instance.port.yard.blocks.append(build_instance().port.yard.blocks[1])
    with pytest.raises(YardCapacityError):
        dig_out(instance, state, ids[0])


def test_missing_pickup_forecast_can_create_future_rehandle():
    instance, state, ids = setup_yard([[0, 1], [2], [3]], bays=3)
    instance.load_list = ids[:2]
    # Actual pickup order is target, urgent inventory, blocker, late inventory.
    # The fallback instead treats both non-load inventory boxes as equally late.
    ranks = {ids[0]: 0, ids[2]: 1, ids[1]: 2, ids[3]: 3}
    fallback = dig_out(instance, state, ids[0])
    forecast = dig_out(instance, state, ids[0], departure_ranks=ranks)
    assert fallback.moves[0].dst == "A-002-01-02"
    assert forecast.moves[0].dst == "A-003-01-02"
    target_exit = GateOut(call_index=state.call_index, container_id=ids[0], src=fallback.source)
    fallback_after_pickup = apply_move(instance, fallback.state, target_exit)
    forecast_after_pickup = apply_move(instance, forecast.state, target_exit)
    future_fallback = dig_out(instance, fallback_after_pickup, ids[2], departure_ranks=ranks)
    future_forecast = dig_out(instance, forecast_after_pickup, ids[2], departure_ranks=ranks)
    assert len(future_fallback.moves) == 1
    assert len(future_forecast.moves) == 0
    # Both choices remain physically legal. Forecast quality changes the lift
    # count rather than relaxing any move-engine requirement.
    replay = state
    for move in (*fallback.moves, target_exit, *future_fallback.moves):
        replay = apply_move(instance, replay, move)
    assert replay == future_fallback.state


def test_partial_forecasts_override_fallback_for_named_boxes_only():
    instance, state, ids = setup_yard([[0, 1], [2], [3]], bays=3)
    instance.load_list = ids[:2]
    # The blocker is a current load, but its known pickup rank supersedes zero.
    # Unspecified non-load inventory keeps the late-inventory fallback.
    result = dig_out(instance, state, ids[0], departure_ranks={ids[1]: 2, ids[2]: 1})
    assert result.moves[0].dst == "A-003-01-02"
    # An explicitly supplied zero is a real rank, not a missing value.
    result = dig_out(instance, state, ids[0], departure_ranks={ids[1]: 2, ids[3]: 0})
    assert result.moves[0].dst == "A-002-01-02"
