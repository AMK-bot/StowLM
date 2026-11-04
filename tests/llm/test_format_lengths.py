"""Source-pinned CPU tokenizer lengths under both rule-freeze regimes.

These are observations of archived ladder and physical-instance sizing sources,
not checker or quality certificates. They load tokenizer JSON only, never weights.
"""
from __future__ import annotations

import hashlib
import json
import re

import pytest

from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.format_prompt import encode_prompt, instance_to_prompt
from stowage.io import load_instance
from stowage.rules.catalogue import rule_freeze_v1


@pytest.fixture(scope="module")
def length_report(repo_root):
    return json.loads((repo_root / "tests/llm/test_format_measurements.json").read_text())["rule_freeze_v1_lengths"]


@pytest.mark.parametrize("rung", ["tiny", "small", "medium", "full", "realistic_500", "realistic_800"])
@pytest.mark.parametrize("enabled", [False, True], ids=["legacy", "active"])
def test_seeded_ladder_prompt_lengths_and_legacy_bytes(repo_root, length_report, rung, enabled):
    row = next(row for row in length_report["rows"] + length_report["dense_rows"] if row["profile"] == rung)
    source = repo_root / row["source"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == row["source_sha256"]
    # The persisted source predates activation. Load it with legacy validation;
    # observe the same exact boxes in each prompt regime to isolate format cost.
    with rule_freeze_v1(enabled=False):
        instance = load_instance(source)
    tokenizer = load_tokenizer()
    with rule_freeze_v1(enabled=enabled):
        wire = instance_to_prompt(instance)
        encoded = encode_prompt(instance, tokenizer)
    assert json.loads(wire)["v"] == ("stowlm-prompt@3.0" if enabled else "stowlm-prompt@2.2")
    measurement = row["active" if enabled else "legacy"]
    assert len(wire) == measurement["wire_chars"]
    assert len(encoded) == measurement["prompt_tokens"]
    assert len(tokenizer.decode(list(encoded), skip_special_tokens=False)) == measurement["chat_chars"]
    assert hashlib.sha256(wire.encode()).hexdigest() == measurement["wire_sha256"]
    assert hashlib.sha256(json.dumps(encoded, separators=(",", ":")).encode()).hexdigest() == measurement["token_ids_sha256"]
    # The 3.0 acceptance budget applies to every rung, including fixed overhead
    # on tiny calls; padding legacy observations is not a valid way to meet it.
    if enabled:
        assert len(encoded) <= 1.2 * row["legacy"]["prompt_tokens"]
    assert measurement["prompt_tokens"] < 131072


def test_prompt_length_evidence_identifies_method_and_regimes(length_report):
    assert length_report["cpu_cores"] == 1
    assert length_report["gpu"] is False
    assert length_report["method"]
    assert length_report["sampling_limits"]
    assert {row["profile"] for row in length_report["rows"]} == {"tiny", "small", "medium", "full"}
    for row in length_report["rows"] + length_report["dense_rows"]:
        assert row["active"]["prompt_tokens"] - row["legacy"]["prompt_tokens"] == row["growth_tokens"]
        assert row["active"]["chat_chars"] - row["legacy"]["chat_chars"] == row["growth_chars"]


@pytest.mark.parametrize("profile", ["tiny", "small", "medium", "full", "dense_17k_pilot"])
def test_production_size_prompt_growth_and_training_limit_are_recorded(repo_root, profile):
    report = json.loads((repo_root / "tests/llm/test_format_measurements.json").read_text())["production_size_lengths"]
    row = next(row for row in report["rows"] if row["profile"] == profile)
    source = repo_root / row["source"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == row["source_sha256"]
    with rule_freeze_v1(enabled=False):
        instance = load_instance(source)
    tokenizer = load_tokenizer()
    for enabled in (False, True):
        with rule_freeze_v1(enabled=enabled):
            encoded = encode_prompt(instance, tokenizer)
        measurement = row["active" if enabled else "legacy"]
        assert len(encoded) == measurement["prompt_tokens"]
        assert measurement["prompt_exceeds_train_default"] == (len(encoded) > 8192)
        assert measurement["estimated_sft_tokens"] == len(encoded) + row["estimated_output_tokens"]
        assert measurement["estimated_sft_exceeds_train_default"] == (measurement["estimated_sft_tokens"] > 8192)
    assert row["active"]["prompt_tokens"] <= 1.2 * row["legacy"]["prompt_tokens"]
    assert report["cpu_cores"] == 1 and report["gpu"] is False
    assert report["train_default_max_seq_length"] == 8192
    assert report["sampling_limits"]
    assert row["full_checker_certified"] is False
    assert row["accepted_training_gold"] is False
    if profile == "dense_17k_pilot":
        completion_source = repo_root / row["completion_source"]
        assert hashlib.sha256(completion_source.read_bytes()).hexdigest() == row["completion_source_sha256"]
        archived = json.loads(completion_source.read_text().splitlines()[0])
        assert archived["instance_id"] == instance.id
        assert "sft_pilot.format_only" in archived["stubs"]
        completion = archived["input_ids"][archived["prompt_len"]:]
        codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
        actions = codec.decode_plan(completion)
        assert codec.encode_plan(actions) == tuple(completion)
        assert len(actions) == archived["n_actions"] == len(instance.load_list)
        assert len(completion) == row["actual_output_tokens"] == 6041
        for regime in ("legacy", "active"):
            assert row[regime]["format_only_total_tokens"] == row[regime]["prompt_tokens"] + len(completion)
            assert row[regime]["format_only_total_exceeds_train_default"] is True


@pytest.mark.parametrize("rung", ["tiny", "small", "medium", "full", "realistic_800", "dense_17k_pilot"])
def test_glimmer_tokens_never_span_two_box_mass_records(repo_root, length_report, rung):
    """Inspect real tokenizer offsets, rather than assuming punctuation isolates BPE."""
    rows = (json.loads((repo_root / "tests/llm/test_format_measurements.json").read_text())["production_size_lengths"]["rows"]
            if rung == "dense_17k_pilot" else length_report["rows"] + length_report["dense_rows"])
    row = next(row for row in rows if row["profile"] == rung)
    with rule_freeze_v1(enabled=False):
        instance = load_instance(repo_root / row["source"])
    with rule_freeze_v1(enabled=True):
        wire = instance_to_prompt(instance)
    cargo = json.loads(wire)["c"]
    spans = []
    for _category, records in cargo:
        # Each box owns its index and exact within-call rank in the wire.
        assert isinstance(records, str)
        assert re.fullmatch(r"[\d/-]+:\d+( [\d/-]+:\d+)*", records)
        start = wire.index(json.dumps(records)) + 1
        spans.extend((start + match.start(), start + match.end())
                     for match in re.finditer(r"[\d/-]+:\d+", records))
    spans.sort()
    assert spans
    tokenizer = load_tokenizer()
    offsets = tokenizer.encode(wire, add_special_tokens=False).offsets
    span_index = 0
    for token_start, token_end in offsets:
        while span_index < len(spans) and spans[span_index][1] <= token_start:
            span_index += 1
        if span_index + 1 < len(spans) and token_start < spans[span_index][1]:
            assert token_end <= spans[span_index + 1][0], (
                "Glimmer token includes fields owned by two boxes", wire[token_start:token_end])


@pytest.mark.parametrize("rung", ["tiny", "small", "medium", "full"])
def test_current_operational_port_prompt_lengths(repo_root, length_report, rung):
    from stowage.model import Instance
    from stowage.ports.profiles import load_port

    row = next(row for row in length_report["rows"] if row["profile"] == rung)
    with rule_freeze_v1(enabled=False):
        instance = load_instance(repo_root / row["source"])
    # A separate synthetic observation uses the current operational port's H=8
    # and DG policy, while retaining archived cargo and its exact yard geometry.
    # It isolates richer port observations; it is not new generator evidence.
    with rule_freeze_v1(enabled=True):
        data = instance.model_dump(mode="json")
        port = load_port(instance.port.archetype).model_dump(mode="json")
        port["yard"] = data["port"]["yard"]
        data["port"] = port
        operational = Instance.model_validate(data)
        tokenizer = load_tokenizer()
        wire = instance_to_prompt(operational)
        encoded = encode_prompt(operational, tokenizer)
    assert operational.port.max_crane_height_tiers == 8
    assert any(rule.rule_id == "port.dg.refused_class" for rule in operational.port.port_rules)
    expected = row["operational_active"]
    assert len(wire) == expected["wire_chars"]
    assert len(encoded) == expected["prompt_tokens"]
    assert len(tokenizer.decode(list(encoded), skip_special_tokens=False)) == expected["chat_chars"]
    assert len(encoded) < 131072
