"""Rule-freeze examples retain canonical actions and their serialization versions.

These are serializer contracts; they do not admit synthetic fixtures as SFT gold.
"""
from __future__ import annotations

import pytest

from llm.stowlm import format as action_format
from llm.stowlm.format import ActionCodec, build_sequence, load_tokenizer
from stowage.actions import ACTION_SPACE_VERSION
from stowage.actions.space import Discharge, Place, Relocate, RestowOff
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1


@pytest.fixture(scope="module")
def tokenizer():
    return load_tokenizer()


def _direct_instance(*, discharge: bool = False) -> tuple[Instance, str]:
    payload = build_instance().model_dump(mode="json")
    box = next(box for box in payload["containers"] if box["length_ft"] == 20)
    box["dg"] = dict(un_number="0004", imdg_class="1.4S", stowage_category="01")
    box["direct_delivery"] = True
    payload["containers"] = [box]
    payload["load_list"] = [] if discharge else [box["id"]]
    payload["discharge_list"] = [box["id"]] if discharge else []
    payload["yard"]["stacks"] = []
    payload["port"]["port_rules"] = [
        dict(rule_id="port.dg.direct_delivery", params={"classes": ["1"]}),
    ]
    if discharge:
        box["pod"] = payload["port"]["locode"]
        payload["onboard"]["placements"] = [dict(container_id=box["id"], slot="0010182")]
    return Instance.model_validate(payload), box["id"]


@pytest.mark.parametrize("enabled", [False, True])
def test_encoded_example_records_all_serialization_versions(tokenizer, enabled):
    with rule_freeze_v1(enabled=enabled):
        instance = build_instance()
        actions = [Place(instance.load_list[0], "0010182")]
        encoded = action_format.encode_example(instance, actions, tokenizer)
        assert encoded.meta == {
            "format": action_format.FORMAT_VERSION,
            "prompt": "stowlm-prompt@3.0" if enabled else "stowlm-prompt@2.2",
            "action_space": ACTION_SPACE_VERSION,
        }
        assert encoded.n_actions == len(actions)
        codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
        assert codec.decode_plan(encoded.input_ids[encoded.prompt_len:]) == actions
        from llm.stowlm.format_prompt import encode_prompt
        assert encoded.input_ids[:encoded.prompt_len] == encode_prompt(instance, tokenizer)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("discharge", [False, True])
def test_direct_delivery_examples_keep_existing_canonical_actions(tokenizer, enabled, discharge):
    with rule_freeze_v1(enabled=enabled):
        instance, cid = _direct_instance(discharge=discharge)
        actions = [Discharge(cid)] if discharge else [Place(cid, "0010182")]
        example = action_format.encode_example(instance, actions, tokenizer)
        codec = ActionCodec(tokenizer, [cid])
        completion = example.input_ids[example.prompt_len:]
        assert codec.decode_plan(completion) == actions
        assert completion == codec.encode_plan(actions)
        text = tokenizer.decode(list(completion), skip_special_tokens=False)
        assert text == ("D000\n<|eot|>" if discharge else "P000 0010182\n<|eot|>")


@pytest.mark.parametrize("enabled", [False, True])
def test_all_action_variants_roundtrip_in_encoded_example(tokenizer, enabled):
    with rule_freeze_v1(enabled=enabled):
        instance = build_instance()
        boxes = sorted(box.id for box in instance.containers)
        actions = [Place(boxes[0], "0010182"), Discharge(boxes[1]),
                   Discharge(boxes[1], "A-001-01"), Relocate(boxes[2], "B-001-02"),
                   RestowOff(boxes[3])]
        # Serialization intentionally does not claim these actions are legal.
        example = action_format.encode_example(instance, actions, tokenizer)
        codec = ActionCodec(tokenizer, boxes)
        assert codec.decode_plan(example.input_ids[example.prompt_len:]) == actions
        assert example.input_ids == build_sequence(tokenizer, [
            example.input_ids[:example.prompt_len], codec.encode_plan(actions),
        ])


def test_empty_example_has_canonical_end_of_plan(tokenizer):
    with rule_freeze_v1(enabled=False):
        instance = build_instance()
        example = action_format.encode_example(instance, [], tokenizer)
        codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
        assert example.n_actions == 0
        assert example.input_ids[example.prompt_len:] == codec.end_plan_tokens
