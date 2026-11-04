"""Exact D25 class cues must survive the deliberately quantized mass channel."""

import json

import pytest

from llm.stowlm.format_prompt import instance_to_prompt, parse_prompt, prompt_facts
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import load_catalogue, rule_freeze_v1


@pytest.fixture(autouse=True)
def legacy_d25_regime():
    """D25 class cues are a legacy observation contract, superseded in v3."""
    with rule_freeze_v1(enabled=False):
        yield


def _instance(masses, overrides=None):
    data = build_instance().model_dump(mode="json")
    for box, mass in zip(data["containers"], masses, strict=True):
        box.update(gross_mass_kg=mass, weight_class="untrusted-stale-label")
    data["rule_overrides"] = overrides or {}
    return Instance.model_validate(data)


def _flags(instance):
    return [row[5] for row in parse_prompt(instance_to_prompt(instance))["cargo"]]


def test_prompt_preserves_both_default_d25_class_boundaries():
    instance = _instance([9999.999, 10000, 19999.999, 20000])
    facts = prompt_facts(instance)
    assert [row[3] for row in facts["cargo"]] == [10000, 10000, 20000, 20000]
    assert [flags["weight_class"] for flags in _flags(instance)] == ["L", "M", "M", "H"]
    assert all("e1_heavy" not in flags and "e6_heavy" not in flags for flags in _flags(instance))


@pytest.mark.parametrize("wrapped", [False, True])
def test_prompt_class_boundaries_follow_instance_overrides(wrapped):
    def value(number):
        return {"value": number} if wrapped else number

    instance = _instance([10999.999, 11000, 20999.999, 21000], {
        "expert.e2.weight_order": {"medium_min_kg": value(11000), "heavy_min_kg": value(21000)},
    })
    assert [flags["weight_class"] for flags in _flags(instance)] == ["L", "M", "M", "H"]
    # E1/E6 keep their independent catalogue threshold (20t), not the E2 override.
    assert [flags["e1_heavy"] for flags in _flags(instance)] == [False, False, True, True]
    assert [flags["e6_heavy"] for flags in _flags(instance)] == [False, False, True, True]


def test_prompt_class_boundaries_follow_active_catalogue(monkeypatch):
    catalogue = load_catalogue().model_dump()
    shared = catalogue["weight_classes"]["parameters"]
    shared["medium_min_kg"]["value"] = 11000
    shared["heavy_min_kg"]["value"] = 21000
    from stowage.rules.catalogue import Catalogue

    monkeypatch.setattr("stowage.rules.catalogue.load_catalogue", lambda: Catalogue.model_validate(catalogue))
    assert [flags["weight_class"] for flags in _flags(_instance([10999.999, 11000, 20999.999, 21000]))] == ["L", "M", "M", "H"]


def test_prompt_preserves_independent_e1_e6_thresholds():
    instance = _instance([10999.999, 11000, 20999.999, 21000], {
        "expert.e1.heavy_bottom": {"heavy_threshold": {"value": 11000}},
        "expert.e6.heavy_under_deck": {"heavy_threshold": 21000},
    })
    assert [flags["e1_heavy"] for flags in _flags(instance)] == [False, True, True, True]
    assert [flags["e6_heavy"] for flags in _flags(instance)] == [False, False, False, True]


@pytest.mark.parametrize("value", [None, True, -1, 0, 20000, 21000, "10000", {"value": None}])
def test_prompt_does_not_invent_class_for_invalid_boundary_override(value):
    instance = _instance([9999, 10000, 19999, 20000], {
        "expert.e2.weight_order": {"medium_min_kg": value},
    })
    assert [flags["weight_class"] for flags in _flags(instance)] == [None] * 4


@pytest.mark.parametrize("value,expected", [(None, None), (True, None), (-1, None), (0, True)])
def test_prompt_does_not_replace_invalid_or_zero_heavy_override_with_defaults(value, expected):
    instance = _instance([9999, 10000, 19999, 20000], {
        "expert.e1.heavy_bottom": {"heavy_threshold": value},
        "expert.e6.heavy_under_deck": {"heavy_threshold": {"value": value}},
    })
    flags = _flags(instance)
    assert [row["e1_heavy"] for row in flags] == [expected] * 4
    assert [row["e6_heavy"] for row in flags] == [expected] * 4


def test_prompt_is_deterministic_and_omits_csc_rating_and_cached_class():
    instance = _instance([9999, 10000, 19999, 20000])
    before = instance_to_prompt(instance)
    for box in instance.containers:
        box.max_gross_mass_kg = 123456
        box.weight_class = "PRIVATE_STALE_CLASS_SENTINEL"
    assert instance_to_prompt(instance) == before
    assert "123456" not in before
    assert "PRIVATE_STALE_CLASS_SENTINEL" not in before


def test_prompt_omits_d40_policy_and_vessel_source_audit_metadata():
    instance = _instance([9999, 10000, 19999, 20000])
    before = instance_to_prompt(instance)
    data = instance.model_dump(mode="json")
    hull = data["vessel"]["hull"]
    hull.update(min_trim_m=-0.7654321, min_displacement_kg=1234567,
                max_displacement_kg=2345678, bilge_keel_area_m2=12.34567)
    for name in ("min_trim_m", "min_displacement_kg", "max_displacement_kg", "bilge_keel_area_m2"):
        hull[f"{name}_provenance"] = {"assumption": "PRIVATE_POLICY_SENTINEL"}
    data["vessel"]["sources"].append("PRIVATE_BACKGROUND_AUDIT_SENTINEL")
    assert instance_to_prompt(Instance.model_validate(data)) == before


def test_prompt_v22_and_legacy_v20_read_compatibility():
    wire = json.loads(instance_to_prompt(_instance([9999, 10000, 19999, 20000])))
    assert wire["v"] == "stowlm-prompt@2.2"
    wire["v"] = "stowlm-prompt@2.0"
    wire["y"] = [[length, height, " ".join(f"{wire['z'][block][0]}:{item}"
                      for item in group.split())] for length, height, block, group in wire["y"]]
    # Original v2 payloads carried no explicit class cue.
    for category, _ in wire["c"]:
        if isinstance(category, list):
            category[-1].pop("w", None)
    legacy = parse_prompt(json.dumps(wire))
    assert legacy["format"] == "stowlm-prompt@2.0"
    assert all("weight_class" not in row[5] for row in legacy["cargo"])
