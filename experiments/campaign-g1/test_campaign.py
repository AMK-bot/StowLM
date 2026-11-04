"""Safety boundaries for the G1 orchestration, without running any solver."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace

import pytest


HERE = Path(__file__).resolve().parent


def load_driver(name):
    """Load the hyphenated experiment directory without modifying the repo."""
    module_name = f"g1_test_{name}"
    spec = importlib.util.spec_from_file_location(module_name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(HERE))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(HERE))
    return module


@pytest.fixture
def campaign():
    return load_driver("campaign")


@pytest.mark.parametrize("diagnostic,allow_drift", [(False, False), (True, False), (True, True)])
def test_valid_modes(campaign, diagnostic, allow_drift):
    campaign.validate_modes(diagnostic, allow_drift)


def test_allow_drift_cannot_be_enabled_for_evidence(campaign):
    with pytest.raises(ValueError):
        campaign.validate_modes(False, True)


@pytest.mark.parametrize("row", [
    {"diagnostic": True}, {"allow_drift": True}, {"evidence_eligible": False},
    {"measurement": {"provenance": [{"diagnostic": True}]}},
])
def test_diagnostic_rows_cannot_be_promoted_to_evidence(campaign, row):
    with pytest.raises(ValueError):
        campaign.require_evidence([{"diagnostic": False}, row])


def test_evidence_guard_accepts_clean_rows(campaign):
    campaign.require_evidence([{"diagnostic": False, "allow_drift": False}])


def test_resume_requires_identical_configuration(campaign, tmp_path):
    path = tmp_path / "identity.json"
    identity = {"epoch": "e123", "count": 2, "ballast_seconds": 30, "diagnostic": True}
    campaign.ensure_identity(path, identity)
    original = path.read_bytes()
    campaign.ensure_identity(path, dict(identity))
    assert path.read_bytes() == original
    for key, changed in (("epoch", "e456"), ("count", 3), ("ballast_seconds", 2), ("diagnostic", False)):
        with pytest.raises((ValueError, RuntimeError)):
            campaign.ensure_identity(path, {**identity, key: changed})
        assert path.read_bytes() == original


def test_done_marker_requires_matching_identity_and_result_contents(campaign, tmp_path):
    marker = tmp_path / "done.json"
    result = tmp_path / "result.json"
    identity = {"epoch": "e123", "budget_s": 30}
    assert campaign.valid_done(marker, identity) is False
    result.write_text(json.dumps({"adapter_completed": True, "attempt_id": "test-attempt"}))
    campaign.write_done(marker, identity, result)
    assert campaign.valid_done(marker, dict(identity)) is True
    assert campaign.valid_done(marker, {**identity, "budget_s": 2}) is False
    result.write_text(json.dumps({"adapter_completed": False}))
    assert campaign.valid_done(marker, identity) is False
    result.unlink()
    assert campaign.valid_done(marker, identity) is False


@pytest.mark.parametrize("content", ["{", "{}", "null", "[]"])
def test_malformed_done_markers_never_resume(campaign, tmp_path, content):
    marker = tmp_path / "done.json"
    marker.write_text(content)
    assert campaign.valid_done(marker, {"epoch": "e123"}) is False


def test_summary_zero_checker_denominator_is_unknown(campaign):
    stats = campaign.stage_stats([{"adapter_completed": True,
                                   "measurement": {"status": "no_cargo_candidate"}}])
    assert stats["attempted"] == 1
    assert stats["full_checker_observed"] == 0
    assert stats["pass_pct"] == 0
    assert stats["mean_approved_moves"] is None


def test_summary_rejected_objectives_do_not_enter_quality_averages(campaign):
    stats = campaign.stage_stats([
        {"measurement": {"checker_passed": False, "objective": 0, "gap_to_lb": 0}},
        {"measurement": {"checker_passed": True, "approved": True,
                         "objective": 8, "gap_to_lb": .25}},
    ])
    assert stats["pass_pct"] == 50
    assert stats["mean_approved_moves"] == 8
    assert stats["mean_approved_gap_to_lb"] == .25


def test_diagnostic_synthesis_has_no_publishable_summary(campaign, tmp_path):
    result = campaign.synthesize(SimpleNamespace(diagnostic=True), {"epoch_id": "diagnostic-test"},
                                 [{"stage": "4.6", "diagnostic": True}], [], tmp_path)
    assert result["evidence_eligible"] is False
    assert result["g1_met"] is False
    assert result["solver_full_checker_rate"] is None
    assert result["replay_gate_met"] is False
    assert (tmp_path / "diagnostic-summary.md").exists()
    assert not (tmp_path / "summary.md").exists()
    with pytest.raises(ValueError):
        campaign.require_evidence([result])


def test_evidence_synthesis_rejects_diagnostic_inputs_before_publication(campaign, tmp_path):
    with pytest.raises(ValueError):
        campaign.synthesize(SimpleNamespace(diagnostic=False), {"epoch_id": "evidence-test"},
                             [{"stage": "4.6", "diagnostic": True}], [], tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.fixture
def epoch_validation_stub(monkeypatch):
    """Selection tests isolate ordering; real provenance validation is tested in eval."""
    module = SimpleNamespace(validate_epoch_manifest=lambda *args, **kwargs: None,
                             validate_epoch_instance=lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "eval.splits_epoch", module)
    return module


def test_input_selection_filters_test_members_before_reading_files(campaign, tmp_path, epoch_validation_stub):
    instance = tmp_path / "train.json"
    instance.write_text("synthetic placeholder: selection only checks bytes")
    manifest = {"epoch_id": "diagnostic-test", "groups": {"random_yard/tiny": {"shards": [
        {"instance": "absent-heldout.json", "split": "test", "vessel": "ship"},
        {"instance": instance.name, "split": "train", "vessel": "ship",
         "sha256": campaign.sha(instance)},
    ]}}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    args = SimpleNamespace(instance_root=tmp_path, diagnostic=True, ship="ship", rungs=["tiny"], count=1)
    selected, _ = campaign.select_inputs(args, "diagnostic-test")
    assert len(selected) == 1
    assert selected[0]["path"] == str(instance)
    instance.write_text("changed input bytes")
    with pytest.raises(ValueError, match="digest mismatch"):
        campaign.select_inputs(args, "diagnostic-test")


def test_empty_input_cohort_cannot_qualify_a_campaign(campaign, tmp_path, epoch_validation_stub):
    (tmp_path / "manifest.json").write_text(json.dumps({"epoch_id": "diagnostic-test", "groups": {}}))
    args = SimpleNamespace(instance_root=tmp_path, diagnostic=True, ship="ship", rungs=["tiny"], count=1)
    with pytest.raises(ValueError, match="not generated yet"):
        campaign.select_inputs(args, "diagnostic-test")


def test_campaign_rejects_a_different_regeneration_epoch_even_diagnostic(campaign, tmp_path, epoch_validation_stub):
    (tmp_path / "manifest.json").write_text(json.dumps({"epoch_id": "different", "groups": {}}))
    args = SimpleNamespace(instance_root=tmp_path, diagnostic=True, ship=None, rungs=["tiny"], count=1)
    with pytest.raises(ValueError, match="different source epoch"):
        campaign.select_inputs(args, "diagnostic-test")


def test_campaign_passes_diagnostic_policy_to_shared_provenance_validator(campaign, tmp_path, epoch_validation_stub):
    observed = []
    epoch_validation_stub.validate_epoch_manifest = lambda *args, **kwargs: observed.append(kwargs)
    (tmp_path / "manifest.json").write_text(json.dumps({"epoch_id": "diagnostic-test", "groups": {}}))
    args = SimpleNamespace(instance_root=tmp_path, diagnostic=True, allow_drift=True,
                           ship=None, rungs=["tiny"], count=1)
    with pytest.raises(ValueError, match="not generated yet"):
        campaign.select_inputs(args, "diagnostic-test")
    assert observed == [{"allow_diagnostic": True, "check_source": False}]


def test_tiny_calibration_never_projects_medium_or_full(campaign):
    sample = {"solve_time_per_size": {"4.7/tiny": {"mean_s": 2}}}
    estimate = campaign.calibrated_stage_estimate(sample, "4.7", ["tiny", "medium", "full"], 100, 12)
    assert estimate["scaled_core_s"] is None
    assert estimate["scaled_wall_s"] is None
    assert estimate["uncalibrated_rungs"] == ["medium", "full"]
    same_rung = campaign.calibrated_stage_estimate(sample, "4.7", ["tiny"], 3, 1)
    assert same_rung["scaled_core_s"] == same_rung["scaled_wall_s"] == 6


def test_diagnostic_drift_never_excuses_changed_instance_bytes(campaign, tmp_path):
    path = tmp_path / "instance.json"
    path.write_text("old")
    identity = {"scripts_sha256": {}, "inputs": [{"path": str(path), "sha256": campaign.sha(path)}]}
    path.write_text("changed")
    with pytest.raises(ValueError, match="instance content drift"):
        campaign.runner_check(SimpleNamespace(allow_drift=True), identity)


def test_primary_jobs_start_independent_stages_together(campaign):
    selected = [{"rung": rung, "sha256": str(i)} for i, rung in enumerate(("tiny", "full"))]
    jobs = campaign.primary_jobs(selected)
    assert [job["stage"] for job in jobs[:4]] == list(campaign.PRIMARY)
    assert [job["stage"] for job in jobs[4:]] == ["4.2", "4.7", "4.8"]


def test_unrun_checkers_are_unknown_in_stage_rows(campaign):
    stats = campaign.stage_stats([
        {"measurement": {"checker_passed": False, "plan_returned": False}},
        {"measurement": {"checker_passed": False, "runner_version": "4.1.0"}},
    ])
    assert stats["full_checker_observed"] == 0
    assert stats["pass_pct"] == 0


def test_gold_affordability_deduplicates_finalized_plans(campaign, tmp_path):
    rows = [{"stage": "replay", "input": {"sha256": "same", "rung": "tiny"},
             "process_wall_s": 5, "measurement": {"gold_eligible": True,
             "finalized_plan_identity": "same-physical-plan", "verified": True,
             "checker_passed": True, "finalized_status": "ok"}} for _ in range(2)]
    result = campaign.synthesize(SimpleNamespace(diagnostic=True), {"epoch_id": "diagnostic-test"},
                                 rows, [{}, {}], tmp_path)
    assert result["gold_qualified_candidates"] == 2
    assert result["gold_plan_count"] == 1
    assert result["gold_plans_per_core_hour"] == 360


def test_source_drift_before_shard_starts_no_process(campaign, tmp_path, monkeypatch):
    monkeypatch.setattr(campaign, "freeze_check", lambda args: (_ for _ in ()).throw(RuntimeError("source drift")))
    def forbidden(*args, **kwargs):
        pytest.fail("a process must not start after freeze drift")
    monkeypatch.setattr(campaign.subprocess, "Popen", forbidden)
    with pytest.raises(RuntimeError, match="source drift"):
        campaign.run_shard(SimpleNamespace(), {}, {"stage": "4.6", "id": "one"}, tmp_path)
    assert not list(tmp_path.iterdir())


def test_source_drift_during_shard_never_publishes_done(campaign, tmp_path, monkeypatch):
    directory = tmp_path / "4.6/one"
    checked = []
    def check(args):
        checked.append(True)
        if len(checked) == 2:
            raise RuntimeError("source drift after shard")
    class Child:
        pid = 12345
        def __init__(self, *args, **kwargs):
            (directory / "result.json").write_text(json.dumps({"stage": "4.6", "adapter_completed": True}))
        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(campaign, "freeze_check", check)
    monkeypatch.setattr(campaign, "command", lambda *args: ["mock-only"])
    monkeypatch.setattr(campaign.subprocess, "Popen", Child)
    args = SimpleNamespace(timeout_s=2, diagnostic=True, allow_drift=False)
    with pytest.raises(RuntimeError, match="source drift after shard"):
        campaign.run_shard(args, {"epoch_id": "diagnostic-test", "scripts_sha256": {}},
                           {"stage": "4.6", "id": "one", "input": {"sha256": "synthetic"}}, tmp_path)
    assert len(checked) == 2
    assert not (directory / "done.json").exists()


def test_diagnostic_replay_probe_is_excluded_from_solver_denominator(campaign, tmp_path):
    probe = {"stage": "replay", "diagnostic": True, "replay_probe": True,
             "measurement": {"checker_passed": False, "verified": False}}
    result = campaign.synthesize(SimpleNamespace(diagnostic=True), {"epoch_id": "diagnostic-test"},
                                 [probe], [], tmp_path)
    assert result["solver_checker_observed"] == 0
    assert result["returned_plan_replay_denominator"] == 0
    assert result["solver_full_checker_rate"] is None
    assert result["diagnostic_invalid_replay_probes"] == 1


def test_regen_marker_pins_output_and_audit_bytes(tmp_path):
    regen = load_driver("regen")
    output = tmp_path / "instance.json"
    audit = output.with_suffix(".audit.json")
    output.write_text("synthetic diagnostic output")
    audit.write_text("synthetic diagnostic audit")
    marker = tmp_path / "done.json"
    job = {"id": "synthetic", "config": {"seed": 2}}
    assert regen.marker_valid(marker, job, "diagnostic-test", True) is False
    regen._commit({"job": job, "result": {"path": str(output)}}, marker, "diagnostic-test", True)
    assert regen.marker_valid(marker, job, "diagnostic-test", True) is True
    audit.write_text("changed audit")
    with pytest.raises(ValueError, match="output missing or edited"):
        regen.marker_valid(marker, job, "diagnostic-test", True)


@pytest.mark.parametrize("epoch,diagnostic,job", [
    ("different", True, {"id": "synthetic"}),
    ("diagnostic-test", False, {"id": "synthetic"}),
    ("diagnostic-test", True, {"id": "different"}),
])
def test_regen_marker_refuses_other_epoch_mode_or_job(tmp_path, epoch, diagnostic, job):
    regen = load_driver("regen")
    marker = tmp_path / "done.json"
    marker.write_text(json.dumps({"epoch_id": "diagnostic-test", "diagnostic": True,
                                 "job": {"id": "synthetic"}, "outputs": {}}))
    with pytest.raises(ValueError, match="incompatible done marker"):
        regen.marker_valid(marker, job, epoch, diagnostic)


@pytest.mark.parametrize("independent_proof,scalable_sha,expected", [(True, "same", 1), (False, "same", 0), (True, "different", 0)])
def test_small_gap_requires_matched_input_and_independent_physical_proof(campaign, tmp_path, independent_proof, scalable_sha, expected):
    rows = [
        {"stage": "4.6", "input": {"rung": "small", "sha256": "same"}, "measurement": {
            "independent_proof": independent_proof, "oracle_checker_passed": True, "physical_objective": 10}},
        {"stage": "4.7", "input": {"rung": "small", "sha256": scalable_sha}, "measurement": {
            "approved": True, "objective": 12}},
    ]
    summary = campaign.synthesize(SimpleNamespace(diagnostic=True), {"epoch_id": "diagnostic-test"}, rows, [], tmp_path)
    assert summary["matched_small_optimum_count"] == expected
    if expected:
        assert summary["exact_vs_scalable_small_gap"][0]["absolute_gap"] == 2
        assert summary["exact_vs_scalable_small_gap"][0]["relative_gap"] == pytest.approx(.2)
    else:
        assert summary["exact_vs_scalable_small_gap"] is None


@pytest.fixture
def frozen_copy():
    """Only mutate a disposable copy inside this executive's owned directory."""
    freeze = load_driver("freeze")
    with tempfile.TemporaryDirectory(prefix="test-freeze-", dir=HERE) as temporary:
        root = Path(temporary)
        for relative in ("src/stowage/rules/catalogue.toml", "src/stowage/versions.py"):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(HERE.parents[1] / relative, target)
        inputs = root / "inputs"
        inputs.mkdir()
        source = inputs / "owned-copy.py"
        shutil.copyfile(HERE / "test_campaign.py", source)
        manifest = root / "freeze-manifest.json"
        freeze.atomic_json(manifest, freeze.create_manifest(root, ["inputs"], diagnostic=True))
        yield freeze, root, source, manifest


def test_unchanged_freeze_has_no_drift(frozen_copy):
    freeze, _, _, manifest = frozen_copy
    assert freeze.check_manifest(manifest, diagnostic=True)["drift"] == []


@pytest.mark.parametrize("mutation, expected", [("change", "changed"), ("delete", "deleted"), ("add", "added")])
def test_freeze_names_changed_added_and_deleted_files(frozen_copy, mutation, expected):
    freeze, root, source, manifest = frozen_copy
    name = "inputs/owned-copy.py"
    if mutation == "change":
        source.write_text(source.read_text() + "\n# Diagnostic drift test.\n")
    elif mutation == "delete":
        source.unlink()
    else:
        (root / "inputs/new.py").write_text("# Diagnostic drift test.\n")
        name = "inputs/new.py"
    with pytest.raises(RuntimeError, match=rf"{expected}: {name}"):
        freeze.check_manifest(manifest, diagnostic=True)
    report = freeze.check_manifest(manifest, allow_drift=True, diagnostic=True)
    assert f"{expected}: {name}" in report["drift"]


def test_diagnostic_freeze_cannot_be_checked_as_evidence(frozen_copy):
    freeze, _, _, manifest = frozen_copy
    with pytest.raises(ValueError):
        freeze.check_manifest(manifest)
    with pytest.raises(ValueError):
        freeze.check_manifest(manifest, allow_drift=True)


def test_freeze_cli_check_exits_nonzero_on_owned_copy_drift(frozen_copy, capsys):
    freeze, _, source, manifest = frozen_copy
    source.write_text(source.read_text() + "\n# Intentional diagnostic mutation.\n")
    assert freeze.main(["--manifest", str(manifest), "--check", "--diagnostic"]) == 1
    assert "changed: inputs/owned-copy.py" in capsys.readouterr().err


def test_freeze_includes_non_python_and_hidden_files(frozen_copy):
    freeze, root, _, _ = frozen_copy
    (root / "inputs/.data.bin").write_bytes(b"public synthetic fixture")
    result = freeze.create_manifest(root, ["inputs"], diagnostic=True)
    assert "inputs/.data.bin" in result["files"]
