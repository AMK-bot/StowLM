"""D32's provisional heat and OOG defaults remain traceable and overridable."""

from copy import deepcopy

import pytest

from stowage.rules.catalogue import load_catalogue


HEAT = "expert.e8.away_from_heat"
OOG = "expert.e9.over_width_length"
DEFAULTS = [(HEAT, "minimum_clearance", 2.4, 12), (OOG, "oog_clearance", 0.0, 13)]


@pytest.fixture
def catalogue():
    return load_catalogue()


@pytest.mark.parametrize("rule_id,name,value,question", DEFAULTS)
def test_d32_defaults_are_numeric_metres_and_remain_provisional_hard_rules(
    catalogue, rule_id, name, value, question,
):
    rule = catalogue[rule_id]
    parameter = rule.parameters[name]
    assert parameter.value == value
    assert type(parameter.value) is float
    assert parameter.unit == "m"
    assert parameter.profile_field is None
    assert "D32" in parameter.provenance.assumption
    assert f"Q{question}" in parameter.provenance.assumption
    assert rule.status == "provisional"
    assert question in rule.open_question
    assert rule.hardness == "hard"
    assert rule.is_hard(stage="B", inherited=True)
    assert catalogue.resolve_parameter(rule_id, name) == value


def test_heat_clearance_records_imdg_source(catalogue):
    provenance = catalogue[HEAT].parameters["minimum_clearance"].provenance
    assert "42-24" in provenance.document
    assert "7.1.2" in provenance.clause


@pytest.mark.parametrize("rule_id,name,default,question", DEFAULTS)
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("override", [0.0, 1.25])
def test_d32_instance_override_wins_without_mutating_defaults_or_inputs(
    catalogue, rule_id, name, default, question, wrapped, override,
):
    value = {"value": override} if wrapped else override
    overrides = {rule_id: {name: value}}
    original_overrides = deepcopy(overrides)
    original_catalogue = catalogue.model_dump()
    assert catalogue.resolve_parameter(rule_id, name, overrides) == override
    assert overrides == original_overrides
    assert catalogue.model_dump() == original_catalogue
    assert catalogue.resolve_parameter(rule_id, name) == default


@pytest.mark.parametrize("rule_id,name,default,question", DEFAULTS)
def test_d32_unrelated_override_does_not_hide_default(catalogue, rule_id, name, default, question):
    overrides = {rule_id: {"unrelated_parameter": 9.0}, "other.rule": {name: 8.0}}
    assert catalogue.resolve_parameter(rule_id, name, overrides) == default
