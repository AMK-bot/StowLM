"""Imported-response provenance and immutable-source tests; no model/solver calls."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from experiments.mvp.strategy.proposer.providers import JsonlProvider, make_provider
from experiments.mvp.strategy.proposer.registration import (
    collection_requirements, validate_collection_provenance, validate_proposal_provenance,
)


PROMPT = "the actual prompt\n"
PROMPT_SHA = hashlib.sha256(PROMPT.encode()).hexdigest()
INPUT_SHA = hashlib.sha256(b"actual input bytes").hexdigest()


def provenance(arm="spark", attempt=1):
    return dict(**collection_requirements(arm, attempt), prompt_sha256=PROMPT_SHA,
                input_sha256=INPUT_SHA)


def write_row(tmp_path, *, arm="spark", attempt=1, change=None):
    row = dict(map_id="map-a", arm=arm, attempt=attempt, text="a layout\n",
               collection_provenance=provenance(arm, attempt),
               usage={"input_tokens": 42, "output_tokens": 5})
    if change:
        change(row)
    path = tmp_path / "responses.jsonl"
    path.write_text(json.dumps(row) + "\n")
    return path, row


def propose(provider, *, arm="spark", attempt=1, **overrides):
    args = dict(instance=None, map_id="map-a", arm=arm, attempt=attempt,
                prompt=PROMPT, input_sha256=INPUT_SHA)
    args.update(overrides)
    return provider.propose(**args)


@pytest.mark.parametrize("arm", ["spark", "glimmer"])
def test_registered_collection_retained_separate_from_evaluation(tmp_path, arm):
    path, row = write_row(tmp_path, arm=arm, attempt=4)
    provider = JsonlProvider(path)
    response = propose(provider, arm=arm, attempt=4)
    assert response.text == row["text"]
    assert response.metadata["collection_provenance"] == row["collection_provenance"]
    assert response.metadata["collection_provenance"]["seed"] == 182
    assert "prompt_sha256" not in response.metadata
    assert response.metadata["source_row"]["usage"] == row["usage"]
    assert validate_proposal_provenance(response.metadata, arm=arm, attempt=4,
        prompt_sha256=PROMPT_SHA, input_sha256=INPUT_SHA,
        expected_source_sha256=provider.sha256) == row["collection_provenance"]


@pytest.mark.parametrize("field", list(provenance()))
def test_missing_collection_field_fails_closed(tmp_path, field):
    path, _ = write_row(tmp_path, change=lambda row: row["collection_provenance"].pop(field))
    with pytest.raises(ValueError, match="provenance"):
        propose(JsonlProvider(path))


@pytest.mark.parametrize("field,value", [
    ("prompt_sha256", "0" * 64), ("input_sha256", "0" * 64),
    ("model", "target-finetuned-model"), ("generation_settings", {"temperature": 0}),
    ("seed", 42), ("base_model", False), ("adapter", "target-trained"),
    ("independent", False), ("feedback", "checker"), ("zero_shot", False),
    ("seed", True), ("base_model", 1), ("adapter", False), ("independent", 1),
    ("feedback", None), ("zero_shot", 1),
])
def test_contradictory_collection_metadata_fails_closed(tmp_path, field, value):
    path, _ = write_row(tmp_path, change=lambda row: row["collection_provenance"].update({field: value}))
    with pytest.raises(ValueError, match="provenance"):
        propose(JsonlProvider(path))


@pytest.mark.parametrize("value", [None, "unknown", []])
def test_unverifiable_collection_fails_closed(tmp_path, value):
    path, _ = write_row(tmp_path, change=lambda row: row.update(collection_provenance=value))
    with pytest.raises(ValueError, match="collection_provenance"):
        propose(JsonlProvider(path))


def test_unregistered_provenance_or_top_level_contradiction_fails_closed(tmp_path):
    path, _ = write_row(tmp_path, change=lambda row: row.update(adapter="target-trained"))
    with pytest.raises(ValueError, match="unregistered"):
        propose(JsonlProvider(path))
    path, _ = write_row(tmp_path, change=lambda row: row["collection_provenance"].update(checker_feedback=True))
    with pytest.raises(ValueError, match="unregistered"):
        propose(JsonlProvider(path))


@pytest.mark.parametrize("settings", [
    dict(temperature=0.7, top_p=True, max_output_tokens=2048),
    dict(temperature=0.7, top_p=1.0, max_output_tokens=2048.0),
    dict(temperature=0.7, top_p=1.0, max_output_tokens=2048, extra="setting"),
])
def test_generation_settings_are_exact_and_typed(tmp_path, settings):
    path, _ = write_row(tmp_path, change=lambda row: row["collection_provenance"].update(generation_settings=settings))
    with pytest.raises(ValueError, match="generation_settings"):
        propose(JsonlProvider(path))


@pytest.mark.parametrize("override", [{"prompt": "stale prompt"}, {"input_sha256": "0" * 64},
                                      {"input_sha256": None}])
def test_actual_prompt_and_input_bound_to_collection(tmp_path, override):
    path, _ = write_row(tmp_path)
    with pytest.raises(ValueError, match="sha256"):
        propose(JsonlProvider(path), **override)


@pytest.mark.parametrize("provider_name", ["jsonl", "dry-run"])
def test_mutated_source_rejected_before_consumption(tmp_path, provider_name):
    path, _ = write_row(tmp_path)
    registered_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text("a later version\n")
    kwargs = {"proposals" if provider_name == "jsonl" else "canned_text": path}
    with pytest.raises(ValueError, match="source changed"):
        make_provider(provider_name, **kwargs, expected_sha256=registered_hash)


@pytest.mark.parametrize("provider_name", ["jsonl", "dry-run"])
def test_hash_and_parse_use_the_same_single_read(tmp_path, monkeypatch, provider_name):
    path, row = write_row(tmp_path)
    original = path.read_bytes()
    expected = hashlib.sha256(original).hexdigest()
    reads = []

    def one_read(self):
        assert not reads, "provider must not reopen source for parsing"
        reads.append(self)
        return original

    monkeypatch.setattr(Path, "read_bytes", one_read)
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: pytest.fail("separate text read"))
    kwargs = {"proposals" if provider_name == "jsonl" else "canned_text": path}
    provider = make_provider(provider_name, **kwargs, expected_sha256=expected)
    result = propose(provider)
    assert len(reads) == 1
    assert result.metadata["source_sha256"] == expected
    assert result.text == (row["text"] if provider_name == "jsonl" else original.decode())


def test_provider_retains_original_snapshot_after_source_replaced(tmp_path):
    path, row = write_row(tmp_path)
    provider = JsonlProvider(path)
    path.write_text("replacement invalid json")
    assert propose(provider).text == row["text"]


def test_proposal_metadata_cannot_mutate_the_provider_snapshot(tmp_path):
    path, _ = write_row(tmp_path)
    provider = JsonlProvider(path)
    first = propose(provider)
    first.metadata["source_row"]["collection_provenance"]["adapter"] = "trained"
    first.metadata["collection_provenance"]["model"] = "changed"
    assert propose(provider).metadata["collection_provenance"] == provenance()


@pytest.mark.parametrize("change", [
    lambda m: m.update(provider="dry-run"),
    lambda m: m.update(source_sha256="0" * 64),
    lambda m: m.update(canned="fixed-text"),
    lambda m: m.update(provider_error="bad collection"),
    lambda m: m.pop("collection_provenance"),
    lambda m: m.update(model="fine-tuned"),
    lambda m: m["source_row"].update(model="fine-tuned"),
    lambda m: m["source_row"].update(arm="glimmer"),
    lambda m: m["source_row"].update(attempt=2),
    lambda m: m["source_row"].update(map_id="another-map"),
    lambda m: m["source_row"]["collection_provenance"].update(adapter="trained"),
    lambda m: m.pop("source_row"),
])
def test_report_revalidation_rejects_unverifiable_persisted_metadata(tmp_path, change):
    path, _ = write_row(tmp_path)
    provider = JsonlProvider(path)
    metadata = deepcopy(propose(provider).metadata)
    change(metadata)
    with pytest.raises(ValueError):
        validate_proposal_provenance(metadata, arm="spark", attempt=1,
            prompt_sha256=PROMPT_SHA, input_sha256=INPUT_SHA,
            expected_source_sha256=provider.sha256, map_id="map-a")


def test_registration_returns_independent_settings_copies():
    registration = collection_requirements("spark", 1)
    registration["generation_settings"]["temperature"] = 0
    assert collection_requirements("spark", 1)["generation_settings"]["temperature"] == 0.7
    validated = validate_collection_provenance(provenance(), arm="spark", attempt=1,
        prompt_sha256=PROMPT_SHA, input_sha256=INPUT_SHA)
    validated["generation_settings"]["temperature"] = 0
    assert provenance()["generation_settings"]["temperature"] == 0.7
