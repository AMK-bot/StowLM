"""D66 labelled synthetic 1.4S demand is activated alongside port rules."""
from copy import deepcopy

import pytest

from stowage.generate.cargo import _params, generate_load_list
from stowage.params import load_param_file
from stowage.rules.catalogue import rule_freeze_v1
from stowage.rules.imdg.dgl import entries_for_un


def test_active_profile_adds_rare_1_4s_without_mutating_shared_params():
    raw = load_param_file("cargo", "feeder_export")
    before = deepcopy(raw)
    with rule_freeze_v1(enabled=True):
        effective = _params(raw)
        assert _params(effective) == effective
    assert raw == before
    classes = {row["id"]: row for row in effective["dg"]["classes"]}
    assert classes["1.4"]["share"]["value"] == 0.01
    assert classes["3"]["share"]["value"] == 0.295
    choices = {row["un_number"]: row for row in classes["1.4"]["un_numbers"]}
    assert {un: row["share"]["value"] for un, row in choices.items()} == {"0336": 0.5, "0012": 0.5}
    assert classes["1.4"]["share"]["value"] * choices["0012"]["share"]["value"] == 0.005
    assert sum(row["share"]["value"] for row in classes.values()) == pytest.approx(1)
    assert "synthetic" in choices["0012"]["share"]["assumption"].lower()
    entry, = entries_for_un(12)
    assert (entry.id, entry.hazard_class, entry.compatibility_group) == ("UN0012-none", "1.4", "S")
    assert "581" in choices["0012"]["classification_source_locator"]
    assert "0012" in choices["0012"]["classification_source_locator"]


def test_effective_profile_preserves_custom_choices_and_omitted_classes():
    raw = load_param_file("cargo", "feeder_export")
    raw["dg"]["classes"] = [row for row in raw["dg"]["classes"] if row["id"] == "3"]
    with rule_freeze_v1(enabled=True):
        effective = _params(raw)
        assert [row["id"] for row in effective["dg"]["classes"]] == ["3"]
        effective["dg"]["classes"][0]["share"]["value"] = 1
        assert _params(effective)["dg"]["classes"][0]["share"]["value"] == 1
    assert raw["dg"]["classes"][0]["share"]["value"] == 0.3


def test_off_profile_preserves_legacy_class_and_un_shares():
    raw = load_param_file("cargo", "feeder_export")
    with rule_freeze_v1(enabled=False):
        effective = _params(raw)
    assert effective is raw
    classes = {row["id"]: row for row in effective["dg"]["classes"]}
    assert classes["1.4"]["share"]["value"] == 0.005
    assert classes["3"]["share"]["value"] == 0.3
    assert [(row["un_number"], row["share"]["value"]) for row in classes["1.4"]["un_numbers"]] == [("0336", 1)]


def test_seeded_raw_1_4s_is_direct_delivery_and_compatible_with_fleet_doc():
    with rule_freeze_v1(enabled=True):
        boxes = generate_load_list("straits", 0, 1200, seed=731)
    boxes_s = [box for box in boxes if box.dg and box.dg.imdg_class == "1.4S"]
    assert boxes_s, "The unmodified active profile must generate its new 1.4S support"
    for box in boxes_s:
        assert box.dg.un_number == "0012"
        assert box.direct_delivery
        # This checks declared DoC compatibility, not a full checker outcome.
        for vessel_id in ("feeder_500", "feeder_800", "feeder_1700"):
            document = load_param_file("fleet", vessel_id)["dg_document"]
            compatible = [area for area in document["areas"]
                          if box.dg.imdg_class in area["classes_allowed"]
                          and box.dg.packing_form.value in area["packing_forms_allowed"]]
            assert compatible, vessel_id
            assert all(area["kind"] == "deck" for area in compatible)
