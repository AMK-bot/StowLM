"""Review regressions for readable ranks, version selection and physical geometry."""
import json

from llm.stowlm import format_prompt as prompt
from llm.stowlm.format import load_tokenizer
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.model.moves import Load
from stowage.moves import crossing_paths
from stowage.rules.catalogue import rule_freeze_v1


def test_exported_prompt_constant_cannot_claim_active_bytes():
    assert prompt.PROMPT_VERSION == "stowlm-prompt@2.2"
    for enabled in (False, True):
        with rule_freeze_v1(enabled=enabled):
            assert prompt.active_prompt_version() == ("stowlm-prompt@3.0" if enabled else prompt.PROMPT_VERSION)


def test_mass_rank_is_in_each_box_record_ties_share_rank():
    with rule_freeze_v1():
        data = build_instance().model_dump(mode="json")
        for box, mass in zip(data["containers"], [12000, 12000.001, 12000, 20000], strict=True):
            box["gross_mass_kg"] = mass
        text = prompt.instance_to_prompt(Instance.model_validate(data))
        wire = json.loads(text)
        assert "m" not in wire
        assert [row[5]["mass_rank"] for row in prompt.parse_prompt(text)["cargo"]] == [3, 2, 3, 1]
        records = [record for _, group in wire["c"] for record in group.split()]
        assert sorted(records) == ["0:3", "1:2", "2:3", "3:1"]


def test_rank_digit_tokens_never_cross_box_records():
    with rule_freeze_v1():
        text = prompt.instance_to_prompt(build_instance())
        wire = json.loads(text)
        tokenizer = load_tokenizer()
        encoded = tokenizer.encode(text, add_special_tokens=False)
        rank_spans = []
        for _, group in wire["c"]:
            start = text.index(json.dumps(group)) + 1
            offset = 0
            for record in group.split():
                rank_start = start + offset + record.index(":") + 1
                rank_spans.append((rank_start, start + offset + len(record)))
                offset += len(record) + 1
        for a, b in encoded.offsets:
            assert sum(a < end and b > start for start, end in rank_spans) <= 1


def test_hold_and_deck_coordinate_difference_survives_prompt():
    with rule_freeze_v1():
        data = build_instance().model_dump(mode="json")
        data["port"]["max_crane_height_tiers"] = 3
        data["port"]["quay_crane"]["max_on_deck_tiers"] = 3
        a = Instance.model_validate(data)
        for cell in data["vessel"]["cells"]:
            if cell["stack_id"] == "00101H":
                cell["tcg_m"] = 2.6
        b = Instance.model_validate(data)
        cid = next(box.id for box in a.containers if box.length_ft == 20)
        move = Load(call_index=0, container_id=cid, src="A-001-01-01", dst="0010102")
        assert crossing_paths(a, move).ship_stack_ids != crossing_paths(b, move).ship_stack_ids
        assert prompt.instance_to_prompt(a) != prompt.instance_to_prompt(b)
        coordinates = dict(prompt.prompt_facts(b)["stack_geometry"])
        assert coordinates["00101H"] == 2.6
        assert coordinates["00101D"] == -1.3


def test_a8_yard_blocker_uses_own_limit_even_below_or_without_quay_h():
    for height in (None, 8):
        with rule_freeze_v1():
            data = build_instance().model_dump(mode="json")
            data["port"]["max_crane_height_tiers"] = height
            if height is not None:
                data["port"]["quay_crane"]["max_on_deck_tiers"] = height
            ids = [box["id"] for box in data["containers"]]
            data["load_list"] = ids[:1]
            data["yard"]["stacks"] = [
                dict(key="A-001-01", length_ft=40, boxes=ids[:1]),
                dict(key="B-003-02", length_ft=20, boxes=ids[1:]),
            ]
            left = Instance.model_validate(data)
            data["yard"]["stacks"][1]["key"] = "B-005-02"
            right = Instance.model_validate(data)
            assert prompt.instance_to_prompt(left) != prompt.instance_to_prompt(right)
            facts = prompt.prompt_facts(left)
            assert facts["anonymous_crossing_blockers"] == [[20, 3, 1, "5"]]
            assert facts["call_rules"][0]["yard_blocking_tiers"] == [["A", 3], ["B", 3]]


def test_a8_unequal_block_limits_round_trip_without_quay_assumptions():
    with rule_freeze_v1():
        data = build_instance().model_dump(mode="json")
        data["port"]["max_crane_height_tiers"] = 8
        data["port"]["quay_crane"]["max_on_deck_tiers"] = 8
        data["port"]["yard"]["blocks"][1]["max_tiers"] = 2
        instance = Instance.model_validate(data)
        text = prompt.instance_to_prompt(instance)
        assert prompt.parse_prompt(text)["call_rules"][0]["yard_blocking_tiers"] == [["A", 3], ["B", 2]]
