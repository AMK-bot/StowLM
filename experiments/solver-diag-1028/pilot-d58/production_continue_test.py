"""Cheap continuation-adapter tests; no solver or repair search executes."""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from stowage.model import PlannerResult, Provenance


def module():
    spec = spec_from_file_location("d58_continuation", Path(__file__).with_name("production_continue.py"))
    value = module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_exact_serialized_cargo_is_restored_without_shipping_evidence(tmp_path):
    continuation = module()
    raw = PlannerResult(status="timeout", planner="solver.exact", planner_version="test-version",
                        wall_time_s=120, metrics={"expanded_nodes": 9}, seed=7,
                        provenance=Provenance(stubs={"test.original": "must retain"}))
    (tmp_path / "cargo.result.json").write_text(raw.model_dump_json())
    (tmp_path / "result.json").write_text(json.dumps({"solver": "exact", "has_plan": False,
                         "planner_name": raw.planner, "planner_version": raw.planner_version}))
    restored, _, mode = continuation.restore_raw(tmp_path)
    assert restored.model_dump() == raw.model_dump()
    assert mode == "identical_serialized_cargo_result"
    adapter = continuation.RecordedPlanner(restored, "exact")
    assert adapter.sets_ballast and not adapter.deterministic
    assert adapter.name == raw.planner and adapter.version == raw.planner_version
    replayed = adapter.plan(SimpleNamespace(id="fixture"), seed=7, time_limit_s=30)
    assert replayed is not restored and replayed.model_dump() == restored.model_dump()
    assert not replayed.shippable and replayed.wall_time_s == 120


def test_no_plan_reconstruction_retains_conservative_lineage(tmp_path):
    continuation = module()
    final = PlannerResult(status="timeout", planner="solver.scalable", planner_version="3.0.0",
                          wall_time_s=120, metrics={"pipeline_timing": {"pipeline_s": 121}},
                          provenance=Provenance(stubs={"test.original": "must retain"},
                                                parents=["raw", "checked"]))
    (tmp_path / "final.result.json").write_text(final.model_dump_json())
    (tmp_path / "result.json").write_text(json.dumps({"solver": "scalable", "has_plan": False,
                         "solver_status": "timeout", "solver_metrics": {"nodes": 3},
                         "solve_s": 120, "solver_message": "original timeout"}))
    raw, _, mode = continuation.restore_raw(tmp_path)
    assert raw.plan is None and raw.metrics == {"nodes": 3}
    assert raw.message == "original timeout" and raw.wall_time_s == 120
    assert raw.provenance == final.provenance and "conservative" in mode
    adapter = continuation.RecordedPlanner(raw, "scalable")
    assert not adapter.sets_ballast and not adapter.plan(SimpleNamespace(id="fixture")).shippable
    (tmp_path / "final.result.json").unlink()
    with pytest.raises(ValueError, match="recoverable"):
        continuation.restore_raw(tmp_path)
