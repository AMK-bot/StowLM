"""Harness rows disclose training data from the adapter's validated sidecar."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from eval import harness
from llm.stowlm.planner import StowLMPlanner
from stowage.model import PlannerResult, Provenance


def artifacts(tmp_path, data_identity=None):
    model, adapter = tmp_path / "model", tmp_path / "adapter"
    model.mkdir()
    adapter.mkdir()
    (model / "model.safetensors").write_bytes(b"base")
    (adapter / "adapters.safetensors").write_bytes(b"adapter")
    metadata = {"schema_version": 1, "weights_sha256": hashlib.sha256(b"adapter").hexdigest(),
                "provenance": Provenance(stubs={}).model_dump(mode="json"),
                "training": {"data_identity": data_identity or {}}}
    (adapter / "adapters.safetensors.provenance.json").write_text(json.dumps(metadata))
    return StowLMPlanner(model_path=model, adapter_path=adapter)


def setup(monkeypatch, instance):
    row = {"path": "unused", "spec_key": "a", "split": "val", "rung": "tiny",
           "input_sha256": "hash", "epoch_id": "eval-epoch", "spec": {}}
    monkeypatch.setattr(harness, "_load_selected", lambda *a, **kw: instance)
    monkeypatch.setattr(harness, "select_instances", lambda *a, **kw: [row])
    monkeypatch.setattr(harness, "lower_bound", lambda *a: SimpleNamespace(
        model_dump=lambda **kw: {"weighted": 0}))
    monkeypatch.setattr(harness, "run_planner", lambda planner, *a, **kw: (
        PlannerResult(status="error", planner=planner.name, planner_version=planner.version), None))
    return row


@pytest.mark.parametrize("epochs, differs", [(["train-epoch"], True), (["eval-epoch"], False),
                                           (["eval-epoch", "older-epoch"], True), ([], None)])
def test_each_record_copies_validated_training_lineage_and_compares_epochs(
        tmp_path, empty_ship_instance, monkeypatch, epochs, differs):
    planner = artifacts(tmp_path, {"training_epochs": epochs, "training_catalogue_sha256": ["a" * 64]})
    row = setup(monkeypatch, empty_ship_instance)
    record = harness._evaluate(planner, row, 0, None, True, None)
    assert record["training_epochs"] == epochs
    assert record["training_catalogue_sha256"] == ["a" * 64]
    assert record["training_epoch_differs"] is differs
    assert record["training_epoch_note"] == ("training epoch ≠ evaluation epoch" if differs else None)
    assert record["training_lineage"]["status"] == "validated"
    assert record["training_lineage"]["adapter_weights_sha256"] == hashlib.sha256(b"adapter").hexdigest()


def test_missing_training_values_are_unknown_and_run_json_is_not_authority(
        tmp_path, empty_ship_instance, monkeypatch):
    planner = artifacts(tmp_path)
    (tmp_path / "adapter/run.json").write_text(json.dumps({"training_epochs": ["invented"]}))
    row = setup(monkeypatch, empty_ship_instance)
    record = harness._evaluate(planner, row, 0, None, True, None)
    assert record["training_epochs"] is None
    assert record["training_catalogue_sha256"] is None
    assert record["training_epoch_differs"] is None


def test_base_model_has_no_training_adapter_lineage(tmp_path, empty_ship_instance, monkeypatch):
    row = setup(monkeypatch, empty_ship_instance)
    planner = StowLMPlanner(model_path=tmp_path)
    record = harness._evaluate(planner, row, 0, None, True, None)
    assert record["training_epochs"] is None
    assert record["training_epoch_differs"] is None
    assert record["training_lineage"]["status"] == "base_model"


def test_missing_evaluation_epoch_does_not_invent_comparison(tmp_path, empty_ship_instance, monkeypatch):
    planner = artifacts(tmp_path, {"training_epochs": ["train-epoch"]})
    row = setup(monkeypatch, empty_ship_instance)
    row.pop("epoch_id")
    record = harness._evaluate(planner, row, 0, None, True, None)
    assert record["training_epochs"] == ["train-epoch"]
    assert record["training_epoch_differs"] is None


def test_training_metadata_must_match_already_pinned_adapter_content(
        tmp_path, empty_ship_instance, monkeypatch):
    planner = artifacts(tmp_path, {"training_epochs": ["train-epoch"]})
    row = setup(monkeypatch, empty_ship_instance)
    planner._harness_weight_identity = {"adapter_content_sha256": planner.evaluation_config["adapter_content_sha256"]}
    sidecar = tmp_path / "adapter/adapters.safetensors.provenance.json"
    metadata = json.loads(sidecar.read_text())
    metadata["training"]["data_identity"]["training_epochs"] = ["relabelled"]
    sidecar.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="pinned evaluation identity"):
        harness._evaluate(planner, row, 0, None, True, None)


@pytest.mark.parametrize("damage", ["weights", "missing_sidecar", "schema"])
def test_direct_evaluation_rejects_unbound_training_metadata(
        tmp_path, empty_ship_instance, monkeypatch, damage):
    planner = artifacts(tmp_path, {"training_epochs": ["train"]})
    row = setup(monkeypatch, empty_ship_instance)
    sidecar = tmp_path / "adapter/adapters.safetensors.provenance.json"
    if damage == "weights":
        (tmp_path / "adapter/adapters.safetensors").write_bytes(b"replaced")
    elif damage == "missing_sidecar":
        sidecar.unlink()
    else:
        metadata = json.loads(sidecar.read_text())
        metadata["schema_version"] = 999
        sidecar.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="provenance|lineage"):
        harness._evaluate(planner, row, 0, None, True, None)


def test_resume_refuses_relabelled_training_lineage(tmp_path, empty_ship_instance, monkeypatch):
    planner = artifacts(tmp_path, {"training_epochs": ["train-epoch"], "training_catalogue_sha256": ["a" * 64]})
    setup(monkeypatch, empty_ship_instance)
    run = tmp_path / "run"
    harness.run_evaluation(planner, run_dir=run, rungs=["tiny"], tiny_run=True)
    saved = run / "instances/a.json"
    record = json.loads(saved.read_text())
    record["training_epochs"] = ["invented"]
    saved.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="training lineage"):
        harness.run_evaluation(planner, run_dir=run, rungs=["tiny"], tiny_run=True)


def test_publication_rechecks_adapter_content_after_aggregation(tmp_path, empty_ship_instance, monkeypatch):
    planner = artifacts(tmp_path, {"training_epochs": ["train-epoch"]})
    setup(monkeypatch, empty_ship_instance)
    checks = []

    def publication(*args, **kwargs):
        checks.append(True)
        if len(checks) == 2:
            (tmp_path / "adapter/adapters.safetensors").write_bytes(b"replaced before publication")

    monkeypatch.setattr(harness, "_verify_publication_snapshot", publication)
    with pytest.raises(ValueError, match="provenance|lineage"):
        harness.run_evaluation(planner, run_dir=tmp_path / "run", rungs=["tiny"], tiny_run=True)
    assert not (tmp_path / "run/summary.json").exists()
