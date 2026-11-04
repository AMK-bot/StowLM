"""Bind dataset provenance to the prompt bytes selected by the activation switch."""
import json

import pytest

from llm.stowlm.format import FORMAT_VERSION, encode_example, load_tokenizer
from llm.stowlm.format_prompt import instance_to_prompt
from llm.stowlm.sft_data import build_example, plan_actions, write_dataset
from stowage.actions import ACTION_SPACE_VERSION
from stowage.rules.catalogue import rule_freeze_v1
from tests.llm.test_sft_data_fixtures import deterministic_case


@pytest.mark.parametrize("enabled", [False, True])
def test_builder_versions_match_encoded_bytes(enabled):
    tokenizer = load_tokenizer()
    with rule_freeze_v1(enabled=enabled):
        instance, plan = deterministic_case(2)
        row = build_example(instance, plan, split="train", tokenizer=tokenizer)
        encoded = encode_example(instance, plan_actions(instance, plan), tokenizer)
        assert row.meta["prompt"] == json.loads(instance_to_prompt(instance))["v"]
        assert row.meta["action_space"] == ACTION_SPACE_VERSION
        assert row.meta["format"] == FORMAT_VERSION
        assert tuple(row.input_ids) == encoded.input_ids
        assert row.prompt_len == encoded.prompt_len
        assert all(row.meta[key] == value for key, value in encoded.meta.items())


@pytest.mark.parametrize("enabled", [False, True])
def test_writer_reports_serialized_prompt_version_even_after_switch_changes(tmp_path, enabled):
    with rule_freeze_v1(enabled=enabled):
        instance, plan = deterministic_case(1)
        row = build_example(instance, plan, split="train", tokenizer=load_tokenizer())
        expected = json.loads(instance_to_prompt(instance))["v"]
    with rule_freeze_v1(enabled=not enabled):
        stats = write_dataset([row], tmp_path)
    assert stats["prompt"] == expected
    assert stats["action_space"] == ACTION_SPACE_VERSION


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("field", ["prompt", "action_space"])
def test_production_writer_rejects_stale_serialization_versions(tmp_path, monkeypatch, enabled, field):
    from llm.stowlm.format_prompt import prompt_version
    from llm.stowlm.sft_data import DATASET_VERSION
    from tests.llm.test_sft_data_writer import production_metadata_example

    with rule_freeze_v1(enabled=enabled):
        row = production_metadata_example()
        row.meta.update(format=FORMAT_VERSION, prompt=prompt_version(deterministic_case(1)[0]),
                        action_space=ACTION_SPACE_VERSION, dataset=DATASET_VERSION)
        row.meta[field] = "stale-version"
        monkeypatch.setattr("llm.stowlm.sft_data.validate_production_lineage", lambda gold: None)
        with pytest.raises(ValueError, match="format version mismatch"):
            write_dataset([row], tmp_path, production=True)
        assert not (tmp_path / "stats.json").exists()
