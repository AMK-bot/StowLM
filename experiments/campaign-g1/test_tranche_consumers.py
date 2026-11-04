"""Tranche consumers retain availability and measured-cell boundaries."""
from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent


def load_driver(name):
    spec = importlib.util.spec_from_file_location(f"tranche_consumer_{name}", HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def epoch_validation_stub(monkeypatch):
    module = SimpleNamespace(validate_epoch_manifest=lambda *args, **kwargs: None,
                             validate_epoch_instance=lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "eval.splits_epoch", module)
    return module


def manifest_fixture(tmp_path, campaign):
    root = tmp_path / "instances-ediagnostic-test"
    root.mkdir()
    instance = root / "one.json"
    instance.write_text("synthetic bytes; validator stubbed for orchestration test")
    payload = {"epoch_id": "diagnostic-test", "allocation_complete": False,
               "not_generated_spec_keys": ["later-a", "later-b"],
               "rejected": 0, "bindings": {"one": {}},
               "groups": {"random_yard/tiny": {"shards": [
                   {"instance": instance.name, "split": "train", "vessel": "ship",
                    "sha256": campaign.sha(instance)}]}}}
    (root / "manifest.json").write_text(json.dumps(payload))
    return root, payload


def test_only_generated_members_selected_and_unattempted_are_not_rejections(tmp_path, epoch_validation_stub):
    campaign = load_driver("campaign")
    root, payload = manifest_fixture(tmp_path, campaign)
    args = SimpleNamespace(instance_root=root, diagnostic=True, ship=None, rungs=["tiny"], count=1)
    selected, _ = campaign.select_inputs(args, "diagnostic-test")
    assert len(selected) == 1
    assert campaign.generation_coverage(payload) == {
        "allocation_complete": False, "generated": 1, "rejected": 0,
        "not_generated": 2, "pending": 0, "tranches": {}}
    args.count = 2
    with pytest.raises(ValueError, match="not generated yet"):
        campaign.select_inputs(args, "diagnostic-test")


def test_missing_previously_generated_file_is_corruption(tmp_path, epoch_validation_stub):
    campaign = load_driver("campaign")
    root, _ = manifest_fixture(tmp_path, campaign)
    (root / "one.json").unlink()
    args = SimpleNamespace(instance_root=root, diagnostic=True, ship=None, rungs=["tiny"], count=1)
    with pytest.raises(ValueError, match="binding has no file"):
        campaign.select_inputs(args, "diagnostic-test")


def test_completed_tranche_admits_evidence_without_complete_allocation(tmp_path, monkeypatch, epoch_validation_stub):
    campaign = load_driver("campaign")
    root, _ = manifest_fixture(tmp_path, campaign)
    experiment = tmp_path / "campaign-g1"
    monkeypatch.setattr(campaign, "HERE", experiment)
    release = campaign.ROOT / "experiments/splits/v2/manifest.json"
    prep = experiment / "diagnostic-test/regen/manifest.json"
    preparation = dict(complete=True, allocation_complete=False, epoch_id="diagnostic-test",
        cache_warm_complete=True, stages_completed=["instances", "bindings", "cache_warm_preflight"],
        instance_root=str(root), split_manifest=str(release), current_tranche="first",
        tranches={"first": {"complete": True}})
    campaign.atomic_json(prep, preparation)
    args = SimpleNamespace(instance_root=root, diagnostic=False, ship=None, rungs=["tiny"], count=1)
    selected, _ = campaign.select_inputs(args, "diagnostic-test")
    assert len(selected) == 1
    preparation["tranches"]["first"]["complete"] = False
    campaign.atomic_json(prep, preparation)
    with pytest.raises(ValueError, match="tranche .* has not completed"):
        campaign.select_inputs(args, "diagnostic-test")


def test_manifest_extension_requires_new_campaign_output_even_diagnostic(tmp_path):
    campaign = load_driver("campaign")
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"bindings": {"a": {}}}')
    identity = {"scripts_sha256": {}, "provenance_sha256": {str(manifest): campaign.sha(manifest)}}
    manifest.write_text('{"bindings": {"a": {}, "b": {}}}')
    with pytest.raises(ValueError, match="provenance drift"):
        campaign.runner_check(SimpleNamespace(allow_drift=True), identity)


def test_projection_requires_matching_rung_generator_and_ship():
    schedule = load_driver("schedule")
    sample = {"measured": {"random_yard/tiny/ship": {
        "samples": 2, "mean_wall_s": 10., "peak_rss_gb": .2}}}
    exact = schedule.measured_schedule(sample, {"random_yard/tiny/ship": 4}, 2)
    assert exact["projected_total_core_s"] == 40
    assert exact["projected_total_wall_s"] == 20
    for key in ("random_yard/full/ship", "simulated_yard/tiny/ship", "random_yard/tiny/other"):
        result = schedule.measured_schedule(sample, {key: 1}, 1)
        assert result["projected_total_wall_s"] is None
        assert result["unmeasured_target_cells"] == [key]
    assert schedule.measured_schedule(sample)["projected_total_wall_s"] is None
