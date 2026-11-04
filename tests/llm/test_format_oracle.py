"""Independent observation oracles: expectations never call encoder helpers."""

from copy import deepcopy

import pytest

from llm.stowlm.format_prompt import instance_to_prompt, parse_prompt
from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1


@pytest.fixture(autouse=True)
def legacy_observation_regime():
    """These hand-written oracles pin archived 2.2 observations exactly."""
    with rule_freeze_v1(enabled=False):
        yield


def _observation_instance():
    data = build_instance(layover=True).model_dump(mode="json")
    ids = [box["id"] for box in data["containers"]]
    unrelated = deepcopy(data["containers"][2])
    unrelated["id"] = make_container_id(SYNTHETIC_OWNER_CODES[0], 7)
    data["containers"].append(unrelated)
    data["load_list"] = ids[:3]
    data["yard"]["stacks"] = [
        {"key": "A-001-01", "length_ft": 40, "boxes": [ids[0]]},
        {"key": "B-002-02", "length_ft": 20, "boxes": [ids[1], ids[3]]},
        {"key": "A-004-02", "length_ft": 20, "boxes": [unrelated["id"]]},
    ]
    data["yard"]["quay_buffer"] = [ids[2]]
    data["containers"][1]["dg"]["segregation_codes"] = ["SG35"]
    data["containers"][2]["special_stowage"] = ["top_stowage", "on_deck", "away_from_heat"]
    data["containers"][3]["oog"].update(over_width_port_m=0.2, over_length_fore_m=0.3)
    data["onboard"]["covers_off"] = {"HC-02-2": "quay"}
    data["rotation"]["calls"][1]["max_on_deck_tiers"] = None
    data["rotation"]["calls"][2]["max_on_deck_tiers"] = 1
    data["port"]["quay_crane"]["max_on_deck_tiers"] = 3
    data["port"]["port_rules"] = [{"rule_id": "expert.e3", "params": {"max_tiers": 3}}]
    data["plan_constraints"] = [{"kind": "reefer_bays", "params": {"bays": [1, 3]},
                                 "hardness": "hard", "source_text": "Use bays 1 and 3"}]
    data["rule_overrides"] = {"stability.gm": {"min_gm_m": 0.4}}
    cell = next(c for c in data["vessel"]["cells"] if c["id"] == "0030284")
    cell.update(accepts_45=False, reefer_plug=False)
    return Instance.model_validate(data)


def test_manually_specified_complete_observation():
    """Every public fact is pinned without reusing _wire, _facts or prompt_facts."""
    actual = parse_prompt(instance_to_prompt(_observation_instance()))
    assert actual == {
        "format": "stowlm-prompt@2.2", "instance": "fixture_layover", "stage": "B", "call": 0,
        "vessel": {"id": "synthetic_feeder.slice_b01-b03", "slice_of": "synthetic_feeder"},
        "rotation": [["THLCH", 2], ["THBKK", None], ["THSGZ", 1]],
        "load": [0, 1, 2], "discharge": [4],
        "cargo": [
            [0, 40, 2896, 20000, 1, {"reefer": True, "weight_class": "H"}],
            [1, 20, 2591, 12000, 2, {"dg": {"imdg_class": "3", "segregation_codes": ["SG35"]}, "weight_class": "M"}],
            [2, 20, 2591, 12000, 1, {"special_stowage": ["away_from_heat", "on_deck", "top_stowage"], "weight_class": "M"}],
            [3, 20, 2591, 12000, 2, {"oog": "HWL", "weight_class": "M"}],
            [4, 20, 2591, 12000, 0, {"weight_class": "M"}],
            [5, 20, 2591, 12000, 1, {"weight_class": "M"}],
        ],
        "yard": [["A-001-01", 40, 1, [0]], ["B-002-02", 20, 2, [3, 1]]],
        "anonymous_occupancy": [["A", 20, 1, 1]],
        "ship": {"placements": [["0010102", 4], ["0010104", 5]], "covers_off": {"HC-02-2": "quay"}},
        "quay": [2], "port": ["THLCH", 3, 3],
        "constraints": {
            "instructions": [{"kind": "reefer_bays", "params": {"bays": [1, 3]},
                              "hardness": "hard", "source_text": "Use bays 1 and 3"}],
            "rule_overrides": {"stability.gm": {"min_gm_m": 0.4}},
            "port_rules": [{"rule_id": "expert.e3", "params": {"max_tiers": 3}}],
        },
        "layout": {
            "groups": [
                [[["H", "1/3", "1-2"]], [2, 4], 7],
                [[["D", "3", "1"], ["D", "1", "1-2"]], [82, 84], 15],
                [[["D", "3", "2"]], [82, 84], [15, 3]],
            ],
            "covers": [
                ["HC-02-1", [["H", "1/3", "1"]], [["D", "1/3", "1"]]],
                ["HC-02-2", [["H", "1/3", "2"]], [["D", "1/3", "2"]]],
            ],
        },
    }


def test_layout_exclusion_and_reordered_input_are_deterministic():
    instance = _observation_instance()
    data = instance.model_dump(mode="json")
    for field in ("containers", "load_list", "discharge_list"):
        data[field].reverse()
    for field in ("cells", "stacks", "hatch_covers"):
        data["vessel"][field].reverse()
    data["yard"]["stacks"].reverse()
    data["port"]["yard"]["blocks"].reverse()
    assert instance_to_prompt(Instance.model_validate(data)) == instance_to_prompt(instance)
    minimal = parse_prompt(instance_to_prompt(instance, include_layout=False))
    complete = parse_prompt(instance_to_prompt(instance))
    assert "layout" not in minimal
    complete.pop("layout")
    assert minimal == complete


@pytest.mark.parametrize("mass,bucket,weight_class", [(2000, 2000, "L"), (2000.001, 4000, "L"),
                                                        (52000, 52000, "H"), (52000.001, 54000, "H")])
def test_compact_and_explicit_cargo_mass_boundaries(mass, bucket, weight_class):
    data = build_instance().model_dump(mode="json")
    data["containers"][2]["gross_mass_kg"] = mass
    data["containers"][2]["tare_kg"] = 1000
    actual = parse_prompt(instance_to_prompt(Instance.model_validate(data)))
    assert actual["cargo"][2] == [2, 20, 2591, bucket, 1,
                                   {"special_stowage": ["away_from_heat", "under_deck"], "weight_class": weight_class}]


def test_single_block_ordinals_and_late_pod_call_boundary():
    data = build_instance().model_dump(mode="json")
    data["port"]["yard"]["blocks"] = [data["port"]["yard"]["blocks"][0]]
    next(stack for stack in data["yard"]["stacks"] if stack["key"] == "B-001-01")["key"] = "A-002-02"
    data["rotation"]["calls"] = [dict(locode="THLCH", index=i, max_on_deck_tiers=2)
                                     for i in range(26)] + [
        dict(locode="THBKK", index=26, max_on_deck_tiers=1),
        dict(locode="THSGZ", index=27, max_on_deck_tiers=None),
    ]
    actual = parse_prompt(instance_to_prompt(Instance.model_validate(data)))
    assert actual["yard"] == [["A-001-01", 40, 1, [0]], ["A-002-02", 20, 1, [1]],
                               ["A-003-01", 20, 1, [2]], ["A-004-01", 20, 1, [3]]]
    assert [row[4] for row in actual["cargo"]] == [26, 27, 26, 27]
    assert actual["rotation"][-2:] == [["THBKK", 1], ["THSGZ", None]]


def test_hidden_csc_policy_booking_and_shipment_fields_do_not_leak():
    instance = _observation_instance()
    data = instance.model_dump(mode="json")
    data["family"]["booking_policy"] = "hidden-booking-policy"
    data["family"]["region_capacity_teu"] = "123456"
    data["vessel"]["hull"].update(min_trim_m=-0.75, max_trim_m=0.75,
                                      min_displacement_kg=6000000, max_displacement_kg=12000000,
                                      bilge_keel_area_m2=42)
    for box in data["containers"]:
        box["max_gross_mass_kg"] = 32000
    data["containers"][0]["reefer_setpoint_c"] = -23
    data["containers"][1]["dg"].update(un_number="1993", flashpoint_c=25,
                                           protected_from_heat=True)
    changed = Instance.model_validate(data)
    text = instance_to_prompt(changed)
    assert text == instance_to_prompt(instance)
    assert not any(box.id in text for box in changed.containers)
    assert all(name not in text for name in ("max_gross_mass_kg", "bilge_keel_area_m2",
                                             "booking_policy", "protected_from_heat", "1993"))


def test_anonymous_occupancy_intentionally_omits_relocation_destination():
    """Witness the documented observation loss; checker/expander retain exact yards."""
    from stowage.actions.expander import Expander

    data = build_instance().model_dump(mode="json")
    ids = [box["id"] for box in data["containers"]]
    data["load_list"] = ids[:2]
    data["yard"]["stacks"] = [
        {"key": "A-001-01", "length_ft": 40, "boxes": [ids[0]]},
        {"key": "B-001-01", "length_ft": 20, "boxes": [ids[1]]},
        {"key": "A-003-01", "length_ft": 20, "boxes": [ids[2]]},
    ]
    left = Instance.model_validate(data)
    data["yard"]["stacks"][2]["key"] = "A-005-02"
    right = Instance.model_validate(data)
    assert instance_to_prompt(left) == instance_to_prompt(right)
    assert Expander(left).yard_target(ids[3], None) == "A-003-01"
    assert Expander(right).yard_target(ids[3], None) == "A-005-02"


def test_d42_background_boxes_stay_outside_decision_observation():
    data = build_instance().model_dump(mode="json")
    vessel = data["vessel"]
    # Reserve row 1 as non-decision D42 geometry, leaving row 2 for actions.
    for field in ("cells", "stacks"):
        vessel[f"fixed_load_{field}"] = [item for item in vessel[field] if item["id"][3:5] == "01"]
        vessel[field] = [item for item in vessel[field] if item["id"][3:5] == "02"]
    vessel["fixed_load_hatch_covers"] = [item for item in vessel["hatch_covers"] if item["id"] == "HC-02-1"]
    vessel["hatch_covers"] = [item for item in vessel["hatch_covers"] if item["id"] == "HC-02-2"]
    background = {
        "cell_ids": ["0010102"], "length_ft": 20, "height_m": 2.591,
        "gross_mass_kg": 10000.0, "x_m": 46.9, "y_m": -1.3, "z_m": 3.2955,
        "stack_id": "00101H", "tier": 2,
        "provenance": {"created_by": "format.background.oracle"},
    }
    vessel["fixed_loads"] = [{"kind": "cargo", "mass_kg": 10000.0,
                              "x_m": 46.9, "y_m": -1.3, "z_m": 3.2955,
                              "cargo_boxes": [background]}]
    before = Instance.model_validate(data)
    vessel["fixed_loads"][0]["mass_kg"] = 18000.0
    vessel["fixed_loads"][0]["cargo_boxes"][0]["gross_mass_kg"] = 18000.0
    after = Instance.model_validate(data)
    assert len(before.vessel.fixed_loads[0].cargo_boxes) == 1
    assert instance_to_prompt(before) == instance_to_prompt(after)
    observation = parse_prompt(instance_to_prompt(after))
    assert [row[0] for row in observation["cargo"]] == [0, 1, 2, 3]
    assert observation["layout"] == {
        "groups": [[[["H", "1/3", "2"]], [2, 4], 7],
                   [[["D", "1/3", "2"]], [82, 84], 15]],
        "covers": [["HC-02-2", [["H", "1/3", "2"]], [["D", "1/3", "2"]]]],
    }
