"""D219 data invariants, independent of the reader and sealed evaluations."""
import hashlib
import json
from pathlib import Path
import re

import pytest

from experiments.mvp.reader import build_data_v2 as builder
from experiments.mvp.reader.schema import canonicalize, messages
from experiments.mvp.reader.train import validate_splits


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    output = tmp_path_factory.mktemp("reader-data-v2")
    manifest = builder.build(output)
    return output, manifest, {s: builder.rows(output / f"{s}.jsonl") for s in ("train", "valid", "test")}


def skeleton(sentence):
    sentence = builder.fold(sentence)
    sentence = re.sub(r"\b(?:sgsin|mypkg|thlch)\b", "{port}", sentence)
    return re.sub(r"-?\d+(?:\.\d+)?", "{value}", sentence)


def test_schema_prompt_targets_and_identity(generated):
    _, _, splits = generated
    validate_splits(splits["train"], splits["valid"])
    ids = [r["id"] for data in splits.values() for r in data]
    assert len(ids) == len(set(ids))
    for data in splits.values():
        for row in data:
            assert row["messages"][:-1] == messages(row["sentence"], row["context"])
            assert canonicalize(row["messages"][-1]["content"], row["context"]) == canonicalize(row["expected"], row["context"])
            assert row["is_loosening"] == (row["expected"].get("refusal", {}).get("code") == "loosening")


def test_families_sentences_and_value_skeletons_are_disjoint(generated):
    _, _, splits = generated
    for left, right in (("train", "valid"), ("train", "test"), ("valid", "test")):
        assert not ({r["family"] for r in splits[left]} & {r["family"] for r in splits[right]})
        assert not ({builder.fold(r["sentence"]) for r in splits[left]} & {builder.fold(r["sentence"]) for r in splits[right]})
        assert not ({skeleton(r["sentence"]) for r in splits[left]} & {skeleton(r["sentence"]) for r in splits[right]})


def test_validation_and_family_test_preserved_and_mixture_raised(generated):
    output, manifest, splits = generated
    for split in ("valid", "test"):
        assert (output / f"{split}.jsonl").read_bytes() == (builder.HERE / "data" / f"{split}.jsonl").read_bytes()
    train = splits["train"]
    assert 1800 <= len(train) <= 2600
    assert sum(r["intent"] == "loosening" for r in train) / len(train) >= .30
    assert sum(r["intent"] == "hedged" for r in train) / len(train) >= .18
    assert manifest["spark_v2_spend_usd"] == 0
    assert len(manifest["error_evidence"]) >= 5
    for filename, entry in manifest["files"].items():
        assert hashlib.sha256((output / filename).read_bytes()).hexdigest() == entry["sha256"]


def test_authored_variants_share_construction_family(generated):
    _, manifest, splits = generated
    authored = [r for r in splits["train"] if r["source"] == "authored_d219"]
    assert set(r["family"] for r in authored) == {"d219." + f for f in builder.FAMILIES}
    # One manifest entry owns every variant of a construction; no split by value,
    # wrapper, context, or alternative wording is permitted.
    for family, (kind, _, templates) in builder.FAMILIES.items():
        name = "d219." + family
        entries = [f for f in manifest["families"] if f["family"] == name]
        assert len(entries) == 1 and entries[0]["templates"] == templates
        assert all(r["split"] == "train" for r in authored if r["family"] == name)
        if kind == "loosening":
            assert all(r["expected"]["refusal"]["code"] == "loosening" for r in authored if r["family"] == name)


def test_no_sealed_access_even_during_rebuild(tmp_path, monkeypatch):
    original_bytes, original_text = Path.read_bytes, Path.read_text
    def check(path):
        assert "sealed" not in path.name.lower(), "builder attempted to open a sealed artifact"
    def read_bytes(path, *args, **kwargs):
        check(path)
        return original_bytes(path, *args, **kwargs)
    def read_text(path, *args, **kwargs):
        check(path)
        return original_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "read_text", read_text)
    result = builder.build(tmp_path)
    checked = json.loads((tmp_path / "manifest.json").read_text())
    assert result["files"] == checked["files"]


def test_rebuild_is_byte_reproducible(generated, tmp_path):
    output, _, _ = generated
    builder.build(tmp_path)
    for name in ("train.jsonl", "valid.jsonl", "test.jsonl", "manifest.json"):
        assert (output / name).read_bytes() == (tmp_path / name).read_bytes()
