"""Training representation shifts and absent evidence never look like a match."""
import hashlib
import json

import pytest

from llm.stowlm.planner import StowLMPlanner
from stowage.model import Provenance
from eval.training_lineage import adapter_training_lineage, training_lineage_fields


def adapter(tmp_path, data, *, stats=None):
    directory = tmp_path / "adapter"
    directory.mkdir()
    (directory / "adapters.safetensors").write_bytes(b"weights")
    training = {"data_identity": data}
    if stats is not None:
        dataset = tmp_path / "dataset"
        dataset.mkdir()
        payload = json.dumps(stats).encode()
        (dataset / "stats.json").write_bytes(payload)
        data["files_sha256"] = {"stats.json": hashlib.sha256(payload).hexdigest()}
        training["config"] = {"data_dir": str(dataset)}
    (directory / "adapters.safetensors.provenance.json").write_text(json.dumps({
        "schema_version": 1, "weights_sha256": hashlib.sha256(b"weights").hexdigest(),
        "provenance": Provenance(stubs={}).model_dump(mode="json"), "training": training}))
    return StowLMPlanner(adapter_path=directory)


def test_old_prompt_is_flagged_even_when_training_epoch_matches(tmp_path):
    from llm.stowlm.format import FORMAT_VERSION
    from llm.stowlm.sft_data import DATASET_VERSION
    from stowage.versions import component_versions
    data = {"training_epochs": ["same"], "prompt": "stowlm-prompt@2.1", "format": FORMAT_VERSION,
            "dataset_version": DATASET_VERSION, "training_catalogue_sha256": [component_versions()["rule_catalogue"]]}
    record = training_lineage_fields(adapter_training_lineage(adapter(tmp_path, data)), "same")
    assert record["training_epoch_differs"] is False
    assert record["training_version_differs"] is True
    assert record["training_version_mismatches"] == ["prompt"]
    assert record["training_versions"]["prompt"] == ["stowlm-prompt@2.1"]
    assert record["training_lineage_unknown"] is False


def test_historical_missing_versions_and_epochs_have_an_explicit_unknown_label(tmp_path):
    record = training_lineage_fields(adapter_training_lineage(adapter(tmp_path, {})), "evaluation")
    assert record["training_version_differs"] is None
    assert record["training_lineage_unknown"] is True
    assert record["training_lineage_note"] == "[training lineage unknown]"
    assert record["training_versions"]["prompt"] is None


def test_versions_can_be_recovered_only_from_original_content_pinned_stats(tmp_path):
    planner = adapter(tmp_path, {"training_epochs": ["old"]}, stats={
        "prompt": "stowlm-prompt@2.1", "format": "stowlm@1.0", "dataset": "stowlm-sft@2.0"})
    first = adapter_training_lineage(planner)
    assert first["training_versions"]["prompt"] == ["stowlm-prompt@2.1"]
    (tmp_path / "dataset/stats.json").write_text(json.dumps({"prompt": "fabricated"}))
    changed = adapter_training_lineage(planner)
    assert changed["training_versions"]["prompt"] is None


def test_base_model_does_not_claim_unknown_adapter_training():
    record = training_lineage_fields(adapter_training_lineage(StowLMPlanner()), "evaluation")
    assert record["training_lineage_unknown"] is False
    assert record["training_lineage_note"] is None


@pytest.mark.parametrize("changed", [None, "prompt", "format", "dataset", "rule_catalogue"])
def test_all_representation_and_rule_versions_are_compared_from_recorded_evidence(tmp_path, changed):
    from eval.training_lineage import current_data_versions
    versions = current_data_versions()
    if changed is not None:
        versions[changed] = "a" * 64 if changed == "rule_catalogue" else "historical-version"
    data = {"training_epochs": ["epoch"], **versions}
    planner = adapter(tmp_path, data)
    sidecar = tmp_path / "adapter/adapters.safetensors.provenance.json"
    before = sidecar.read_bytes()
    record = training_lineage_fields(adapter_training_lineage(planner), "epoch")
    assert record["training_version_differs"] is (changed is not None)
    assert record["training_version_mismatches"] == ([] if changed is None else [changed])
    assert record["training_lineage_unknown"] is False
    assert sidecar.read_bytes() == before


def test_reusable_producer_helper_keeps_observed_versions_and_missing_values():
    from eval.training_lineage import data_version_identity
    assert data_version_identity({"prompt": "old-prompt", "dataset_version": "old-data",
                                  "training_catalogue_sha256": ["b" * 64]}) == {
        "prompt": ["old-prompt"], "format": None, "dataset": ["old-data"],
        "rule_catalogue": ["sha256:" + "b" * 64]}
