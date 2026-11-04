"""A8 keeps independent yard equipment and quay-crane height contracts."""

from hashlib import sha256
import json

import pytest

from stowage.rules.catalogue import load_catalogue, rule_freeze_v1, rule_freeze_v1_enabled


def test_a8_catalogue_does_not_apply_quay_height_to_yard_storage_or_crossing():
    catalogue = load_catalogue()
    yard = catalogue["port.e3.yard_height"]
    quay_crossing = catalogue["port.e3.quay_crossing"]
    yard_crossing = catalogue["port.e3.yard_crossing"]
    assert "min(H" not in yard.description
    assert "cannot exceed governing" not in yard.parameters["max_tiers"].provenance.assumption
    assert "H governs ship-side" in quay_crossing.description
    assert "yard_blocking_tiers(port, block)" in yard_crossing.description
    assert "H is not required for yard crossing" in yard_crossing.parameters["activation_path"].value
    assert "max_crane_height_tiers" in quay_crossing.parameters
    assert "berthing_side" in quay_crossing.parameters
    assert "truck_lane_side" not in quay_crossing.parameters
    assert "max_crane_height_tiers" not in yard_crossing.parameters
    assert "berthing_side" not in yard_crossing.parameters
    for field, path in (
        ("yard_max_tiers", "port.yard_max_tiers"),
        ("block_max_tiers", "port.yard.blocks.max_tiers"),
        ("equipment_stack_tiers", "port.equipment.stack_tiers"),
    ):
        assert yard_crossing.parameters[field].profile_field == path
        assert yard_crossing.parameters[field].unit == "tiers"
    assert all(any("A8" in ref for ref in rule.references) for rule in (yard, quay_crossing, yard_crossing))


@pytest.mark.parametrize("enabled", [False, True])
def test_crossing_metadata_ids_match_the_separate_quay_and_yard_contracts(enabled):
    with rule_freeze_v1(enabled=enabled):
        catalogue = load_catalogue()
        assert rule_freeze_v1_enabled() is enabled
        for obsolete in ("port.crane.max_height", "port.crane.crossing"):
            with pytest.raises(KeyError):
                catalogue[obsolete]
        for rule_id in ("port.e3.quay_crossing", "port.e3.yard_crossing"):
            rule = catalogue[rule_id]
            assert rule.hardness == "hard" and rule.scope == "port"
            assert set(rule.enforced_by) == {"checker", "feasibility_engine", "solver"}
            path = rule.parameters["activation_path"].value
            assert "activation.rule_freeze_v1" in path and "inactive" in path.lower()
            assert all(any(token in ref for ref in rule.references) for token in ("D62", "A2", "A8", "D69"))


def test_quay_height_retains_every_active_legacy_field_off_and_merges_h_only_on():
    with rule_freeze_v1(enabled=False):
        legacy = load_catalogue()["port.e3.quay_height"]
        active_fields = legacy.model_dump()
        active_fields.pop("pending_contract")
        encoded = json.dumps(active_fields, sort_keys=True, separators=(",", ":")).encode()
        assert sha256(encoded).hexdigest() == "7eb9f54ab1cf4071327d006e4fbda8eba8bc4afaa853819f6b79e925b3421062"
        assert "max_crane_height_tiers" not in legacy.parameters
        pending = legacy.pending_contract
        assert pending is not None and pending.hardness == "hard"
        assert pending.parameters["max_crane_height_tiers"].profile_field == "port.max_crane_height_tiers"
        assert pending.parameters["max_on_deck_tiers"] == legacy.parameters["max_on_deck_tiers"]
        assert "A8" in pending.description and "D62" in pending.description
    with rule_freeze_v1(enabled=True):
        active = load_catalogue()["port.e3.quay_height"]
        assert active.pending_contract is None
        assert active.parameters["max_crane_height_tiers"].profile_field == "port.max_crane_height_tiers"
        assert active.parameters["max_on_deck_tiers"] == legacy.parameters["max_on_deck_tiers"]
        assert "H >= 1" in active.description and "on-deck" in active.description
        assert active.references == legacy.references


def test_d69_quay_sequencing_and_yard_dig_out_remain_separate():
    catalogue = load_catalogue()
    quay = catalogue["port.e3.quay_crossing"].description
    yard = catalogue["port.e3.yard_crossing"].description
    for phrase in ("far rows before near rows", "brings a row to H", "farther row", "still needs a lift"):
        assert phrase in quay
    assert "dig-out" in yard and "counting each extra lift" in yard
    assert "quay H" in yard and "independent" in yard
