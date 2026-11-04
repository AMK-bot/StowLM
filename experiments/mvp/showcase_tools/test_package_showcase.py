"""Cheap artifact-contract checks using the existing checked tiny smoke fixture."""
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from package_showcase import ROOT, package_run, read_json, timeline_argv, write_json


@pytest.fixture
def tiny():
    fixture = ROOT / "experiments/mvp/smoke/run-20261029/tiny-feeder_500-0"
    if not fixture.is_dir():
        pytest.skip("existing tiny smoke fixture unavailable")
    return fixture


def test_plan_roundtrip_and_hash_binding(tmp_path, tiny):
    from app.inputs import load_inputs
    folder = tmp_path / "run"
    entry = package_run(tiny / "instance.json", tiny / "plan.json", folder,
                        report_path=tiny / "checker.json")
    assert (entry / "instance.json").read_bytes() == (tiny / "instance.json").read_bytes()
    outcome = read_json(entry / "outcome.json")
    assert outcome["status"] == "passed"
    assert outcome["checker_seconds"] is None  # exact-bound existing report reused
    assert outcome["planning_seconds"] is None  # no invented MovePlan runtime
    selected = (entry / "result.json").relative_to(folder)
    loaded = load_inputs(run_folder=folder, entry=selected)
    assert len(loaded.plan.moves) == outcome["executed_moves"]
    payload = read_json(entry / "result.json")
    payload["input_sha256"] = "0" * 64
    write_json(entry / "result.json", payload)
    with pytest.raises(ValueError, match="checksum"):
        load_inputs(run_folder=folder, entry=selected)


def test_partial_preserves_planner_status(tmp_path, tiny):
    from stowage.model import MovePlan, PlannerResult
    plan = MovePlan.model_validate(read_json(tiny / "plan.json"))
    result = PlannerResult(status="timeout", plan=plan, planner="solver.scalable",
                           planner_version="test", wall_time_s=0.125, provenance=plan.provenance)
    source = tmp_path / "source.json"
    write_json(source, result.model_dump(mode="json"))
    folder = tmp_path / "run"
    entry = package_run(tiny / "instance.json", source, folder,
                        report_path=tiny / "checker.json", status="partial")
    outcome = read_json(entry / "outcome.json")
    assert outcome["status"] == "partial" and not outcome["departure_approved"]
    assert outcome["checker_passed"] is True  # actual report, independent of planner timeout
    assert read_json(entry / "result.json")["status"] == "timeout"
    manifest = read_json(folder / "manifest.json")
    assert manifest["status"] == "partial"
    assert not {"planner_kind", "base_model", "adapter_sha256", "mask_version"} & manifest.keys()
    command = timeline_argv(folder, (entry / "result.json").relative_to(folder))
    assert command[command.index("--workers") + 1] == "1"
    assert Path(command[command.index("--cache-dir") + 1]).is_relative_to(folder)
    # A numerical table must never enter the app's recursive plan discovery.
    from app.inputs import _files
    numerical = Path(command[command.index("--numerical-cache-root") + 1])
    numerical.mkdir(parents=True)
    table = numerical / "large-table.json"
    write_json(table, {"table": "stand-in for measured numerical data"})
    assert table not in _files(folder)
    with pytest.raises(ValueError, match="unshippable"):
        package_run(tiny / "instance.json", source, folder,
                    report_path=tiny / "checker.json", status="passed")


def test_no_plan_failure_is_retained_without_replay(tmp_path, tiny):
    source = tmp_path / "failure.json"
    write_json(source, {"status": "error", "planner": "solver.scalable",
                        "planner_version": "test", "message": "No candidate"})
    entry = package_run(tiny / "instance.json", source, tmp_path / "run")
    outcome = read_json(entry / "outcome.json")
    assert outcome["status"] == "failed" and not outcome["departure_approved"]
    assert outcome["checker_passed"] is None and outcome["timeline_command"] is None
    assert outcome["ui_import"].startswith("unavailable:")


def test_model_folder_rejected_before_writes(tmp_path, tiny):
    folder = tmp_path / "model"
    write_json(folder / "manifest.json", {"planner_kind": "base_glimmer_mask"})
    with pytest.raises(ValueError, match="model-manifest"):
        package_run(tiny / "instance.json", tiny / "plan.json", folder)
    assert not (folder / "instances").exists()
