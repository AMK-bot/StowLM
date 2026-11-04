"""Cheap campaign safeguards; these doubles never certify physical gold plans."""
from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def legacy_rules():
    """These assertions specify the frozen legacy class/E6/clearance contract."""
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=False):
        yield


@pytest.fixture
def campaign():
    path = Path(__file__).resolve().parents[2] / "experiments/scalable-solver/fresh_campaign.py"
    spec = importlib.util.spec_from_file_location("scalable_campaign_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def measurement(**updates):
    row = dict(status="dead_end", completed=False, approved=False, checker_passed=False,
        diagnostic_c2_off=False, timing_s={"total": 100., "search": 90.},
        objective_d11=None, gap=None, gap_valid=False)
    row.update(updates)
    return row


def test_selection_reproducible_train_val_only_before_generation(campaign):
    from eval.splits import split_of, spec_key
    rows = campaign.selections(list(campaign.RUNGS), 30, 71000000, ["train", "val"])
    assert rows == campaign.selections(list(campaign.RUNGS), 30, 71000000, ["train", "val"])
    assert len(rows) == 150
    assert len({row["spec_key"] for row in rows}) == 150
    for row in rows:
        assert row["split"] in ("train", "val")
        assert split_of(row["spec"]) == row["split"]
        assert spec_key(row["spec"]) == row["spec_key"]
        if row["rung"] == "dense":
            assert row["spec"]["rung"] == row["spec"]["slice"] == "full"
            assert row["overrides"] == {"n_boxes": 400, "dense_20ft": True}


@pytest.mark.parametrize("allowed", [["test"], ["train", "test"], []])
def test_selection_rejects_forbidden_or_empty_split_set(campaign, allowed):
    with pytest.raises(ValueError):
        campaign.selections(["tiny"], 1, 0, allowed)


def test_cli_rejects_test_split_without_generation(campaign, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("test split must fail before selections or generation")
    monkeypatch.setattr(campaign, "selections", forbidden)
    with pytest.raises(SystemExit) as exc:
        campaign.main(["--output", str(tmp_path), "--split", "test"])
    assert exc.value.code == 2


def test_generation_rejects_test_before_resuming_existing_file(campaign, monkeypatch, tmp_path):
    import stowage.io
    entry = campaign.selections(["tiny"], 1, 71000000, ["train", "val"])[0]
    entry["split"] = "test"
    path = tmp_path / "tiny" / f"seed-{entry['spec']['seed']:08d}.json"
    path.parent.mkdir()
    path.write_text("{}")
    path.with_suffix(".audit.json").write_text(json.dumps({"identity": entry, "sha256": campaign.sha(path)}))
    def forbidden(*args, **kwargs):
        pytest.fail("forbidden test instance must never be opened")
    monkeypatch.setattr(stowage.io, "load_instance", forbidden)
    with pytest.raises(ValueError, match="test|split"):
        campaign.generate(entry, tmp_path)


def test_generation_resume_rejects_changed_physics_before_loading(campaign, monkeypatch, tmp_path):
    import stowage.io
    entry = campaign.selections(["tiny"], 1, 71000000, ["train", "val"])[0]
    path = tmp_path / "tiny" / f"seed-{entry['spec']['seed']:08d}.json"
    path.parent.mkdir()
    path.write_text("{}")
    identity = {**entry, "generation_source_sha256": "old-physics"}
    path.with_suffix(".audit.json").write_text(json.dumps({"identity": identity, "sha256": campaign.sha(path)}))
    monkeypatch.setattr(campaign, "generation_hash", lambda: "new-physics")
    def forbidden(*args, **kwargs):
        pytest.fail("stale generated physics must not be loaded")
    monkeypatch.setattr(stowage.io, "load_instance", forbidden)
    with pytest.raises(ValueError, match="resume mismatch"):
        campaign.generate(entry, tmp_path)


def test_generation_rejects_forged_train_label_for_heldout_spec(campaign, monkeypatch, tmp_path):
    from eval.splits import spec_key, split_of
    import stowage.generate.random_yard
    import stowage.io
    entry = campaign.selections(["tiny"], 1, 71000000, ["train", "val"])[0]
    entry["spec"]["port"] = "us_west_coast"
    entry["spec_key"] = spec_key(entry["spec"])
    entry["split"] = "train"
    assert split_of(entry["spec"]) == "test"
    def forbidden(*args, **kwargs):
        pytest.fail("held-out specification must never be generated or loaded")
    monkeypatch.setattr(stowage.io, "load_instance", forbidden)
    monkeypatch.setattr(stowage.generate.random_yard, "generate_random_instance", forbidden)
    with pytest.raises(ValueError, match="split|specification"):
        campaign.generate(entry, tmp_path)
    assert list(tmp_path.iterdir()) == []


def frozen_checkpoint(campaign, directory):
    entry = campaign.selections(["tiny"], 1, 71000000, ["train", "val"])[0]
    path = directory / "tiny" / f"seed-{entry['spec']['seed']:08d}.json"
    path.parent.mkdir()
    path.write_text("immutable-generated-input")
    audit = {"identity": {**entry, "generation_source_sha256": "original-physics"},
             "sha256": campaign.sha(path)}
    path.with_suffix(".audit.json").write_text(json.dumps(audit))
    return entry, path, audit


def test_frozen_read_preserves_original_fingerprint_after_source_changes(campaign, monkeypatch, tmp_path):
    import eval.splits
    import stowage.io
    entry, path, audit = frozen_checkpoint(campaign, tmp_path)
    instance = object()
    monkeypatch.setattr(campaign, "generation_hash", lambda: "changed-current-physics")
    monkeypatch.setattr(stowage.io, "load_instance", lambda candidate: instance if candidate == path else None)
    monkeypatch.setattr(eval.splits, "spec_from_instance", lambda candidate: entry["spec"])
    loaded, actual_audit, actual_path = campaign.load_generated(entry, tmp_path)
    assert loaded is instance and actual_path == path
    assert actual_audit == audit
    assert actual_audit["identity"]["generation_source_sha256"] == "original-physics"


def test_frozen_read_rejects_tampered_bytes_before_deserialization(campaign, monkeypatch, tmp_path):
    import stowage.io
    entry, path, audit = frozen_checkpoint(campaign, tmp_path)
    path.write_text("tampered-input")
    def forbidden(*args, **kwargs):
        pytest.fail("tampered instance must not reach deserialization")
    monkeypatch.setattr(stowage.io, "load_instance", forbidden)
    with pytest.raises(ValueError, match="identity/content mismatch"):
        campaign.load_generated(entry, tmp_path)


def test_frozen_read_rejects_forged_train_label_without_file_reads(campaign, monkeypatch, tmp_path):
    from eval.splits import spec_key
    entry = campaign.selections(["tiny"], 1, 71000000, ["train", "val"])[0]
    entry["spec"]["port"] = "us_west_coast"
    entry["spec_key"] = spec_key(entry["spec"])
    entry["split"] = "train"
    def forbidden(*args, **kwargs):
        pytest.fail("held-out spec must be rejected before any file reads")
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    with pytest.raises(ValueError, match="invalid/test frozen input"):
        campaign.load_generated(entry, tmp_path)


def test_frozen_read_rejects_embedded_identity_mismatch(campaign, monkeypatch, tmp_path):
    import eval.splits
    import stowage.io
    entry, path, audit = frozen_checkpoint(campaign, tmp_path)
    wrong_spec = {**entry["spec"], "seed": entry["spec"]["seed"] + 1}
    monkeypatch.setattr(stowage.io, "load_instance", lambda candidate: object())
    monkeypatch.setattr(eval.splits, "spec_from_instance", lambda instance: wrong_spec)
    with pytest.raises(ValueError, match="disagrees with selected specification"):
        campaign.load_generated(entry, tmp_path)


def test_cli_list_only_shards_are_disjoint_and_cover_full_selection(campaign, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(campaign, "SCOPE", tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("list-only selection must not generate or load inputs")
    monkeypatch.setattr(campaign, "generate", forbidden)
    monkeypatch.setattr(campaign, "load_generated", forbidden)
    monkeypatch.setattr(campaign, "helpers", forbidden)
    base = ["--output", str(tmp_path/"unwritten"), "--rungs", "tiny", "small",
            "--count", "7", "--list-only"]
    assert campaign.main(base) == 0
    all_rows = json.loads(capsys.readouterr().out)
    shards = []
    for index in range(3):
        assert campaign.main(base+["--shard-count", "3", "--shard-index", str(index)]) == 0
        shards.append(json.loads(capsys.readouterr().out))
    keys = [{row["spec_key"] for row in shard} for shard in shards]
    assert set.union(*keys) == {row["spec_key"] for row in all_rows}
    assert sum(map(len, keys)) == len(all_rows) == 14
    assert not any(keys[left] & keys[right] for left in range(3) for right in range(left))
    assert not (tmp_path/"unwritten").exists()


@pytest.mark.parametrize("count,index", [(0, 0), (2, -1), (2, 2)])
def test_cli_rejects_invalid_shard_bounds(campaign, tmp_path, count, index):
    with pytest.raises(SystemExit) as exc:
        campaign.main(["--output", str(tmp_path), "--list-only", "--shard-count", str(count),
                       "--shard-index", str(index)])
    assert exc.value.code == 2


@pytest.fixture
def oog_instance():
    from stowage.actions.samples import tiny_instance
    from stowage.model.cargo import OutOfGauge
    instance = tiny_instance(834, n_load=1, n_other=0, p40=0)
    instance.containers[0].oog = OutOfGauge(over_width_port_m=.3, over_length_fore_m=.2)
    # Catalogue defaults are present under D32; an invalid explicit override
    # tests the same fail-closed preflight without depending on their absence.
    instance.rule_overrides["expert.e9.over_width_length"] = {"oog_clearance": None}
    return instance


def test_preflight_reports_global_oog_clearance_for_all_affected_slots(legacy_rules, campaign, oog_instance):
    from stowage.solver.scalable import _slots
    before = oog_instance.model_dump(mode="json")
    issues = campaign.shared_data_preflight(oog_instance)
    assert len(issues) == 1
    assert issues[0]["container"] == oog_instance.load_list[0]
    assert issues[0]["slots_checked"] == 1
    assert issues[0]["slots_affected"] == len(_slots(oog_instance)[20]) > 0
    assert issues[0]["scope"] == "instance-wide rule parameter"
    assert all(v["rule_id"] == "checker.missing_vessel_data" and
        "oog_clearance must be finite and nonnegative" in v["message"]
        for v in issues[0]["violations"])
    assert oog_instance.model_dump(mode="json") == before


def test_preflight_accepts_explicit_clearance_data_without_certifying_plan(campaign, oog_instance):
    oog_instance.rule_overrides["expert.e9.over_width_length"] = {"minimum_clearance_m": .1}
    assert campaign.shared_data_preflight(oog_instance) == []


def test_preflight_does_not_require_horizontal_clearance_for_height_only_oog(campaign, oog_instance):
    from stowage.model.cargo import OutOfGauge
    oog_instance.containers[0].oog = OutOfGauge(over_height_m=.3)
    assert campaign.shared_data_preflight(oog_instance) == []


def test_missing_oog_data_stops_before_planner_and_counts_failed_pipeline_attempt(
        legacy_rules, campaign, oog_instance, monkeypatch, tmp_path):
    import stowage.bounds
    def forbidden(*args, **kwargs):
        pytest.fail("missing shared rule data must stop before bounds or planner")
    monkeypatch.setattr(stowage.bounds, "lower_bound", forbidden)
    args = SimpleNamespace(diagnostic_c2_off=False, preflight_data=True)
    row = campaign.measure(oog_instance, SimpleNamespace(plan=forbidden), args,
        tmp_path/"never-approved.json", None)
    assert row["status"] == "unsupported_data"
    assert not row["completed"] and not row["approved"] and not row["checker_passed"]
    assert row["required_data_violations"]
    assert "not an infeasibility proof" in row["message"]
    assert row["objective_d11"] is None and row["gap"] is None and not row["gap_valid"]
    assert row["timing_s"]["total"] > 0
    summary = campaign.summarize([dict(identity={"rung": "tiny"}, boxes=1, measurement=row)])["tiny"]
    assert summary["n"] == 1 and summary["completed"] == summary["approved"] == 0
    assert summary["status_counts"] == {"unsupported_data": 1}
    assert summary["censored"] == 0
    assert summary["core_seconds"] == row["timing_s"]["total"]
    assert summary["approved_plans_per_core_hour"] == 0
    assert not (tmp_path/"never-approved.json").exists()


def test_throughput_charges_failed_and_interrupted_attempts(campaign):
    rows = [dict(identity={"rung": "small"}, boxes=36, measurement=row) for row in [
        measurement(status="ok", completed=True, approved=True, checker_passed=True),
        measurement(timing_s={"total": 200., "search": 190.}),
        measurement(status="interrupted", timing_s={"total": 300., "search": 290.}),
    ]]
    summary = campaign.summarize(rows)["small"]
    assert summary["n"] == 3
    assert summary["completed"] == summary["approved"] == 1
    assert summary["core_seconds"] == 600.
    assert summary["approved_plans_per_core_hour"] == pytest.approx(6.)
    assert summary["completion_rate"] == pytest.approx(1/3)
    assert summary["valid_gaps"] == []
    assert summary["censored"] == 1


def fake_pipeline(monkeypatch, campaign, diagnostic, tmp_path, *, checker_passed=True,
                  mask_status="passed", save_error=False, finalized_status="ok"):
    import stowage.bounds
    import stowage.checker
    import stowage.frozen
    import stowage.io
    import stowage.moves
    import stowage.planner
    count = SimpleNamespace(weighted_nonproductive=0, model_dump=lambda **kwargs: {"loads": 1})
    applied = SimpleNamespace(final_state=SimpleNamespace(ship=SimpleNamespace(
        placements=[SimpleNamespace(container_id="a")])),
        legal=True, violations=[], count=count)
    report = SimpleNamespace(passed=checker_passed, violations=[], shut_out=[], count=count, parts={})
    result = SimpleNamespace(status="ok", message="fake", metrics={}, plan=object())
    bound = SimpleNamespace(weighted=0, model_dump=lambda **kwargs: {"weighted": 0})
    monkeypatch.setattr(stowage.bounds, "lower_bound", lambda instance: bound)
    monkeypatch.setattr(stowage.frozen, "require_clean", lambda *args, **kwargs: None)
    monkeypatch.setattr(stowage.moves, "apply_plan", lambda *args: applied)
    finalized = SimpleNamespace(**{**vars(result), "status": finalized_status})
    monkeypatch.setattr(stowage.planner, "finalize", lambda *args, **kwargs: finalized)
    monkeypatch.setattr(stowage.checker, "check", lambda *args: report)
    saved = []
    def save_plan(plan, path):
        if save_error:
            raise OSError("synthetic artifact write failure")
        saved.append(path)
        path.write_text("fake-plan")
    monkeypatch.setattr(stowage.io, "save_plan", save_plan)
    replayed = []
    def replay(*args):
        replayed.append(True)
        return {"status": mask_status}
    helper = SimpleNamespace(search_deadline=lambda seconds: nullcontext(), replay=replay)
    args = SimpleNamespace(diagnostic_c2_off=diagnostic, check=True, time_limit_s=1.,
        solver_seed=0, validation_limit_s=1., ballast_limit_s=1.)
    row = campaign.measure(SimpleNamespace(load_list=["a"]),
        SimpleNamespace(plan=lambda *args, **kwargs: result), args, tmp_path/"approved.json", helper)
    return row, saved, replayed


def test_diagnostic_c2_off_cannot_approve_even_with_passing_checker(campaign, monkeypatch, tmp_path):
    row, saved, replayed = fake_pipeline(monkeypatch, campaign, True, tmp_path)
    assert row["completed"] and row["checker_passed"]
    assert row["diagnostic_c2_off"]
    assert not row["approved"]
    assert saved == replayed == []
    assert row["mask"]["status"] != "passed"


def test_strict_approval_requires_checker_and_replay(campaign, monkeypatch, tmp_path):
    row, saved, replayed = fake_pipeline(monkeypatch, campaign, False, tmp_path)
    assert row["completed"] and row["checker_passed"] and row["approved"]
    assert row["mask"]["status"] == "passed"
    assert saved and replayed
    assert row["approved_plan_sha256"] == campaign.sha(saved[0])
    assert row["gap_valid"] and row["gap"] == 0


@pytest.mark.parametrize("checker_passed,mask_status", [(False, "passed"), (True, "failed")])
def test_failed_checker_or_mask_never_writes_gold(campaign, monkeypatch, tmp_path, checker_passed, mask_status):
    row, saved, replayed = fake_pipeline(monkeypatch, campaign, False, tmp_path,
        checker_passed=checker_passed, mask_status=mask_status)
    assert not row["approved"]
    assert saved == []
    if not checker_passed:
        assert not row["gap_valid"] and row["gap"] is None
        assert replayed == []


def test_artifact_write_failure_cannot_count_as_approved(campaign, monkeypatch, tmp_path):
    row, saved, replayed = fake_pipeline(monkeypatch, campaign, False, tmp_path, save_error=True)
    assert row["completed"] and row["checker_passed"] and replayed
    assert "artifact write failure" in row["error"]
    assert not row["approved"]
    assert saved == []


def test_finalize_failure_cannot_approve_checker_passing_plan(campaign, monkeypatch, tmp_path):
    row, saved, replayed = fake_pipeline(monkeypatch, campaign, False, tmp_path, finalized_status="error")
    assert row["completed"] and row["checker_passed"]
    assert row["status"] == "error"
    assert not row["approved"] and saved == []


def test_result_resume_rejects_changed_config_and_source(campaign, monkeypatch, tmp_path):
    monkeypatch.setattr(campaign, "SCOPE", tmp_path)
    source = ["source-a"]
    helper = SimpleNamespace(source_hash=lambda: source[0])
    monkeypatch.setattr(campaign, "helpers", lambda: helper)
    monkeypatch.setattr(campaign, "generate", lambda entry, directory: (
        SimpleNamespace(load_list=["a"]),
        {"boxes": 1, "generation_s": .01, "sha256": "input-a",
         "identity": {"generation_source_sha256": "original-physics"}}, directory/"unused.json"))
    calls = []
    def measure(*args):
        calls.append(True)
        return measurement()
    monkeypatch.setattr(campaign, "measure", measure)
    args = ["--output", str(tmp_path/"run"), "--rungs", "tiny", "--count", "1"]
    assert campaign.main(args) == 0
    assert campaign.main(args) == 0
    assert len(calls) == 1
    with pytest.raises(ValueError, match="resume mismatch"):
        campaign.main(args+["--time-limit-s", "121"])
    source[0] = "source-b"
    with pytest.raises(ValueError, match="resume mismatch"):
        campaign.main(args)
    assert len(calls) == 1
    source[0] = "source-a"
    target = next((tmp_path/"run"/"results").glob("*.result.json"))
    row = json.loads(target.read_text())
    artifact = tmp_path/"approved-plan.json"
    artifact.write_text("checked-artifact")
    row["measurement"].update(approved=True, approved_plan=str(artifact),
        approved_plan_sha256=campaign.sha(artifact))
    target.write_text(json.dumps(row))
    assert campaign.main(args) == 0
    artifact.write_text("tampered-artifact")
    with pytest.raises(ValueError, match="approved plan changed"):
        campaign.main(args)
    assert len(calls) == 1
