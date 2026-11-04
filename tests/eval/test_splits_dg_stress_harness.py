"""DG stress is a separate frozen test population, including ungenerated specs.

Synthetic evaluator records exercise release selection and reporting only; they
are diagnostic fixtures, not checker evidence for the allocated cargo instances.
"""
from collections import Counter
from hashlib import sha256
import json

import pytest

from eval import harness
from eval.harness_inventory import select_frozen_instances
from eval.splits import load_inventory, spec_key
from eval.training_lineage import adapter_training_lineage, training_lineage_fields
from tests.eval.test_harness import Diagnostic
from tests.eval.test_harness_inventory import _campaign


RELEASE = "dg-stress-v3"


def _entries(inventory, rung):
    return sorted((entry for entry in inventory.manifest["entries"]
                   if entry["split"] == "test" and entry["spec"]["rung"] == rung),
                  key=lambda entry: spec_key(entry["spec"]))


def test_dg_stress_release_name_keeps_full_test_allocation(tmp_path):
    inventory = load_inventory(RELEASE)
    root = _campaign(tmp_path, inventory, [])
    selection = select_frozen_instances(root, split_manifest=RELEASE, split="test")

    assert selection.split_version == RELEASE
    assert selection.split_entries_sha256 == inventory.entries_sha256
    assert selection.split_entries_sha256 != load_inventory("v2").entries_sha256
    assert selection.allocated_count == 45
    assert selection.available_count == 0
    assert selection.rows == []
    assert Counter(row["rung"] for row in selection.allocated_rows) == {"tiny": 15, "small": 30}
    assert {row["split"] for row in selection.allocated_rows} == {"test"}


def test_dg_stress_harness_preserves_four_statuses_and_denominators(
        tmp_path, empty_ship_instance, monkeypatch):
    inventory = load_inventory(RELEASE)
    entries = {rung: _entries(inventory, rung) for rung in ("tiny", "small")}
    emitted = [entry for rung in entries for entry in entries[rung][:3]]
    root = _campaign(tmp_path, inventory, emitted)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    group = manifest["groups"]["synthetic"]
    group["rejected_shards"] = []
    (root / "synthetic").mkdir()
    for rung, allocated in entries.items():
        entry = allocated[3]
        rejection = root / "synthetic" / f"{rung}-rejected.json"
        rejection.write_text('{"status":"rejected"}')
        group["rejected_shards"].append({
            "spec": entry["spec"], "spec_key": spec_key(entry["spec"]), "split": "test",
            "rejection": rejection.name, "sha256": sha256(rejection.read_bytes()).hexdigest()})
    manifest_path.write_text(json.dumps(manifest))
    statuses = {spec_key(entry["spec"]): status
                for allocated in entries.values()
                for entry, status in zip(allocated[:3],
                    ("feasible-witnessed", "proven-infeasible", "unknown"), strict=True)}
    monkeypatch.setattr(harness, "_load_selected", lambda *args: empty_ship_instance)

    def evaluate(planner, row, *args):
        status = statuses[row["spec_key"]]
        passed = status == "feasible-witnessed"
        return {**{key: value for key, value in row.items() if key != "path"},
                "instance_id": row["spec_key"], "feasibility_status": status,
                "result": {"repaired": False, "metrics": {}},
                "report": {"passed": passed, "parts": {}, "violations": [],
                           "count": {"weighted_nonproductive": 0}},
                "shippable": passed, "report_binding_valid": passed,
                "timing": {"total_s": 0, "plan_s": 0},
                **training_lineage_fields(adapter_training_lineage(planner), None)}

    monkeypatch.setattr(harness, "_evaluate", evaluate)
    run = tmp_path / "run"
    summary = harness.run_evaluation(Diagnostic(), instances_dir=root, run_dir=run,
        split_manifest=RELEASE, split="test", rungs=["tiny", "small"], tiny_run=True)

    assert summary["split_version"] == RELEASE
    assert summary["split_entries_sha256"] == inventory.entries_sha256
    assert summary["n_allocated"] == summary["inventory_allocated_count"] == 45
    assert summary["n_instances"] == summary["inventory_available_count"] == 6
    assert summary["pass_rate"] == pytest.approx(2 / 45)
    assert summary["feasibility_status_counts"] == {
        "feasible-witnessed": 2, "proven-infeasible": 2, "unknown": 39,
        "generation-rejected": 2}
    for rung, total in (("tiny", 15), ("small", 30)):
        assert summary["by_rung"][rung]["n_allocated"] == total
        assert summary["by_rung"][rung]["n_instances"] == 3
        assert summary["by_rung"][rung]["pass_rate"] == pytest.approx(1 / total)
    allocations = json.loads((run / "allocations.json").read_text())
    assert len(allocations) == 45
    assert sum(row.get("allocation_only", False) for row in allocations) == 39
    config = json.loads((run / "config.json").read_text())
    assert config["split_version"] == RELEASE
    assert config["split_entries_sha256"] == inventory.entries_sha256


@pytest.mark.parametrize("campaign_release,selected_release", [("v2", RELEASE), (RELEASE, "v2")])
def test_dg_stress_and_v2_campaign_members_cannot_mix(tmp_path, campaign_release, selected_release):
    inventory = load_inventory(campaign_release)
    root = _campaign(tmp_path, inventory, _entries(inventory, "tiny")[:1])
    with pytest.raises(ValueError, match="selected frozen inventory"):
        select_frozen_instances(root, split_manifest=selected_release, split="test", rungs=["tiny"])


def test_dg_stress_run_directory_cannot_be_reused_for_v2(tmp_path):
    root = _campaign(tmp_path, load_inventory(RELEASE), [])
    kwargs = dict(instances_dir=root, run_dir=tmp_path / "run", split="test",
                  rungs=["tiny", "small"], tiny_run=True)
    harness.run_evaluation(Diagnostic(), split_manifest=RELEASE, **kwargs)
    with pytest.raises(ValueError, match="configuration changed"):
        harness.run_evaluation(Diagnostic(), split_manifest="v2", **kwargs)
    summary = json.loads((tmp_path / "run/summary.json").read_text())
    assert summary["split_version"] == RELEASE
    assert summary["n_allocated"] == 45
