"""Legacy D25 consumers stay compatible while D61 metadata awaits migration."""

import math

import pytest
from pydantic import ValidationError

from stowage.rules.catalogue import load_catalogue


E1 = "expert.e1.heavy_bottom"
E2 = "expert.e2.weight_order"
E6 = "expert.e6.heavy_under_deck"


@pytest.fixture
def catalogue():
    return load_catalogue()


def test_weight_class_block_has_traceable_single_source_defaults(catalogue):
    block = catalogue.weight_classes
    assert block.status == "provisional"
    assert set(block.open_question) == {2, 4, 11}
    expected = {
        "labels": (["L", "M", "H"], "1"),
        "medium_min_kg": (10_000, "kg"),
        "heavy_min_kg": (20_000, "kg"),
    }
    for name, (value, unit) in expected.items():
        parameter = block.parameters[name]
        assert parameter.value == value
        assert parameter.unit == unit
        assert parameter.provenance.assumption


@pytest.mark.parametrize("mass,expected", [
    (0, "L"), (9_999.999, "L"), (10_000, "M"),
    (19_999.999, "M"), (20_000, "H"), (40_000, "H"),
])
def test_weight_class_boundary_inclusivity(catalogue, mass, expected):
    assert catalogue.weight_classes.classify(mass) == expected


@pytest.mark.parametrize("mass", [-1, math.nan, math.inf, -math.inf])
def test_classification_rejects_invalid_masses(catalogue, mass):
    with pytest.raises(ValueError):
        catalogue.weight_classes.classify(mass)


def test_staged_d61_ship_weight_order_preserves_active_d25_parameters(catalogue):
    for rule_id in (E1, E2):
        rule = catalogue[rule_id]
        assert rule.hardness == "tolerated_if_inherited"
        assert rule.is_hard(stage="A", inherited=True)
        assert rule.is_hard(stage="B", inherited=False)
        assert not rule.is_hard(stage="B", inherited=True)
        assert rule.pending_contract.parameters["weight_order_mode"].value == "gross_mass_kg"
        tolerance = rule.pending_contract.parameters["weight_order_tolerance_kg"]
        assert tolerance.value == 0 and tolerance.unit == "kg"
        assert "Q24" in tolerance.provenance.assumption
        assert "activation.rule_freeze_v1" in rule.pending_contract.activation
        assert "each" in rule.pending_contract.description and "Equal masses" in rule.pending_contract.description


def test_e6_stages_level_four_preference_without_activating_it(catalogue):
    rule = catalogue[E6]
    assert rule.hardness == "hard"
    assert rule.is_hard(stage="A") and rule.is_hard(stage="B", inherited=True)
    assert set(rule.enforced_by) == {"checker", "feasibility_engine", "linear_model", "solver"}
    assert rule.penalty is None
    pending = rule.pending_contract
    assert pending.hardness == "soft" and pending.enforced_by == ["solver"]
    assert pending.penalty.priority == 4
    assert pending.penalty.metric == "heavy_on_deck_count"
    assert "activation.rule_freeze_v1" in pending.activation
    threshold = pending.parameters["heavy_on_deck_threshold_kg"]
    assert threshold.value == 20_000 and threshold.unit == "kg"
    assert threshold.shared_parameter is None
    assert "provisional" in threshold.provenance.assumption
    assert rule.parameters["heavy_threshold"].shared_parameter == "heavy_min_kg"
    assert rule.parameters["heavy_threshold"].superseded_by == "pending_contract.parameters"
    assert rule.parameters["allow_on_deck_if_no_compatible_hold_cell"].value is True


def test_all_legacy_ship_weight_parameters_remain_available(catalogue):
    assert catalogue[E1].parameters["heavy_threshold"].value == 20_000
    assert catalogue[E1].parameters["heavy_threshold"].shared_parameter == "heavy_min_kg"
    parameters = catalogue[E2].parameters
    assert parameters["order_mode"].value == "weight_class"
    assert parameters["class_order"].value == ["L", "M", "H"]
    assert parameters["class_order"].shared_parameter == "labels"
    assert parameters["tolerance"].value == 0 and parameters["tolerance"].unit == "classes"
    for name, value in [("medium_min_kg", 10_000), ("heavy_min_kg", 20_000)]:
        assert parameters[name].shared_parameter == name
        assert parameters[name].value == value
    for name in ("order_mode", "class_order", "tolerance", "medium_min_kg", "heavy_min_kg"):
        assert parameters[name].superseded_by == "pending_contract.parameters"


def test_shared_boundary_still_updates_legacy_but_not_new_d61_parameters(catalogue):
    data = catalogue.model_dump()
    data["weight_classes"]["parameters"]["heavy_min_kg"]["value"] = 21_000
    changed = type(catalogue).model_validate(data)
    for rule_id in (E1, E6):
        assert changed[rule_id].parameters["heavy_threshold"].value == 21_000
    assert changed[E2].parameters["heavy_min_kg"].value == 21_000
    assert changed[E6].pending_contract.parameters["heavy_on_deck_threshold_kg"].value == 20_000
    for rule_id in (E1, E2):
        assert changed[rule_id].pending_contract.parameters["weight_order_tolerance_kg"].value == 0
        assert changed[rule_id].pending_contract.parameters["weight_order_mode"].value == "gross_mass_kg"
    assert changed.weight_classes.classify(20_000) == "M"
    assert changed.weight_classes.classify(21_000) == "H"


def test_rule_override_precedes_catalogue_default(catalogue):
    overrides = {E6: {"heavy_threshold": 25_000}}
    assert catalogue.resolve_parameter(E6, "heavy_threshold", overrides) == 25_000
    assert catalogue.resolve_parameter(E2, "order_mode", overrides) == "weight_class"
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=True):
        migrated = load_catalogue()
        overrides = {E2: {"weight_order_tolerance_kg": 500}}
        assert migrated.resolve_parameter(E2, "weight_order_tolerance_kg", overrides) == 500
        assert migrated.resolve_parameter(E1, "weight_order_tolerance_kg", overrides) == 0
        assert migrated.resolve_parameter(E2, "weight_order_mode", overrides) == "gross_mass_kg"


def test_wrapped_override_is_supported_without_silent_invalid_fallback(catalogue):
    assert catalogue.resolve_parameter(E6, "heavy_threshold", {
        E6: {"heavy_threshold": {"value": 26_000}},
    }) == 26_000
    # Consumers validate policy values; explicit invalid overrides remain visible.
    for value in (None, -1, "invalid"):
        assert catalogue.resolve_parameter(E6, "heavy_threshold", {
            E6: {"heavy_threshold": value},
        }) == value


@pytest.mark.parametrize("medium,heavy", [
    (20_000, 10_000), (20_000, 20_000), (0, 20_000), (-1, 20_000),
    ("10000", 20_000), (True, 20_000), (10_000, False),
    (math.nan, 20_000), (10_000, math.inf),
])
def test_malformed_shared_boundaries_are_rejected(catalogue, medium, heavy):
    data = catalogue.model_dump()
    parameters = data["weight_classes"]["parameters"]
    parameters["medium_min_kg"]["value"] = medium
    parameters["heavy_min_kg"]["value"] = heavy
    with pytest.raises(ValidationError):
        type(catalogue).model_validate(data)


@pytest.mark.parametrize("field,value", [
    ("shared_parameter", "misspelled_boundary"), ("unit", "m"),
])
def test_broken_shared_reference_is_rejected(catalogue, field, value):
    data = catalogue.model_dump()
    rule = next(rule for rule in data["rules"] if rule["id"] == E6)
    rule["parameters"]["heavy_threshold"]["shared_parameter"] = "heavy_min_kg"
    rule["parameters"]["heavy_threshold"][field] = value
    with pytest.raises(ValidationError):
        type(catalogue).model_validate(data)
