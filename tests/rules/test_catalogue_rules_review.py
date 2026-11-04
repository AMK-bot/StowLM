"""Shared C1 override metadata and dimension-specific C4 pending contracts."""

import pytest

from stowage.rules.catalogue import load_catalogue, rule_freeze_v1


SHARED_TOLERANCE = (
    "One shared tolerance governs E1 and E2. Set "
    "instance.rule_overrides['expert.e2.weight_order']['weight_order_tolerance_kg']. "
    "An E1 override requires an equal explicit E2 override; otherwise fail closed. "
    "The default is 0 kg."
)


@pytest.mark.parametrize("enabled", [False, True])
def test_e1_e2_provenance_documents_one_authoritative_tolerance(enabled):
    with rule_freeze_v1(enabled=enabled):
        catalogue = load_catalogue()
        tolerances = []
        for rule_id in ("expert.e1.heavy_bottom", "expert.e2.weight_order"):
            rule = catalogue[rule_id]
            parameters = rule.parameters if enabled else rule.pending_contract.parameters
            parameter = parameters["weight_order_tolerance_kg"]
            assert parameter.value == 0 and parameter.unit == "kg"
            assert SHARED_TOLERANCE in parameter.provenance.assumption
            assert "instance.rule_overrides[rule.id]" not in parameter.provenance.assumption
            tolerances.append(parameter)
        assert tolerances[0] == tolerances[1]


@pytest.mark.parametrize("enabled", [False, True])
def test_e9_width_length_contract_describes_its_own_dimensions_and_broken_space(enabled):
    with rule_freeze_v1(enabled=enabled):
        catalogue = load_catalogue()
        rules = [catalogue[f"expert.e9.{suffix}"] for suffix in ("over_height", "over_width_length")]
        height, lateral = [rule if enabled else rule.pending_contract for rule in rules]
        assert height.title != lateral.title
        assert "over-width" in lateral.description and "over-length" in lateral.description
        assert "on deck or under deck" in lateral.description
        assert "adjacent" in lateral.description and "envelope" in lateral.description
        assert "vertical_clearance" not in lateral.parameters
        for contract in (height, lateral):
            assert "killed" in contract.description and "TEU" in contract.description
            assert "above" in contract.description and "level-4" in contract.description
            assert "broken space" in contract.parameters["tie_break_metric"].provenance.assumption
