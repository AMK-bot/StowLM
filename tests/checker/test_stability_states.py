"""Incremental in-port states exactly match the authoritative cold move engine."""
from time import perf_counter

import pytest

from stowage.model.moves import GateIn, GateOut, Relocate
from stowage.moves import IllegalMoveError, apply_move, initial_state
from stowage.rules.catalogue import rule_freeze_v1
from tests.moves.test_mixed_replay import mixed_scenario


@pytest.mark.parametrize('enabled', [False, True])
def test_mixed_states_match_cold_prefixes_and_preserve_every_previous_snapshot(enabled):
    from stowage.checker.stability_states import InPortStateBuilder
    instance, operations = mixed_scenario()
    before = instance.model_dump()
    moves = [move for move, _ in operations.values()]
    with rule_freeze_v1(enabled=enabled):
        builder = InPortStateBuilder(instance)
        snapshots = [(builder.state, builder.state.model_dump())]
        for index, move in enumerate(moves):
            cold = initial_state(instance)
            for offset, prefix_move in enumerate(moves[:index+1]):
                cold = apply_move(instance, cold, prefix_move, move_index=offset)
            actual = builder.push(move, move_index=index)
            assert actual.model_dump() == cold.model_dump()
            assert builder.state.model_dump() == cold.model_dump()
            for previous, payload in snapshots:
                assert previous.model_dump() == payload
            snapshots.append((actual, actual.model_dump()))
    assert instance.model_dump() == before


@pytest.mark.parametrize('enabled', [False, True])
def test_illegal_move_has_identical_diagnostic_is_atomic_and_can_continue(enabled):
    from stowage.checker.stability_states import InPortStateBuilder
    instance, operations = mixed_scenario()
    with rule_freeze_v1(enabled=enabled):
        builder = InPortStateBuilder(instance)
        before = builder.state.model_dump()
        illegal = operations['hold_load'][0]
        with pytest.raises(IllegalMoveError) as cold:
            apply_move(instance, builder.state, illegal, move_index=17)
        with pytest.raises(IllegalMoveError) as actual:
            builder.push(illegal, move_index=17)
        assert actual.value.violation.model_dump() == cold.value.violation.model_dump()
        assert builder.state.model_dump() == before
        move = operations['hold_open'][0]
        expected = apply_move(instance, builder.state, move, move_index=18)
        assert builder.push(move, move_index=18).model_dump() == expected.model_dump()


def test_builder_can_resume_a_previous_world_and_snapshots_have_no_mutable_aliases():
    from stowage.checker.stability_states import InPortStateBuilder
    instance, operations = mixed_scenario()
    initial = initial_state(instance)
    previous = apply_move(instance, initial, operations['hold_open'][0])
    payload = previous.model_dump()
    builder = InPortStateBuilder(instance, previous)
    actual = builder.push(operations['through_off'][0])
    assert actual.model_dump() == apply_move(instance, previous, operations['through_off'][0]).model_dump()
    previous.ship.tank_masses_kg.clear()
    previous.yard.stacks.clear()
    actual.ship.placements.clear()
    actual.ship.covers_off.clear()
    actual.yard.quay_buffer.clear()
    restored = builder.state
    assert restored.ship.tank_masses_kg == payload['ship']['tank_masses_kg']
    assert restored.ship.covers_off == payload['ship']['covers_off']
    assert restored.ship.placements and restored.yard.quay_buffer
    assert restored.yard.stacks


def test_simulator_gate_events_match_apply_move():
    from stowage.checker.stability_states import InPortStateBuilder
    instance, _ = mixed_scenario()
    box = instance.containers[1].id
    moves = [GateOut(call_index=0, container_id=box, src='B-001-01-01'),
             GateIn(call_index=0, container_id=box, dst='B-001-01-01')]
    builder = InPortStateBuilder(instance)
    cold = initial_state(instance)
    for move in moves:
        cold = apply_move(instance, cold, move)
        assert builder.push(move).model_dump() == cold.model_dump()


def test_each_push_uses_one_engine_move_instead_of_replaying_prefix(monkeypatch):
    from stowage.checker.stability_states import InPortStateBuilder
    from stowage.moves._physical import Replay
    instance, _ = mixed_scenario()
    cid = instance.containers[2].id
    moves = [Relocate(call_index=0, container_id=cid,
                     src='A-003-01-01' if index % 2 == 0 else 'A-005-01-01',
                     dst='A-005-01-01' if index % 2 == 0 else 'A-003-01-01')
             for index in range(32)]
    calls = []
    original = Replay.move
    def tracked(self, move, move_index=None):
        calls.append(move_index)
        return original(self, move, move_index=move_index)
    monkeypatch.setattr(Replay, 'move', tracked)
    builder = InPortStateBuilder(instance)
    for index, move in enumerate(moves):
        builder.push(move, move_index=index)
    assert calls == list(range(len(moves)))


def test_incremental_prefix_build_timing(record_property):
    """Record performance; shared-host timing never becomes a correctness gate."""
    from stowage.checker.stability_states import InPortStateBuilder
    instance, _ = mixed_scenario()
    cid = instance.containers[2].id
    moves = [Relocate(call_index=0, container_id=cid,
                     src='A-003-01-01' if index % 2 == 0 else 'A-005-01-01',
                     dst='A-005-01-01' if index % 2 == 0 else 'A-003-01-01')
             for index in range(128)]
    started = perf_counter()
    builder = InPortStateBuilder(instance)
    for index, move in enumerate(moves):
        actual = builder.push(move, move_index=index)
    incremental = perf_counter()-started
    started = perf_counter()
    for stop in range(1, len(moves)+1):
        cold = initial_state(instance)
        for index, move in enumerate(moves[:stop]):
            cold = apply_move(instance, cold, move, move_index=index)
    replayed = perf_counter()-started
    assert actual.model_dump() == cold.model_dump()
    record_property('incremental_128_prefixes_seconds', incremental)
    record_property('cold_128_prefixes_seconds', replayed)
    record_property('cold_over_incremental_speedup', replayed/incremental)
