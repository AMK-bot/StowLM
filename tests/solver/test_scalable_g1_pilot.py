"""Cheap G1 driver safeguards; mocked witnesses do not qualify physical plans."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def pilot():
    path = Path(__file__).resolve().parents[2] / "experiments/scalable-solver/g1_pilot.py"
    spec = importlib.util.spec_from_file_location("scalable_g1_pilot_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class FakeStats:
    pushes: int = 0


@pytest.fixture
def replay_setup(monkeypatch):
    import stowage.actions
    import stowage.feasibility
    import stowage.frozen
    from stowage.actions.space import Place

    action = Place("box-A", "0010102")
    engine = SimpleNamespace(
        legal_index=Mock(return_value=[4]), index_of=Mock(return_value=4),
        decode_index=Mock(return_value=action), push=Mock(), dead_end=Mock(return_value=None),
        is_complete=Mock(return_value=True), to_plan=Mock(return_value=None),
        stats=Mock(return_value=FakeStats(pushes=1)),
    )
    space = SimpleNamespace(from_plan=Mock(return_value=[action]), encode=lambda a: {"op": "P", "box": a.box})
    factory = Mock(return_value=engine)
    monkeypatch.setattr(stowage.actions, "get_action_space", lambda: space)
    monkeypatch.setattr(stowage.feasibility, "make_feasibility_engine", factory)
    monkeypatch.setattr(stowage.frozen, "require_clean", Mock())
    return SimpleNamespace(action=action, engine=engine, space=space, factory=factory)


def test_replay_verifies_public_index_and_decode_before_push(pilot, replay_setup):
    instance, plan = object(), object()
    row = pilot.replay(instance, plan)
    assert row["status"] == "passed" and row["verified_actions"] == 1 and row["complete"]
    replay_setup.engine.legal_index.assert_called_once_with()
    replay_setup.engine.decode_index.assert_called_once_with(4)
    replay_setup.engine.push.assert_called_once_with(replay_setup.action)
    # No feasibility config override: this replay uses production C2 defaults.
    replay_setup.factory.assert_called_once_with(instance, replay_setup.space)


@pytest.mark.parametrize("indices, flat", [([], 4), ([3, 5], 4), ([4], None)])
def test_masked_or_unknown_action_never_decoded_or_pushed(pilot, replay_setup, indices, flat):
    replay_setup.engine.legal_index.return_value = indices
    replay_setup.engine.index_of.return_value = flat
    row = pilot.replay(object(), object())
    assert row["status"] == "failed" and row["reason"] == "action_masked"
    assert row["action_index"] == row["verified_actions"] == 0
    replay_setup.engine.decode_index.assert_not_called()
    replay_setup.engine.push.assert_not_called()


def test_index_decode_mismatch_fails_before_push(pilot, replay_setup):
    from stowage.actions.space import Place
    replay_setup.engine.decode_index.return_value = Place("different-box", "0010102")
    row = pilot.replay(object(), object())
    assert row["reason"] == "index_decode_mismatch"
    replay_setup.engine.push.assert_not_called()


def test_replay_requires_complete_departure(pilot, replay_setup):
    replay_setup.engine.is_complete.return_value = False
    row = pilot.replay(object(), object())
    assert row["status"] == "failed" and row["reason"] == "incomplete_departure"
    assert row["verified_actions"] == 1
    replay_setup.engine.push.assert_called_once()


def test_nonindexed_override_fails_closed(pilot, replay_setup):
    from stowage.actions.space import RestowOff
    replay_setup.space.from_plan.return_value = [RestowOff("box-A")]
    row = pilot.replay(object(), object())
    assert row["reason"] == "action_outside_legal_index_contract"
    replay_setup.engine.legal_index.assert_not_called()
    replay_setup.engine.push.assert_not_called()


@pytest.fixture
def measurement_setup(pilot, monkeypatch):
    import stowage.bounds
    import stowage.checker
    import stowage.frozen
    import stowage.moves
    import stowage.planner
    import stowage.solver.scalable

    monkeypatch.setattr(pilot, "time", SimpleNamespace(monotonic=lambda: 1000.))
    count = SimpleNamespace(weighted_nonproductive=6., model_dump=lambda **k: {"weighted_nonproductive": 6.})
    bound = SimpleNamespace(weighted=2., model_dump=lambda **k: {"weighted": 2.})
    applied = SimpleNamespace(final_state=SimpleNamespace(ship=SimpleNamespace(
        placements=[SimpleNamespace(container_id="box-A")])), count=count, legal=True, violations=[])
    raw_plan, finalized_plan = object(), object()
    raw = SimpleNamespace(status="ok", plan=raw_plan, message="", metrics={})
    finalized = SimpleNamespace(status="ok", plan=finalized_plan, message="", metrics={"ballast_status": "ok"})
    planner = Mock(return_value=raw)
    finalizer = Mock(return_value=finalized)
    checker = Mock(return_value=SimpleNamespace(passed=False, violations=[], shut_out=[], count=count))
    mask = Mock(return_value={"status": "passed", "complete": True})
    monkeypatch.setattr(stowage.bounds, "lower_bound", Mock(return_value=bound))
    monkeypatch.setattr(stowage.frozen, "require_clean", Mock())
    monkeypatch.setattr(stowage.moves, "apply_plan", Mock(return_value=applied))
    monkeypatch.setattr(stowage.planner, "finalize", finalizer)
    monkeypatch.setattr(stowage.checker, "check", checker)
    monkeypatch.setattr(stowage.solver.scalable, "ScalableSolver", lambda: SimpleNamespace(plan=planner))
    monkeypatch.setattr(pilot, "replay", mask)
    limits = []

    def deadline(seconds):
        limits.append(seconds)
        return nullcontext()

    return SimpleNamespace(args=SimpleNamespace(overhead_reserve_s=120., ballast_limit_s=30., solver_seed=0),
        instance=SimpleNamespace(load_list=["box-A"]), helper=SimpleNamespace(search_deadline=deadline),
        planner=planner, raw=raw, raw_plan=raw_plan, finalized_plan=finalized_plan,
        finalizer=finalizer, checker=checker, mask=mask, limits=limits)


@pytest.mark.parametrize("failure", ["checker_rejection", "checker_timeout", "finalizer_error"])
def test_failed_validation_still_independently_replays(pilot, measurement_setup, tmp_path, failure):
    from stowage.solver.scalable_oracle import SearchDeadline
    setup = measurement_setup
    if failure == "checker_timeout":
        setup.checker.side_effect = SearchDeadline("mock checker exhausted its budget")
    elif failure == "finalizer_error":
        setup.finalizer.side_effect = ValueError("mock ballast failed")
    row = pilot.measure(setup.instance, setup.args, 60., setup.helper, tmp_path / "must-not-save.json")
    setup.mask.assert_called_once_with(setup.instance,
        setup.raw_plan if failure == "finalizer_error" else setup.finalized_plan)
    assert row["replay"]["status"] == "passed"
    assert not row["approved"] and not row["checker_passed"]
    assert row["objective"] is None and row["gap_to_lb"] is None and not row["gap_valid"]
    assert row["raw_candidate_cost"] == 6. and row["raw_candidate_gap_to_lb"] == pytest.approx(2/3)
    assert row["plan_returned"] and not (tmp_path / "must-not-save.json").exists()
    assert len(setup.limits) == 3  # search guard, validation, independent replay


def test_outer_guard_grace_does_not_expand_nominal_solver_budget(pilot, measurement_setup, tmp_path):
    setup = measurement_setup
    setup.raw.plan = None
    row = pilot.measure(setup.instance, setup.args, 60., setup.helper, tmp_path / "unused.json")
    assert row["nominal_search_budget_s"] == 60.
    assert row["outer_search_guard_s"] == 65.
    assert setup.limits == [65.]
    assert setup.planner.call_args.kwargs["time_limit_s"] == 60.
    assert not row["plan_returned"]
    setup.mask.assert_not_called()


def test_source_transition_joint_pass_is_quarantined_and_excluded(pilot, tmp_path):
    approved = tmp_path / "tiny-1.approved-plan.json"
    approved.write_text('{"mock_plan": true}')
    row = dict(selection={"rung": "tiny"}, admitted=True, source_changed_during_run=True,
        measurement=dict(approved=True, joint_gate_passed=True, plan_returned=True,
            approved_plan=str(approved), approved_plan_sha256="old", objective=8.,
            gap_valid=True, gap_to_lb=.5, timings_s={"search": 60., "pipeline": 65.}))
    pilot.quarantine_source_transition(row)
    data = row["measurement"]
    quarantined = Path(data["quarantined_plan"])
    assert not approved.exists() and quarantined.name == "tiny-1.source-transition-plan.json"
    assert quarantined.read_text() == '{"mock_plan": true}'
    assert data["quarantined_plan_sha256"] == pilot.sha(quarantined)
    assert data["raw_joint_gate_passed"] and data["joint_gate_passed"] and not data["approved"]
    assert "approved_plan" not in data and "approved_plan_sha256" not in data
    assert data["status"] == "source_transition_joint_pass"
    result = pilot.summary([row], {"tiny": {"eligible_train_val": 1, "selected": 1}})["tiny"]
    assert result["approved"] == 0 and result["raw_joint_gate_passed"] == 1
    assert result["plans_returned"] == 1 and result["source_transition_cases"] == 1
    assert result["approved_plans_per_core_hour"] == 0 and result["mean_approved_objective"] is None
    assert result["mean_search_s"] == 60. and result["mean_checker_qualified_gap_to_lb"] == .5
    assert result["mean_approved_gap_to_lb"] is None and result["core_seconds"] == 65.


def test_summary_defensively_excludes_changed_sources_even_if_flag_is_stale(pilot):
    row = dict(selection={"rung": "small"}, admitted=True, source_changed_during_run=True,
               measurement=dict(approved=True, objective=2., timings_s={"pipeline": 10.}))
    result = pilot.summary([row], {"small": {"eligible_train_val": 1, "selected": 1}})["small"]
    assert result["approved"] == 0 and result["mean_approved_objective"] is None
    assert result["core_seconds"] == 10.


def test_frozen_test_shard_is_filtered_before_spec_or_file_access(pilot, monkeypatch):
    import eval.splits

    def forbidden(*args, **kwargs):
        pytest.fail("test shard reached frozen-spec validation or file access")

    monkeypatch.setattr(eval.splits, "split_of", forbidden)
    monkeypatch.setattr(eval.splits, "spec_key", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    manifest = {"groups": {"random_yard/small": {"profile": "small", "shards": [
        {"split": "test", "instance": "DO-NOT-OPEN.json", "split_spec": "malformed"}]}}}
    selected, availability = pilot.select(manifest, ["tiny", "small"], 2)
    assert selected == []
    assert availability["tiny"]["selected"] == availability["small"]["selected"] == 0
    assert availability["tiny"]["status"] == "no_accepted_train_val_shards"
