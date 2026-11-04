"""Catalogue contracts, including every row of docs/domain.md's constraint table."""

from copy import deepcopy
from pathlib import Path
import tomllib
import types
from typing import Annotated, Union, get_args, get_origin

import pytest
from pydantic import BaseModel, ValidationError

from stowage.rules.catalogue import load_catalogue


# Deliberately hard-coded: changing the catalogue must not silently shrink coverage.
DOMAIN_ROWS = [
    "One container per slot; 20ft vs 40ft slot compatibility; 20s not on top of 40s **(verify)**",
    "Stack weight limits per stack; tier height limits (incl. high cubes)",
    "Reefers only in slots with plugs",
    "Earlier-port discharges must be physically accessible, directly or through explicitly planned, legal and counted restows",
    'IMDG segregation between classes (4 levels: "away from", "separated from", "separated by a complete compartment or hold from", "separated longitudinally by an intervening complete compartment or hold from")',
    "Stowage categories and special provisions (e.g. on deck only, away from living quarters, protected from heat)",
    "Displacement/draft within limits; GM ≥ minimum after free-surface correction",
    "IMO 2008 Intact Stability Code GZ-curve criteria: area to 30° ≥ 0.055 m·rad, to 40° ≥ 0.09, 30–40° ≥ 0.03; GZ ≥ 0.20 m at ≥ 30°; max GZ at ≥ 25°; initial GM ≥ 0.15 m **(verify)**",
    "Trim and list within limits",
    "Shear force and bending moment along the hull within limits at each station",
    "SOLAS V/22: view of the sea surface from the conning position not obscured by more than 2 ship lengths or 500 m, whichever is less **(verify)**",
    "Lashing forces",
    "Heavy containers under deck, for stability (E6)",
    "Weight order in a stack: heavy at the bottom, light on top; the stack total within its stacking-weight limit (E1, E2, E7)",
    "Special stowage requests per box: under deck, on deck, away from heat, top stowage (E8)",
    "Out-of-gauge (OOG) cargo (E9)",
    "Height limit per port, e.g. only 2 or 3 high (E3)",
    "Physical access: only the top box is reachable; below deck needs the hatch cover clear; ship stacks fill bottom-up",
]


@pytest.fixture
def catalogue():
    return load_catalogue()


def test_catalogue_loads_unique_complete_entries(catalogue):
    assert catalogue.rules
    ids = [rule.id for rule in catalogue.rules]
    assert len(ids) == len(set(ids))
    for rule in catalogue.rules:
        assert rule.title and rule.source and rule.scope and rule.hardness
        assert rule.enforced_by and rule.references
        assert rule.status == "provisional"
        for parameter in rule.parameters.values():
            assert parameter.unit.strip()
            provenance = parameter.provenance
            assert provenance.assumption or (
                provenance.document and (provenance.clause or provenance.page)
            )
            assert (parameter.value is not None) != (parameter.profile_field is not None)


@pytest.mark.parametrize("row", DOMAIN_ROWS)
def test_every_domain_constraint_row_maps_to_rules(catalogue, row):
    assert any(row in rule.domain_rows for rule in catalogue.rules), row


def test_parameter_values_are_si_and_profile_limits_are_references(catalogue):
    forbidden_units = {"deg", "degree", "degrees", "t", "tonne", "kN", "kNm", "mm"}
    for rule in catalogue.rules:
        for parameter in rule.parameters.values():
            assert parameter.unit not in forbidden_units
            if parameter.profile_field:
                assert parameter.value is None


def test_runtime_soft_rules_remain_d11_margins_until_e6_migration(catalogue):
    soft = [rule for rule in catalogue.rules if rule.hardness == "soft"]
    assert {rule.id for rule in soft} == {
        "iscode.stability.margin", "iacs.strength.margin",
    }
    for rule in soft:
        assert rule.penalty.kind == "tie_break"
        assert rule.penalty.priority == 4
    inherited = [rule for rule in catalogue.rules if rule.hardness == "tolerated_if_inherited"]
    assert inherited
    assert all(
        rule.source == "expert" and set(rule.expert_rules) <= {"E1", "E2", "E7"}
        for rule in inherited
    )


def test_lookup_and_stage_b_semantics(catalogue):
    from stowage.rules.catalogue import get_rule

    for rule in catalogue:
        assert catalogue[rule.id] is rule
        assert get_rule(rule.id, catalogue) is rule
        if rule.hardness == "tolerated_if_inherited":
            assert rule.is_hard(stage="A", inherited=True)
            assert rule.is_hard(stage="B", inherited=False)
            assert not rule.is_hard(stage="B", inherited=True)
        elif rule.hardness == "hard":
            assert rule.is_hard(stage="B", inherited=True)
    with pytest.raises(KeyError):
        catalogue.get_rule("missing.rule")
    for row in DOMAIN_ROWS:
        assert catalogue.for_domain_row(row)


@pytest.mark.parametrize("rule_id,owners", [
    ("checker.internal_error", {"checker"}),
    ("checker.missing_vessel_data", {"checker"}),
    ("checker.invalid_ballast", {"checker"}),
    ("port.call.profile_resolution", {"checker", "feasibility_engine", "solver"}),
])
def test_project_diagnostics_fail_closed_without_claiming_physical_infeasibility(catalogue, rule_id, owners):
    rule = catalogue[rule_id]
    assert rule.source == "user specification (D62)"
    assert rule.hardness == "hard" and rule.status == "provisional"
    assert set(rule.enforced_by) == owners
    assert rule.parameters["classification"].value == "diagnostic"
    assert rule.parameters["physical_violation"].value is False
    assert "undecided" in rule.description.lower()
    assert "physical infeasibility" in rule.description.lower()
    assert any("D68" in reference for reference in rule.references)
    if rule.scope == "port":
        assert rule.parameters["activation_path"].value


def test_required_load_contract_is_a_plan_level_requirement_not_a_prefix_physics_claim(catalogue):
    rule = catalogue["objective.all_loaded"]
    assert rule.source == "user specification (D62)" and rule.scope == "global"
    assert rule.hardness == "hard" and rule.status == "provisional"
    assert set(rule.enforced_by) == {"checker", "solver"}
    assert rule.parameters["classification"].value == "plan_contract"
    assert rule.parameters["evaluation_level"].value == "plan"
    assert rule.parameters["physical_violation"].value is False
    assert rule.parameters["required_load_list"].profile_field == "instance.load_list"
    assert any("D11" in reference for reference in rule.references)
    assert "prefix" in rule.description.lower()


def test_expert_rules_are_present_with_written_answer_sources(catalogue):
    expected_questions = {
        "E1": 2, "E2": 2, "E3": 3, "E6": 11,
        "E7": 2, "E8": 12, "E9": 13,
    }
    covered = {expert for rule in catalogue for expert in rule.expert_rules}
    assert {*expected_questions, "E11"} <= covered
    for expert, question in expected_questions.items():
        rules = [rule for rule in catalogue if expert in rule.expert_rules]
        assert any("28 Oct 2026" in reference for rule in rules for reference in rule.references) or any(
            question in rule.open_question for rule in rules)
        assert all(rule.status == "provisional" for rule in rules)


@pytest.mark.parametrize("parameter", [
    {"value": 1, "unit": "kg", "provenance": {}},
    {"unit": "kg", "provenance": {"assumption": "Test"}},
    {"value": 1, "profile_field": "vessel.limit", "unit": "kg", "provenance": {"assumption": "Test"}},
    {"value": [True, "yes"], "unit": "1", "provenance": {"assumption": "Test"}},
])
def test_untraceable_or_ambiguous_parameters_are_rejected(parameter):
    from stowage.rules.catalogue import Parameter

    with pytest.raises(ValidationError):
        Parameter.model_validate(parameter)


def test_duplicate_ids_are_rejected(catalogue):
    data = catalogue.model_dump()
    data["rules"].append(deepcopy(data["rules"][0]))
    with pytest.raises(ValidationError):
        type(catalogue).model_validate(data)


@pytest.mark.parametrize("field", ["id", "source", "scope", "hardness", "status", "enforced_by", "parameters"])
def test_required_rule_fields_cannot_be_omitted(catalogue, field):
    data = catalogue.model_dump()
    del data["rules"][0][field]
    with pytest.raises(ValidationError):
        type(catalogue).model_validate(data)


@pytest.mark.parametrize("field", ["unit", "provenance"])
def test_required_parameter_fields_cannot_be_omitted(catalogue, field):
    data = catalogue.model_dump()
    rule = next(rule for rule in data["rules"] if rule["parameters"])
    parameter = next(iter(rule["parameters"].values()))
    del parameter[field]
    with pytest.raises(ValidationError):
        type(catalogue).model_validate(data)


def test_loader_rejects_invalid_toml(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("[[invalid", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_catalogue(path)


def test_loader_honours_explicit_path(tmp_path, catalogue):
    from stowage.rules import catalogue as module

    source = Path(module.__file__).with_suffix(".toml")
    target = tmp_path / "catalogue.toml"
    target.write_bytes(source.read_bytes())
    assert load_catalogue(target) == catalogue


def _resolve_model_path(path):
    """Resolve fields, projecting collections and unwrapping optional annotations.

    Dictionaries may be endpoints (rule_overrides), but arbitrary dictionary keys
    are not model fields and cannot make a misspelled field path pass this test.
    """
    from stowage.model.instance import Instance
    from stowage.model.port import PortProfile
    from stowage.model.vessel import Vessel

    root, *parts = path.split(".")
    current = {"vessel": Vessel, "port": PortProfile, "instance": Instance}[root]
    for part in parts:
        while True:
            origin = get_origin(current)
            if origin is Annotated or origin is list:
                current = get_args(current)[0]
            elif origin in (Union, types.UnionType):
                options = [option for option in get_args(current) if option is not type(None)]
                assert len(options) == 1, f"Ambiguous model annotation in {path}"
                current = options[0]
            else:
                break
        assert isinstance(current, type) and issubclass(current, BaseModel), path
        assert part in current.model_fields, f"Unknown field {part!r} in {path}"
        current = current.model_fields[part].annotation
    return current


def test_every_catalogue_profile_path_exists_in_frozen_models(catalogue):
    rule_ids = {rule.id for rule in catalogue}
    for rule in catalogue:
        parameters = dict(rule.parameters)
        if rule.pending_contract and rule.pending_contract.parameters:
            parameters.update({f"pending.{name}": parameter for name, parameter
                               in rule.pending_contract.parameters.items()})
        for name, parameter in parameters.items():
            paths = [parameter.profile_field] if parameter.profile_field else []
            values = parameter.value if isinstance(parameter.value, list) else [parameter.value]
            paths.extend(value for value in values if isinstance(value, str)
                         and value.startswith(("vessel.", "port.", "instance."))
                         # A validated cross-reference (D74's exact recheck rule)
                         # shares the port.* namespace but is not a model field.
                         and value not in rule_ids)
            for path in paths:
                try:
                    _resolve_model_path(path)
                except (AssertionError, KeyError) as error:
                    pytest.fail(f"{rule.id}.{name}: {error}")


def test_quay_height_uses_enforced_tier_count(catalogue):
    rule = catalogue["port.e3.quay_height"]
    parameters = [parameter for parameter in rule.parameters.values()
                  if parameter.profile_field == "port.quay_crane.max_on_deck_tiers"]
    assert len(parameters) == 1
    assert parameters[0].unit == "tiers"
    assert _resolve_model_path(parameters[0].profile_field) is int


@pytest.mark.parametrize("path", [
    "port.quay_crane.max_reachable_height_m",
    "vessel.stowage_policy.heavy_threshold_kg",
    "instance.rule_overrides.invented_field",
    "vessel.cells.invented_field",
])
def test_model_path_resolver_rejects_stale_or_untyped_fields(path):
    with pytest.raises(AssertionError):
        _resolve_model_path(path)


def _domain_constraint_rows(text):
    """Read only the constraint table, preserving the independent inventory above."""
    section = text.split("## Constraint catalogue (what the checker enforces)", 1)[1]
    section = section.split("\n## ", 1)[0]
    return [
        line.split("|")[2].strip()
        for line in section.splitlines()
        if line.startswith("| ") and not line.startswith("| Group |")
    ]


def test_independent_inventory_matches_current_domain_constraint_table():
    domain = Path(__file__).resolve().parents[2] / "docs/domain.md"
    assert _domain_constraint_rows(domain.read_text()) == DOMAIN_ROWS


def test_domain_inventory_detects_a_new_constraint_row():
    domain = Path(__file__).resolve().parents[2] / "docs/domain.md"
    changed = domain.read_text().replace(
        "| Group | Constraint | Notes |",
        "| Group | Constraint | Notes |\n| Physical | New independent constraint | |",
        1,
    )
    assert "New independent constraint" in _domain_constraint_rows(changed)
    with pytest.raises(AssertionError):
        assert _domain_constraint_rows(changed) == DOMAIN_ROWS


# A composite row cannot be certified by an unrelated validation/tie-break rule.
COMPOSITE_ROW_RULES = {
    0: {"expert.physical.slot_occupancy", "expert.physical.size_fit", "expert.physical.no_20_on_40"},
    1: {"expert.physical.stack_weight", "expert.physical.tier_height"},
    3: {"expert.rotation.no_overstow", "expert.rotation.hatch_overstow"},
    4: {"imdg.segregation.7_2_4", "imdg.segregation.7_4_3"},
    6: {"iscode.loading.displacement", "iscode.loading.draft", "iscode.loading.gm", "iscode.b3_1.free_surface"},
    7: {"iscode.a2_2.gz_area_0_30", "iscode.a2_2.gz_area_0_40", "iscode.a2_2.gz_area_30_40",
        "iscode.a2_2.gz_at_30", "iscode.a2_2.max_gz_angle", "iscode.a2_2.initial_gm"},
    8: {"iscode.loading.trim", "iscode.loading.list"},
    9: {"iacs.s11a.longitudinal_strength"},
    13: {"expert.e1.heavy_bottom", "expert.e2.weight_order", "expert.physical.stack_weight"},
    14: {"expert.e8.under_deck", "expert.e8.on_deck", "expert.e8.away_from_heat", "expert.e8.top_stowage"},
    15: {"expert.e9.over_height", "expert.e9.over_width_length"},
    16: {"port.e3.yard_height", "port.e3.quay_height"},
    17: {"expert.moves.top_access", "expert.moves.hatch_clear", "expert.moves.bottom_up"},
}


@pytest.mark.parametrize("row_index,required", COMPOSITE_ROW_RULES.items())
def test_composite_domain_rows_map_to_each_substantive_rule(catalogue, row_index, required):
    mapped = {rule.id for rule in catalogue.for_domain_row(DOMAIN_ROWS[row_index])}
    assert required <= mapped
    assert all(catalogue[rule_id].is_hard() for rule_id in required)


@pytest.mark.parametrize("row_index,required", COMPOSITE_ROW_RULES.items())
def test_composite_coverage_detects_missing_subrule_despite_remaining_mapping(
    catalogue, row_index, required,
):
    """A table row still being associated with some rule is insufficient."""
    row = DOMAIN_ROWS[row_index]
    for removed_id in required:
        data = catalogue.model_dump()
        removed = next(rule for rule in data["rules"] if rule["id"] == removed_id)
        removed["domain_rows"] = [DOMAIN_ROWS[2]]
        changed = type(catalogue).model_validate(data)
        assert changed.for_domain_row(row)  # The old existence-only check passes.
        mapped = {rule.id for rule in changed.for_domain_row(row)}
        with pytest.raises(AssertionError):
            assert required <= mapped


@pytest.mark.parametrize("length,within", [(90., True), (500., True), (500.001, False), (501., False)])
def test_s11a_length_metadata_includes_upper_boundary(catalogue, length, within):
    parameters = catalogue["iacs.s11a.longitudinal_strength"].parameters
    assert parameters["maximum_length"].unit == "m"
    assert parameters["maximum_length"].provenance.clause == "S11A.1.1.2(i)"
    assert parameters["maximum_length"].provenance.page == 1
    assert (parameters["minimum_length"].value <= length <= parameters["maximum_length"].value) is within


@pytest.mark.parametrize("rule_id,name,field,direction", [
    ("iscode.loading.trim", "minimum", "min_trim_m", "raise the minimum"),
    ("iscode.loading.trim", "maximum", "max_trim_m", "lower the maximum"),
    ("iscode.loading.displacement", "minimum", "min_displacement_kg", "raise the minimum"),
    ("iscode.loading.displacement", "maximum", "max_displacement_kg", "lower the maximum"),
])
def test_loading_policy_metadata_requires_explicit_signed_limits(
    catalogue, rule_id, name, field, direction,
):
    rule = catalogue[rule_id]
    parameter = rule.parameters[name]
    assert rule.is_hard(stage="B", inherited=True)
    assert parameter.value is None
    assert parameter.profile_field == f"vessel.hull.{field}"
    policy = parameter.provenance.assumption
    assert f"vessel.hull.{field}_provenance" in policy
    assert "D40" in policy and "no numeric fallback" in policy
    assert "instance.rule_overrides" in policy and "may only tighten" in policy
    assert f"['{rule_id}']['{name}']" in policy and direction in policy
    assert "fail closed" in policy
    if rule_id == "iscode.loading.trim":
        assert "never derive one trim bound from the other" in policy
    else:
        assert "not hydrostatic-table endpoints" in policy


def test_weather_keel_metadata_requires_explicit_vessel_area_and_provenance(catalogue):
    parameter = catalogue["iscode.a2_3.weather"].parameters["bilge_keel_area_m2"]
    assert parameter.value is None and parameter.unit == "m^2"
    assert parameter.profile_field == "vessel.hull.bilge_keel_area_m2"
    assert parameter.provenance.clause == "A 2.3.4, table 2.3.4-3"
    policy = parameter.provenance.assumption
    assert "vessel.hull.bilge_keel_area_m2_provenance" in policy
    assert "D40" in policy and "no numeric fallback" in policy
    assert "['iscode.a2_3.weather']['bilge_keel_area_m2']" in policy
    assert "may only tighten" in policy and "reduce" in policy
    assert "0" in policy and "None" in policy and "fail closed" in policy
