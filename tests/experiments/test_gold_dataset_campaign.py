"""CPU-only dataset orchestration fixtures, not physical gold evidence."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

import pytest

from eval.splits import load_inventory, spec_key
from llm.stowlm.sft_data import SFTExample, write_dataset


@pytest.fixture
def campaign_dataset():
    path = Path(__file__).resolve().parents[2] / "experiments/sft-data/build.py"
    spec = importlib.util.spec_from_file_location("gold_campaign_dataset", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def publication(tmp_path, monkeypatch, campaign_dataset):
    inventory = load_inventory()
    member = next(row for row in inventory.manifest["entries"] if row["split"] == "train")
    pin = {"split_version": "v2", "split_entries_sha256": inventory.entries_sha256}
    inputs, gold = tmp_path / "inputs", tmp_path / "gold"
    inputs.mkdir()
    gold.mkdir()
    (inputs / "instance.json").write_bytes(b"unit input")
    record = {"input_relative_path": "instance.json", "input_sha256": sha256(b"unit input").hexdigest(),
              "plan": {}, "split_spec": member["spec"]}
    raw = json.dumps(record).encode()
    (gold / "row.json").write_bytes(raw)
    entry = {"path": "row.json", "sha256": sha256(raw).hexdigest(), "spec_key": spec_key(member["spec"]),
             "input_sha256": record["input_sha256"], "split": "train", **pin}
    manifest = {"format": "tiny-gold-manifest@1.0", "c2": "on/default", "accepted": 1,
                "relevant_source_sha256": "test-epoch", "shards": [entry], **pin}
    path = gold / "manifest.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(campaign_dataset, "current_gold_epoch", lambda: "test-epoch")
    monkeypatch.setattr(campaign_dataset, "load_instance", lambda _: object())
    monkeypatch.setattr(campaign_dataset, "parse_plan", lambda _: object())
    admitted = []
    def admit(*args, **kwargs):
        admitted.append(kwargs)
        return SFTExample("unit", "train", [1, 2, 3, 4, 5], 3, 1, 1, "unit-plan")
    monkeypatch.setattr(campaign_dataset, "admit_gold", admit)
    def unit_writer(examples, out, **kwargs):
        assert kwargs == {"production": False, "required_splits": ("train",)}
        # Exercise real staging/statistics using explicit unverified fixtures.
        return write_dataset(examples, out, production=False, required_splits=("train",))
    monkeypatch.setattr(campaign_dataset, "write_dataset", unit_writer)
    return path, inputs, manifest, admitted, member["spec"]["rung"]


def test_completed_resume_hashes_outputs_and_does_not_readmit(campaign_dataset, publication, tmp_path):
    path, inputs, _, admitted, rung = publication
    out = tmp_path / "dataset"
    kwargs = {"required_splits": ("train",), "resume": True}
    stats = campaign_dataset.build_dataset(path, inputs, out, **kwargs)
    assert len(admitted) == 1
    assert stats["rungs"][rung]["splits"]["train"]["prompt_tokens"]["sum"] == 3
    assert stats["rungs"][rung]["all"]["completion_tokens"]["sum"] == 2
    assert stats["acceptance"]["published_selected"] == 1
    assert campaign_dataset.build_dataset(path, inputs, out, **kwargs) == stats
    assert len(admitted) == 1
    (out / "train.jsonl").write_text("changed")
    with pytest.raises(ValueError, match="output drift"):
        campaign_dataset.build_dataset(path, inputs, out, **kwargs)


def test_resume_checks_input_before_using_done_marker(campaign_dataset, publication, tmp_path):
    path, inputs, _, admitted, _ = publication
    out = tmp_path / "dataset"
    kwargs = {"required_splits": ("train",), "resume": True}
    campaign_dataset.build_dataset(path, inputs, out, **kwargs)
    (inputs / "instance.json").write_bytes(b"drift")
    with pytest.raises(ValueError, match="input SHA256"):
        campaign_dataset.build_dataset(path, inputs, out, **kwargs)
    assert len(admitted) == 1


def test_interrupted_build_without_done_retries_admission(campaign_dataset, publication, tmp_path):
    path, inputs, _, admitted, _ = publication
    out = tmp_path / "dataset"
    kwargs = {"required_splits": ("train",), "resume": True}
    campaign_dataset.build_dataset(path, inputs, out, **kwargs)
    (out / "build.done.json").unlink()
    campaign_dataset.build_dataset(path, inputs, out, **kwargs)
    assert len(admitted) == 2


def test_resume_refuses_publication_drift(campaign_dataset, publication, tmp_path):
    path, inputs, manifest, admitted, _ = publication
    out = tmp_path / "dataset"
    kwargs = {"required_splits": ("train",), "resume": True}
    campaign_dataset.build_dataset(path, inputs, out, **kwargs)
    manifest["extra_metadata"] = "changed after publication"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="input/source drift"):
        campaign_dataset.build_dataset(path, inputs, out, **kwargs)
    assert len(admitted) == 1


@pytest.mark.parametrize("field,value", [("diagnostic", True), ("evidence_eligible", False)])
def test_diagnostic_manifest_refused_before_admission(campaign_dataset, publication, tmp_path, field, value):
    path, inputs, manifest, admitted, _ = publication
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="diagnostic gold"):
        campaign_dataset.build_dataset(path, inputs, tmp_path / "out", required_splits=("train",), resume=True)
    assert admitted == []
    assert not (tmp_path / "out").exists()


def test_campaign_requires_actual_epoch_manifest(campaign_dataset, publication, tmp_path):
    path, inputs, _, admitted, _ = publication
    with pytest.raises(ValueError, match="epoch instance manifest schema"):
        campaign_dataset.build_dataset(path, inputs, tmp_path / "out", required_splits=("train",),
                                       require_epoch=True)
    assert not admitted


def test_campaign_calls_shared_epoch_validator(campaign_dataset, publication, tmp_path, monkeypatch):
    path, inputs, _, admitted, _ = publication
    (inputs / "manifest.json").write_text('{"epoch_id":"unit"}')
    def reject(*args, **kwargs):
        raise ValueError("shared validator rejected unit drift")
    monkeypatch.setattr(campaign_dataset, "validate_epoch_manifest", reject)
    with pytest.raises(ValueError, match="shared validator rejected"):
        campaign_dataset.build_dataset(path, inputs, tmp_path / "out", required_splits=("train",),
                                       require_epoch=True)
    assert not admitted


def test_zero_gold_does_not_publish_done_marker(campaign_dataset, publication, tmp_path):
    path, inputs, manifest, admitted, _ = publication
    manifest.update(shards=[], accepted=0)
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="no published gold"):
        campaign_dataset.build_dataset(path, inputs, tmp_path / "out", required_splits=("train",), resume=True)
    assert not admitted
    assert not (tmp_path / "out").exists()
