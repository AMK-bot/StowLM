"""Contract checks for adapting bottom-to-top published BRP data."""

import pytest

from stowage.bench import adapter
from stowage.bench.adapter import benchmark_bounds, replay_relocations, to_instance
from stowage.frozen import StubTaintError, require_clean, stub_override
from stowage.moves import IllegalMoveError, apply_move, apply_plan, count_moves


def test_adapter_keeps_orientation_height_and_distinct_bound_semantics():
    stacks = ((1, 3), (2,), ())
    instance = to_instance(stacks, 3)
    assert instance.port.yard.blocks[0].max_tiers == 3
    assert [len(stack.boxes) for stack in instance.yard.stacks] == [2, 1]
    bound = benchmark_bounds(stacks, 3)
    assert bound.engine_yard == 0
    assert bound.ordered_lb1 == 1


def test_replay_counts_relocations_separately_from_retrieval_lifts():
    result = replay_relocations(((1, 3), (2,), ()), 2, [(0, 2)],
                                expected_relocations=1, restricted=True)
    assert result.count.relocations == 1
    assert result.count.gate_moves == 3
    assert result.count.weighted_nonproductive == 1
    assert result.count.total_lifts_now == 1
    assert not result.final_state.yard.stacks
    assert [move.kind for move in result.plan.moves] == ["relocate", "gate_out", "gate_out", "gate_out"]


def test_simulator_trace_remains_illegal_as_operational_loading_plan():
    stacks = ((1, 3), (2,), ())
    result = replay_relocations(stacks, 2, [(0, 2)])
    instance = to_instance(stacks, 2)
    operational = apply_plan(instance, result.plan)
    assert not operational.legal
    assert operational.violations[0].move_index == 1
    assert "simulator-only" in operational.violations[0].message
    with pytest.raises(ValueError, match="simulator-only"):
        count_moves(instance, result.plan)


def test_replay_objective_weights_relocations_without_charging_retrievals(monkeypatch):
    instance = to_instance(((1, 3), (2,), ()), 2)
    instance.objective.relocate = 2.5
    monkeypatch.setattr(adapter, "to_instance", lambda *args, **kwargs: instance)
    result = replay_relocations(((1, 3), (2,), ()), 2, [(0, 2)], expected_relocations=1)
    assert result.count.relocations == 1
    assert result.count.gate_moves == 3
    assert result.count.weighted_nonproductive == 2.5
    assert [future.call_index for future in result.count.future] == [1, 2]
    assert all(future.restow_lifts == future.hatch_cover_lifts == 0 for future in result.count.future)


def test_replay_preserves_active_move_engine_stub_provenance():
    with stub_override("moves.apply_move", apply_move.__wrapped__, reason="review replay"):
        result = replay_relocations(((1, 3), (2,), ()), 2, [(0, 2)])
    assert result.plan.provenance.stubs == {"moves.apply_move": "review replay"}
    assert result.plan.provenance.created_by == "bench.replay"
    assert result.plan.provenance.versions["move_engine"]
    assert "brp" in result.plan.provenance.parents
    with pytest.raises(StubTaintError):
        require_clean(result.plan, what="benchmark evidence")


def test_replay_inherits_input_taint_versions_and_lineage(monkeypatch):
    instance = to_instance(((1, 3), (2,), ()), 2)
    instance.provenance.stubs = {"yard.simulate": "input fixture"}
    instance.provenance.versions["move_engine"] = "older-input"
    instance.provenance.parents = ["public-source"]
    monkeypatch.setattr(adapter, "to_instance", lambda *args, **kwargs: instance)
    result = replay_relocations(((1, 3), (2,), ()), 2, [(0, 2)])
    assert result.plan.provenance.stubs == {"yard.simulate": "input fixture"}
    assert result.plan.provenance.input_versions["move_engine"] == ["older-input"]
    assert set(result.plan.provenance.parents) >= {"public-source", instance.id}
    with pytest.raises(StubTaintError):
        require_clean(result.plan, what="benchmark evidence")


def test_clean_replay_has_current_versions_and_clean_provenance():
    result = replay_relocations(((1, 3), (2,), ()), 2, [(0, 2)])
    assert result.plan.provenance.versions["move_engine"]
    require_clean(result.plan, what="benchmark evidence")


def test_replay_enforces_height_through_engine():
    with pytest.raises(IllegalMoveError):
        replay_relocations(((1, 4), (2, 3)), 2, [(0, 1)])


def test_restricted_replay_rejects_preparatory_relocation():
    with pytest.raises(ValueError, match="restricted"):
        replay_relocations(((1, 4), (2, 3), ()), 3, [(1, 2)], restricted=True)


def test_unrestricted_replay_allows_preparation():
    result = replay_relocations(((1, 4), (2, 3), ()), 3,
                                [(1, 2), (0, 2), (2, 0)], expected_relocations=3)
    assert result.count.relocations == 3
    assert result.count.gate_moves == 4


def test_incomplete_and_count_mismatch_are_rejected():
    with pytest.raises(ValueError, match="unretrieved"):
        replay_relocations(((1, 3), (2,), ()), 2, [])
    with pytest.raises(ValueError, match="expected"):
        replay_relocations(((1, 3), (2,), ()), 2, [(0, 2)], expected_relocations=2)


@pytest.mark.parametrize("stacks,height", [(((1, 1), ()), 2), (((1, 2), ()), 1),
                                          (((0,), ()), 2), (((),), 0)])
def test_adapter_rejects_invalid_inert_data(stacks, height):
    with pytest.raises(ValueError):
        to_instance(stacks, height)
