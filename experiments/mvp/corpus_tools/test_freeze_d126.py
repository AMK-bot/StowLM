"""Certificate-only final fitting freeze; evaluation files deliberately absent."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("freeze_d126_under_test", Path(__file__).with_name("freeze_d126.py"))
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"
    config = dict(split="train", rung="small", vessel="feeder_500", generator="random_yard",
                  seed=30100000, port="laem_chabang_like", booking_slack=.25, booking_mass_fraction=.55)
    pending = dict(config, seed=30100001)
    path = freeze.fitting_path(root, config)
    write(path, {"synthetic": "instance"})
    audit = dict(config=config, accepted=True, rule_freeze_v1_enabled=False, c2_screened=False,
                 global_screen=0, instance_sha256=freeze.sha(path), source_files={"src/original.py": "original-source-hash"},
                 schema_version="1.9", instance_versions={"checker": "2.2"}, requested_targets=13,
                 background_checks=1, background_last={"accepted": True, "physics_sha256": "actual-physics-hash"},
                 elapsed_generation_s=12, stage_environment={"generation": {"STOWAGE_BALLAST_V4": None}})
    write(path.with_suffix(".audit.json"), audit)
    for split, specs in (("train", [config, pending]), ("val", [])):
        write(root / f"{split}.requested.json", {"specs": specs, "counts": {"small": len(specs)}})
    # No physical evaluation directories: opening any evaluation path would fail.
    evaluation = dict(split="test", path="test/preserved.json", sha256="preserved-instance-hash",
                      audit_path="test/preserved.audit.json", audit_sha256="preserved-audit-hash", ship="feeder_500",
                      rung="tiny", family="random_yard", seed=8000000, active_for_current_release=True,
                      source_binding_status="drift", release_epoch="v1_preserved", schema_version="1.9", versions={"checker": "2.2"})
    manifest = dict(format="stowlm-mvp-corpus-v2", files=[evaluation],
        requested_vs_emitted=[dict(split="test", ship="feeder_500", rung="tiny", family="random_yard", requested=1, emitted=1)],
        hashes={evaluation["path"]: evaluation["sha256"], evaluation["audit_path"]: evaluation["audit_sha256"]},
        requested_counts={"test": 1, "showcase": 0}, requested_counts_including_deferred={"test": 1, "showcase": 0},
        release={"current_release": "v2.1", "ship_settings": {"feeder_500": {"booking_slack": .25, "booking_mass_fraction": .55}}},
        counts={"test": 1, "showcase": 0, "train": 0, "val": 0}, complete=False)
    write(root / "manifest.json", manifest)
    evidence = tmp_path / "stopped.json"
    write(evidence, {"stopped": True, "owner_recorded_pid": 123})
    return root, evidence, config, audit, manifest


def test_evaluation_is_retained_without_access_and_inventory_unchanged(corpus):
    root, evidence, config, _, original = corpus
    inventory_bytes = (root / "train.requested.json").read_bytes()
    frozen, report, original_bytes = freeze.assemble(root, evidence)
    assert next(row for row in frozen["files"] if row["split"] == "test") == original["files"][0]
    assert frozen["hashes"]["test/preserved.json"] == "preserved-instance-hash"
    assert (root / "train.requested.json").read_bytes() == inventory_bytes
    assert frozen["counts"]["train"] == 1 and not frozen["complete"]
    row = next(row for row in frozen["requested_vs_emitted"] if row["split"] == "train")
    assert (row["requested"], row["emitted"], row["pending"]) == (2, 1, 1)
    epoch = next(iter(report["source_epochs"].values()))
    assert epoch["source_files"] == {"src/original.py": "original-source-hash"}
    assert report["producer_gates"]["artifacts_verified"] == 1
    assert json.loads(original_bytes) == original


@pytest.mark.parametrize("change,message", [({"accepted": False}, "producer OFF"),
    ({"c2_screened": True}, "C2 OFF"), ({"background_checks": 0}, "D58"),
    ({"instance_sha256": "wrong"}, "checksum"), ({"source_files": {}}, "source hashes")])
def test_gate_and_hash_failures_reject(corpus, change, message):
    root, evidence, config, audit, _ = corpus
    audit.update(change)
    write(freeze.fitting_path(root, config).with_suffix(".audit.json"), audit)
    with pytest.raises(ValueError, match=message):
        freeze.assemble(root, evidence)


def test_stopped_evidence_required(corpus):
    root, evidence, *_ = corpus
    write(evidence, {"stopped": False})
    with pytest.raises(ValueError, match="stopped=true"):
        freeze.assemble(root, evidence)


def test_legacy_tiny_global_screen_is_unknown_not_fabricated(corpus):
    root, _, config, audit, _ = corpus
    tiny = dict(config, rung="tiny")
    path = freeze.fitting_path(root, tiny)
    write(path, {"synthetic": "legacy tiny"})
    audit.update(config=tiny, instance_sha256=freeze.sha(path))
    audit.pop("global_screen")
    write(path.with_suffix(".audit.json"), audit)
    record, provenance, _ = freeze.artifact_record(root, tiny, None, {})
    assert record["global_screen"] is None
    assert provenance["runtime"]["global_screen"] is None
    assert record["global_screen_evidence"] == "not_recorded_legacy_tiny"
    audit["c2_screened"] = None
    write(path.with_suffix(".audit.json"), audit)
    with pytest.raises(ValueError, match="C2 OFF"):
        freeze.artifact_record(root, tiny, None, {})


def test_missing_small_global_screen_is_rejected(corpus):
    root, evidence, config, audit, _ = corpus
    audit.pop("global_screen")
    write(freeze.fitting_path(root, config).with_suffix(".audit.json"), audit)
    with pytest.raises(ValueError, match="C2 OFF"):
        freeze.assemble(root, evidence)


def test_execution_writes_archive_and_sums_once(corpus):
    root, evidence, *_ = corpus
    old = (root / "manifest.json").read_bytes()
    result = freeze.execute(root, evidence)
    assert (root / "archive/D126/manifest.pre-D126.json").read_bytes() == old
    assert result["manifest_sha256"] == freeze.sha(root / "manifest.json")
    assert (root / "FROZEN-D126.json").exists() and (root / "train/SHA256SUMS").exists()
    with pytest.raises(ValueError, match="already exists"):
        freeze.execute(root, evidence)
