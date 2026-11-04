"""Real-tokenizer observation contracts, precision and retrieval dependencies."""

import math
from collections import Counter

import pytest

from llm.stowlm.format import BoxIndex, build_sequence, load_tokenizer
from llm.stowlm.format_prompt import (
    encode_prompt, expand_layout, instance_to_prompt, parse_prompt, prompt_facts, system_prompt,
)
from stowage.conventions import canonical_vessel_id
from stowage.io import load_instance
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1, rule_freeze_v1_enabled


@pytest.fixture(autouse=True, params=[False, True], ids=["legacy", "expert"])
def prompt_regime(request):
    with rule_freeze_v1(enabled=request.param):
        yield request.param


@pytest.fixture(scope="module")
def tokenizer():
    return load_tokenizer()


def _prompt_budget(instance):
    # Tight comparative 20% limits and immutable legacy pins live in lengths.
    return ((2500 + 14 * len(instance.load_list)) if rule_freeze_v1_enabled()
            else (2000 + 12 * len(instance.load_list)))


def _roundtrip(instance, tokenizer):
    text = instance_to_prompt(instance)
    ids = build_sequence(tokenizer, [text])
    facts = parse_prompt(tokenizer.decode(list(ids), skip_special_tokens=False))
    assert facts == prompt_facts(instance)
    assert instance_to_prompt(instance) == text
    index = BoxIndex([c.id for c in instance.containers])
    assert facts["load"] == [index.index(cid) for cid in instance.load_list]
    assert facts["discharge"] == [index.index(cid) for cid in instance.discharge_list]
    assert facts["vessel"]["id"] == canonical_vessel_id(instance.vessel.id)
    assert facts["instance"] == instance.id
    assert facts["call"] == instance.call_index
    assert all(box.id not in text for box in instance.containers)
    expected_cargo = set(instance.load_list)
    expected_cargo.update(p.container_id for p in instance.onboard.placements)
    expected_cargo.update(instance.yard.quay_buffer)
    yard = {row[0]: row for row in facts["yard"]}
    expected_anonymous = Counter()
    for stack in instance.yard.stacks:
        targets = [i for i, cid in enumerate(stack.boxes) if cid in instance.load_list]
        suffix = stack.boxes[min(targets):] if targets else []
        expected_cargo.update(suffix)
        if not targets:
            expected_anonymous[(stack.key.split("-")[0], stack.length_ft, len(stack.boxes))] += 1
            assert stack.key not in yard
            continue
        assert yard[stack.key] == [stack.key, stack.length_ft, len(stack.boxes),
                                   [index.index(cid) for cid in reversed(suffix)]]
    assert facts["anonymous_occupancy"] == [[*key, count] for key, count in sorted(expected_anonymous.items())]
    cargo = {row[0]: row for row in facts["cargo"]}
    assert set(cargo) == {index.index(cid) for cid in expected_cargo}
    for box in instance.containers:
        if box.id not in expected_cargo:
            continue
        row = cargo[index.index(box.id)]
        pod_call = instance.call_index if box.id in instance.discharge_list else next(
            call.index for call in instance.rotation.calls[instance.call_index + 1:]
            if call.locode == box.pod)
        mass = math.ceil(box.gross_mass_kg / 2000) * 2000
        assert row[1:5] == [box.length_ft, round(box.height_m * 1000), mass, pod_call]
        assert row[3] - 2000 < box.gross_mass_kg <= row[3]
        if rule_freeze_v1_enabled():
            masses = sorted({candidate.gross_mass_kg for candidate in instance.containers
                             if candidate.id in expected_cargo}, reverse=True)
            assert row[5]["mass_rank"] == masses.index(box.gross_mass_kg) + 1
        assert abs(row[2] / 1000 - box.height_m) <= 0.0005000001
    expected_flags = {cell.id: int(cell.accepts_20) + 2 * int(cell.accepts_40) +
                      4 * int(cell.accepts_45) + 8 * int(cell.reefer_plug)
                      for cell in instance.vessel.cells}
    assert expand_layout(facts["layout"]) == expected_flags
    return facts


@pytest.mark.parametrize("rung", ["tiny", "small", "medium", "full"])
def test_real_tokenizer_round_trip_every_rung(repo_root, tokenizer, rung):
    instance = load_instance(repo_root / "experiments" / "instances" / rung / "seed-00000001.json")
    _roundtrip(instance, tokenizer)
    assert len(encode_prompt(instance, tokenizer)) <= _prompt_budget(instance)


def test_relevant_suffix_and_anonymous_occupancy(tokenizer):
    original = build_instance()
    data = original.model_dump(mode="json")
    ids = [c.id for c in original.containers]
    data["load_list"] = [ids[2]]
    data["yard"]["stacks"] = [
        {"key": "A-001-01", "length_ft": 40, "boxes": [ids[0]]},
        {"key": "B-001-01", "length_ft": 20, "boxes": ids[1:]},
    ]
    instance = Instance.model_validate(data)
    index = BoxIndex(ids)
    facts = _roundtrip(instance, tokenizer)
    assert facts["yard"] == [
        ["B-001-01", 20, 3, [index.index(ids[3]), index.index(ids[2])]],
    ]
    assert {row[0] for row in facts["cargo"]} == {index.index(cid) for cid in ids[2:]}


def test_flags_constraints_ship_and_layout(tokenizer):
    data = build_instance(layover=True).model_dump(mode="json")
    data["containers"][1]["dg"]["segregation_codes"] = ["SG35"]
    data["containers"][3]["oog"].update(over_width_port_m=0.2, over_length_fore_m=0.3)
    data["plan_constraints"] = [{"kind": "reefer_bays", "params": {"bays": [1, 3]},
                                 "hardness": "hard", "source_text": "Keep reefers in bays 1 and 3"}]
    data["rule_overrides"] = {"stability.gm": {"min_gm_m": 0.4}}
    instance = Instance.model_validate(data)
    facts = _roundtrip(instance, tokenizer)
    index = BoxIndex([c.id for c in instance.containers])
    cargo = {row[0]: row[5] for row in facts["cargo"]}
    for box in instance.containers:
        flags = cargo[index.index(box.id)]
        expected = box.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
        for field in ("special_stowage", "reefer"):
            assert flags.get(field) == expected.get(field)
        if box.dg:
            assert flags["dg"]["imdg_class"] == box.dg.imdg_class
            assert flags["dg"].get("segregation_codes", []) == box.dg.segregation_codes
        else:
            assert "dg" not in flags
        if box.oog:
            assert flags["oog"] == "HWL"
        else:
            assert "oog" not in flags
    assert facts["ship"]["placements"] == [[p.slot, index.index(p.container_id)]
                                            for p in instance.onboard.placements]
    assert facts["ship"]["covers_off"] == instance.onboard.covers_off
    assert facts["constraints"]["instructions"] == [c.model_dump(mode="json", exclude_none=True)
                                                     for c in instance.plan_constraints]
    assert facts["constraints"]["rule_overrides"] == instance.rule_overrides


def test_mass_bucket_edges_and_height_precision(tokenizer):
    data = build_instance().model_dump(mode="json")
    masses = [6000, 6000.001, 7999.999, 8000]
    for box, mass in zip(data["containers"], masses, strict=True):
        box["gross_mass_kg"] = mass
        box["height_m"] = 2.59149
        box["iso_type"] = box["iso_type"][:1] + "2" + box["iso_type"][2:]
    instance = Instance.model_validate(data)
    facts = _roundtrip(instance, tokenizer)
    expected = [6000, 8000, 8000, 8000]
    assert [row[3] for row in facts["cargo"]] == expected
    assert {row[2] for row in facts["cargo"]} == {2591}


def test_chat_matches_native_template_and_fixes_recipient(repo_root, tokenizer):
    from jinja2 import Environment

    instance = build_instance()
    text = tokenizer.decode(list(encode_prompt(instance, tokenizer)), skip_special_tokens=False)
    template = Environment().from_string((repo_root / "models/glimmer-30b-4bit/chat_template.jinja").read_text())
    native = template.render(messages=[{"role": "system", "content": system_prompt(instance)},
                                       {"role": "user", "content": instance_to_prompt(instance)}],
                             bos_token="<|begin_of_text|>", add_generation_prompt=True)
    assert text == native + " to=user<|message|>"
    assert "assistant to=self" not in text
    assert text.endswith("<|start|>assistant to=user<|message|>")


def test_parser_rejects_wrong_version():
    with pytest.raises(ValueError, match="unsupported"):
        parse_prompt('{"format":"other"}')


@pytest.mark.parametrize("generator", ["random_yard", "simulated_yard"])
@pytest.mark.parametrize("vessel_name, loads, profile", [("feeder_500", 400, "realistic_500"),
                                                       ("feeder_800", 800, "realistic_800")])
def test_extended_bookings_fit_prompt_budget(repo_root, tokenizer, generator,
                                                   vessel_name, loads, profile):
    """Measure actual released dense shards; serialization does not certify plans."""
    import hashlib
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location("format_extended", repo_root / "experiments/format/extended.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    campaign = repo_root / "experiments/instances-v2"
    manifest = json.loads((campaign / "manifest.json").read_text())
    assert manifest["mode"] == "canonical"
    from stowage.conventions import canonical_vessel_id
    group = next(key for key in manifest["groups"]
                 if key.split("/", 1)[0] == generator
                 and canonical_vessel_id(key.split("/", 1)[1]) == profile)
    shard = min(manifest["groups"][group]["shards"], key=lambda row: row["seed"])
    assert shard["spec"]["dense_20ft"] is True
    assert shard["spec"]["n_boxes"] == loads
    assert canonical_vessel_id(shard["spec"]["vessel"]) == vessel_name
    path = campaign / group / shard["instance"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == shard["sha256"]
    instance, metadata = module.load_canonical_dense(vessel_name, generator=generator)
    assert repo_root / metadata["source"] == path
    assert metadata["source_sha256"] == shard["sha256"]
    assert metadata["manifest_sha256"] == hashlib.sha256((campaign / "manifest.json").read_bytes()).hexdigest()
    assert metadata["manifest_group"] == group
    assert metadata["seed"] == shard["seed"]
    assert instance == load_instance(path)
    assert instance.seed == shard["seed"]
    assert len(instance.load_list) == loads
    assert instance.family["equipment_profile"] == "dense_20ft"
    _roundtrip(instance, tokenizer)
    assert len(encode_prompt(instance, tokenizer)) <= _prompt_budget(instance)
