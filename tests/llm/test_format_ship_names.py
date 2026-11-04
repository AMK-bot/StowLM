"""D33 ship aliases have one name in model observations."""
import json

import pytest

from llm.stowlm.format_prompt import (
    LEGACY_PROMPT_VERSION, PROMPT_VERSION, EXPERT_PROMPT_VERSION, instance_to_prompt, parse_prompt, prompt_version,
)
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1


@pytest.mark.parametrize("raw, canonical", [
    ("feeder_1000", "feeder_800"),
    ("realistic_1000", "realistic_800"),
    ("feeder_1000.slice_b01-b03", "feeder_800.slice_b01-b03"),
])
def test_prompt_canonicalizes_ship_and_parent_without_mutating_instance(raw, canonical):
    instance = build_instance()
    vessel = instance.vessel.model_copy(update={"id": raw, "slice_of": "feeder_1000"})
    instance = instance.model_copy(update={"vessel": vessel})
    wire = json.loads(instance_to_prompt(instance))
    assert wire["ship_id"] == [canonical, "feeder_800"]
    assert parse_prompt(json.dumps(wire))["vessel"] == {"id": canonical, "slice_of": "feeder_800"}
    assert instance.vessel.id == raw
    assert instance.vessel.slice_of == "feeder_1000"


@pytest.mark.parametrize("enabled", [False, True])
def test_ship_normalization_keeps_v22_and_expert_prompt_is_v30(enabled):
    with rule_freeze_v1(enabled=enabled):
        assert LEGACY_PROMPT_VERSION == "stowlm-prompt@2.2"
        assert PROMPT_VERSION == "stowlm-prompt@2.2"
        assert EXPERT_PROMPT_VERSION == "stowlm-prompt@3.0"
        assert prompt_version(build_instance()) == (EXPERT_PROMPT_VERSION if enabled else PROMPT_VERSION)


@pytest.mark.parametrize("version", ["stowlm-prompt@2.0", "stowlm-prompt@2.1"])
def test_parser_keeps_archived_prompt_versions_readable(version):
    with rule_freeze_v1(enabled=False):
        wire = json.loads(instance_to_prompt(build_instance()))
    wire["v"] = version
    assert parse_prompt(json.dumps(wire))["format"] == version
