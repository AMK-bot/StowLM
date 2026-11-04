"""The activated observation exposes the expert contract; legacy bytes stay fixed."""
import hashlib
import json

from llm.stowlm import format_prompt as prompt
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1


def _instance():
    data = build_instance().model_dump(mode="json")
    data["port"]["max_crane_height_tiers"] = 3
    data["port"]["quay_crane"]["max_on_deck_tiers"] = 3
    data["port"]["berthing_side"] = "starboard"
    data["port"]["yard"]["blocks"][1]["truck_lane_side"] = "high"
    return Instance.model_validate(data)


def test_disabled_prompt_and_header_are_byte_identical():
    with rule_freeze_v1(enabled=False):
        instance = build_instance()
        assert hashlib.sha256(prompt.instance_to_prompt(instance).encode()).hexdigest() == "4243c2e8ee07a38d1fceb3fadcbadbdfd1c33a995ad8e5f0d7dab18efcb404eb"
        assert hashlib.sha256(prompt.SYSTEM_PROMPT.encode()).hexdigest() == "c4e38e78d9233aa08a19b0e5bd39c07b096651df6d40532d17b346bc31921ff9"
        assert prompt.prompt_version(instance) == "stowlm-prompt@2.2"
        assert prompt.system_prompt(instance) == prompt.SYSTEM_PROMPT


def test_active_mass_rank_preserves_within_bucket_order_and_drops_ship_classes():
    with rule_freeze_v1():
        data = _instance().model_dump(mode="json")
        for box, mass in zip(data["containers"], [6000, 6000.001, 7999.999, 8000], strict=True):
            box["gross_mass_kg"] = mass
        instance = Instance.model_validate(data)
        facts = prompt.prompt_facts(instance)
        assert facts["format"] == "stowlm-prompt@3.0"
        assert [row[5]["mass_rank"] for row in facts["cargo"]] == [4, 3, 2, 1]
        assert not any(set(row[5]) & {"weight_class", "e1_heavy", "e6_heavy"} for row in facts["cargo"])
        assert prompt.parse_prompt(prompt.instance_to_prompt(instance)) == facts


def test_active_current_and_later_call_limits_unknown_are_explicit():
    with rule_freeze_v1():
        instance = _instance()
        facts = prompt.prompt_facts(instance)
        current = facts["call_rules"][0]
        assert current["height_tiers"] == 3
        assert current["berthing_side"] == "starboard"
        assert current["crane_limits_known"] is True
        assert current["truck_lane_sides"] == [["A", "low"], ["B", "high"]]
        data = instance.model_dump(mode="json")
        data["rotation"]["calls"].append({"locode": "HKHKG", "index": 3, "archetype": "hong_kong_like", "max_on_deck_tiers": 1})
        later = prompt.prompt_facts(Instance.model_validate(data))["call_rules"][3]
        assert later["height_tiers"] is None
        assert later["crane_limits_known"] is False


def test_active_direct_delivery_export_encodes_without_yard_position():
    with rule_freeze_v1():
        data = _instance().model_dump(mode="json")
        data["port"]["port_rules"] = [{"rule_id": "port.dg.direct_delivery", "params": {"classes": ["1"]}}]
        box = data["containers"][1]
        box["dg"]["imdg_class"] = "1.4S"
        box["direct_delivery"] = True
        for stack in data["yard"]["stacks"]:
            stack["boxes"] = [cid for cid in stack["boxes"] if cid != box["id"]]
        data["yard"]["stacks"] = [stack for stack in data["yard"]["stacks"] if stack["boxes"]]
        instance = Instance.model_validate(data)
        facts = prompt.parse_prompt(prompt.instance_to_prompt(instance))
        assert facts["cargo"][1][5]["direct_delivery"] is True
        assert facts["cargo"][0][5]["direct_delivery"] is False
        assert all(1 not in row[3] for row in facts["yard"])


def test_active_box_route_refusals_include_resolved_later_profile():
    with rule_freeze_v1():
        data = _instance().model_dump(mode="json")
        data["rotation"]["calls"].append({"locode": "THLCH", "index": 3, "archetype": "laem_chabang_like"})
        data["containers"][0]["pod"] = "THLCH"
        facts = prompt.prompt_facts(Instance.model_validate(data))
        assert facts["cargo"][0][5]["route_refused_classes"] == [[3, ["7"]]]
        assert facts["cargo"][1][5]["route_refused_classes"] == []


def test_active_rule_header_and_versions():
    with rule_freeze_v1():
        instance = _instance()
        header = prompt.system_prompt(instance)
        assert prompt.prompt_version(instance) == prompt.EXPERT_PROMPT_VERSION == "stowlm-prompt@3.0"
        for text in ("kg", "tolerance", "far rows first", "H(ship deck)/Y(yard)", "Direct=", "Prefer", "Every-lift"):
            assert text in header
        assert "w=exact-VGM" not in header
        assert prompt.prompt_facts(instance)["weight_rules"] == {"tolerance_kg": 0, "heavy_under_deck_threshold_kg": 20000}


def test_active_crossing_row_order_uses_transverse_geometry():
    with rule_freeze_v1():
        instance = _instance()
        facts = prompt.prompt_facts(instance)
        coordinates = dict(facts["stack_geometry"])
        assert coordinates["00101D"] < coordinates["00102D"]
        assert json.loads(prompt.instance_to_prompt(instance))["v"] == "stowlm-prompt@3.0"


def test_active_anonymous_h_high_blockers_keep_exact_addresses():
    with rule_freeze_v1():
        data = _instance().model_dump(mode="json")
        ids = [box["id"] for box in data["containers"]]
        data["load_list"] = ids[:1]
        data["yard"]["stacks"] = [
            {"key": "A-001-01", "length_ft": 40, "boxes": ids[:1]},
            {"key": "B-003-02", "length_ft": 20, "boxes": ids[1:]},
        ]
        facts = prompt.prompt_facts(Instance.model_validate(data), include_layout=False)
        assert facts["anonymous_crossing_blockers"] == [[20, 3, 1, "5"]]
        assert "layout" not in facts


def test_active_exact_ranks_track_sparse_box_indices_and_fractions():
    with rule_freeze_v1():
        data = _instance().model_dump(mode="json")
        ids = [box["id"] for box in data["containers"]]
        data["load_list"] = ids[2:3]
        data["containers"][2]["gross_mass_kg"] = 123456789.125
        data["containers"][3]["gross_mass_kg"] = 12340.000001
        data["yard"]["stacks"] = [
            {"key": "A-001-01", "length_ft": 40, "boxes": ids[:1]},
            {"key": "B-001-01", "length_ft": 20, "boxes": ids[1:]},
        ]
        facts = prompt.prompt_facts(Instance.model_validate(data))
        assert [(row[0], row[5]["mass_rank"]) for row in facts["cargo"]] == [(2, 1), (3, 2)]


def test_active_integer_ranks_preserve_box_alignment_after_reordering():
    with rule_freeze_v1():
        data = _instance().model_dump(mode="json")
        masses = [6000, 16000, 8000, 20000]
        for box, mass in zip(data["containers"], masses, strict=True):
            box["gross_mass_kg"] = mass
        data["containers"].reverse()
        text = prompt.instance_to_prompt(Instance.model_validate(data))
        assert "m" not in json.loads(text)
        assert [row[5]["mass_rank"] for row in prompt.parse_prompt(text)["cargo"]] == [4, 2, 3, 1]


def test_active_drops_obsolete_ship_class_order_overrides_without_mutation():
    with rule_freeze_v1():
        data = _instance().model_dump(mode="json")
        overrides = {"class_order": ["H", "M", "L"], "order_mode": "weight_class", "weight_order_tolerance_kg": 1}
        data["rule_overrides"] = {"expert.e2.weight_order": overrides}
        instance = Instance.model_validate(data)
        facts = prompt.prompt_facts(instance)
        assert facts["constraints"]["rule_overrides"]["expert.e2.weight_order"] == {"weight_order_tolerance_kg": 1}
        assert instance.rule_overrides["expert.e2.weight_order"] == overrides


def test_active_fixed_deck_heights_and_hold_only_target_row_geometry():
    with rule_freeze_v1():
        data = _instance().model_dump(mode="json")
        vessel = data["vessel"]
        deck = next(stack for stack in vessel["stacks"] if stack["on_deck"])
        source_cells = {cell["id"]: cell for cell in vessel["cells"]}
        fixed = dict(deck, id="00901D", bay=9, hatch_cover_id=None,
                     cell_ids=["0090182", "0090184"])
        vessel["fixed_load_stacks"] = [fixed]
        vessel["fixed_load_cells"] = [dict(source_cells[cid], id=new, stack_id=fixed["id"])
                                      for cid, new in zip(deck["cell_ids"], fixed["cell_ids"], strict=True)]
        boxes = [dict(cell_ids=[cell["id"]], length_ft=20, height_m=2.591,
                      gross_mass_kg=10000.0, x_m=cell["lcg_m"], y_m=cell["tcg_m"],
                      z_m=cell["vcg_base_m"] + 2.591 / 2, stack_id=fixed["id"], tier=int(cell["id"][-2:]))
                 for cell in vessel["fixed_load_cells"]]
        vessel["fixed_loads"] = [dict(kind="cargo", mass_kg=20000.0, x_m=boxes[0]["x_m"],
                                     y_m=boxes[0]["y_m"], z_m=sum(box["z_m"] for box in boxes) / 2,
                                     cargo_boxes=boxes)]
        # Add a hold-only target row under an existing cover.
        hold = next(stack for stack in vessel["stacks"] if not stack["on_deck"])
        vessel["stacks"].append(dict(hold, id="00103H", row=3, cell_ids=["0010302", "0010304"]))
        next(cover for cover in vessel["hatch_covers"] if cover["id"] == hold["hatch_cover_id"])["hold_stack_ids"].append("00103H")
        vessel["cells"].extend(dict(source_cells[cid], id=new, stack_id="00103H", tcg_m=3.9)
                               for cid, new in zip(hold["cell_ids"], ["0010302", "0010304"], strict=True))
        instance = Instance.model_validate(data)
        facts = prompt.prompt_facts(instance)
        assert facts["fixed_deck_stacks"] == [[[["D", "9", "1"]], 2, -1.3]]
        assert dict(facts["stack_geometry"])["00103H"] == 3.9
