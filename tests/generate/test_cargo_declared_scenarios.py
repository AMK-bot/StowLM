"""Synthetic entry declarations retain unknowns and require later scope binding."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from stowage.generate import cargo_shipments
from stowage.params import load_param_file
from stowage.rules.imdg.dgl import entries_for_un
from stowage.rules.imdg.scenarios import BindingContext
from stowage.rules.imdg.shipment_assessments import assess_h1, assess_sw19


def _entry(un):
    return entries_for_un(un)[0]


def _bound(facts, un):
    context = BindingContext(instance_id="synthetic-instance", container_id="QZXU0000020",
                             entry_path="primary", un_number=f"{un:04d}", packing_group=_entry(un).packing_group,
                             vessel_id="synthetic-vessel", voyage_id="synthetic-voyage", call_index=0,
                             slot="0010182")
    registry = {}
    for key in ("h1", "sw19"):
        if key in facts:
            provenance = facts[key]["provenance"]
            registry[provenance["scenario_id"]] = {
                **{name: value for name, value in asdict(context).items() if name != "slot"},
                "allowed_slots": [context.slot], "document": provenance["document"],
                "clause": provenance["clause"],
            }
    return dict(context=context, registry=registry)


@pytest.mark.parametrize("un", [1333, 1402])
def test_h1_declares_dry_packing_without_inspection_or_placement_outcomes(un):
    entry = _entry(un)
    assert "H1" in entry.source_stowage_codes
    facts = cargo_shipments.declare_entry_scenarios(entry, iso_type="22G1")
    assert facts["synthetic"] is True
    assert facts["scope"] == "shipment_entry"
    assert facts["template_ids"] == [facts["h1"]["provenance"]["scenario_id"]]
    assert set(facts["h1"]) == {"provenance", "ctu_weathertight", "packing_condition"}
    assert facts["h1"]["ctu_weathertight"] is True
    assert facts["h1"]["packing_condition"] == "dry"
    assert "synthetic" in facts["h1"]["provenance"]["document"].lower()
    assert not {"dry", "special_provisions", "protected_from_heat", "clear_living_quarters", "sunlight", "inspection"} & facts.keys()
    assert assess_h1({"ctu_closed": True, **facts}, on_deck=True) is None
    scope = _bound(facts, un)
    assert assess_h1({"ctu_closed": True, **facts}, on_deck=True, **scope) is True
    assert assess_h1({**facts, "ctu_closed": False}, on_deck=True, **scope) is False


@pytest.mark.parametrize("un", [3480, 3481])
def test_sw19_normal_intact_route_declares_only_376_377_inapplicability(un):
    entry = _entry(un)
    facts = cargo_shipments.declare_entry_scenarios(entry, iso_type="45G1")
    record = facts["sw19"]
    assert facts["synthetic"] is True
    assert facts["scope"] == "shipment_entry"
    assert record["un_number"] == f"{un:04d}"
    assert record["battery_condition"] == "intact"
    assert record["transport_purpose"] == "normal"
    assert record["special_provision_applicability"] == {"376": False, "377": False}
    assert not {"transport_in_accordance", "voyage", "short_international_voyage", "inspection"} & record.keys()
    assert "special_provisions" not in facts
    declaration = cargo_shipments.declare_shipment(entry, iso_type="45G1", seed=13, generator_version="test")
    assert declaration["special_provisions"] is None
    assert assess_sw19(facts, un_number=f"{un:04d}").compliant is None
    result = assess_sw19(facts, un_number=f"{un:04d}", **_bound(facts, un))
    assert result.compliant is True
    assert result.category == entry.stowage_category
    assert "188" in entry.special_provisions  # Other SP applicability remains unresolved.


@pytest.mark.parametrize("un,iso", [(1333, "22U1"), (1402, "22T1"), (3480, "22P3"), (3481, "22T1"), (1263, "22G1")])
def test_unsupported_equipment_and_entries_have_no_synthetic_clearance(un, iso):
    assert cargo_shipments.declare_entry_scenarios(_entry(un), iso_type=iso) == {}


def test_explicit_templates_are_pure_and_independent_from_outputs(monkeypatch):
    templates = load_param_file("cargo", "declared_scenarios")["templates"]
    before = deepcopy(templates)

    def no_io(*args, **kwargs):
        raise AssertionError("Explicit scenario parameters must not read files")

    monkeypatch.setattr(cargo_shipments, "load_param_file", no_io)
    first = cargo_shipments.declare_entry_scenarios(_entry(3480), iso_type="22G1", scenarios=templates)
    first["sw19"]["special_provision_applicability"]["376"] = True
    second = cargo_shipments.declare_entry_scenarios(_entry(3480), iso_type="22G1", scenarios=templates)
    assert second["sw19"]["special_provision_applicability"]["376"] is False
    assert templates == before
    assert cargo_shipments.declare_entry_scenarios(_entry(3480), iso_type="22G1", scenarios={}) == {}


def test_qualified_class_expectation_uses_division_but_never_another_compatibility_group():
    params = load_param_file("cargo", "feeder_export")
    dry = next(row for row in params["equipment"] if row["id"] == "dry_20")
    params["equipment"] = [dry]
    dry["empty_share"]["value"] = 0
    params["dg"]["share_of_eligible_loaded_boxes"]["value"] = 1
    cls = deepcopy(params["dg"]["classes"][0])
    cls["id"] = "1.4S"
    cls["un_numbers"] = [{"un_number": "0012", "share": {"value": 1}}]
    params["dg"]["classes"] = [cls]
    pods = params["rotations"][0]["pods"]
    expected = cargo_shipments.BookingAudit(params, n_boxes=2, pods=pods).data["demand_expectation"]
    assert expected["dg_by_class"] == {"1.4S": 2}
    assert expected["unresolved_dgl"] == []
    cls["id"] = "1.4G"
    wrong_group = cargo_shipments.BookingAudit(params, n_boxes=2, pods=pods).data["demand_expectation"]
    assert wrong_group["dg_by_class"] == {}
    assert wrong_group["unresolved_dgl"][0]["configured_class"] == "1.4G"


@pytest.mark.parametrize("un,iso", [(1333, "22G1"), (1402, "45R1"), (3480, "45G1")])
def test_closed_packing_facts_are_explicit_in_the_synthetic_template(un, iso):
    facts = cargo_shipments.declare_entry_scenarios(_entry(un), iso_type=iso)
    assert facts["ctu_closed"] is True
    assert facts["packing_form"] == "packaged_goods_in_closed_freight_containers"
    templates = load_param_file("cargo", "declared_scenarios")["templates"]
    name = "sw19" if un == 3480 else "h1"
    assert templates[name]["declarations"]["ctu_closed"] is True
    assert templates[name]["declarations"]["packing_form"] == facts["packing_form"]


@pytest.mark.parametrize("mutation", ["non_synthetic", "wrong_scope", "foreign_id", "numeric_boolean", "outcome_flag"])
def test_invalid_or_outcome_bearing_templates_fail_explicitly(mutation):
    templates = load_param_file("cargo", "declared_scenarios")["templates"]
    template = templates["h1"]
    if mutation == "non_synthetic":
        template["synthetic"] = False
    elif mutation == "wrong_scope":
        template["scope"] = "all_shipments"
    elif mutation == "foreign_id":
        template["provenance"]["scenario_id"] = "foreign-template"
    elif mutation == "numeric_boolean":
        template["declarations"]["ctu_closed"] = 1
    else:
        template["declarations"]["inspection_passed"] = True
    with pytest.raises(ValueError, match="synthetic|scenario"):
        cargo_shipments.declare_entry_scenarios(_entry(1333), iso_type="22G1", scenarios=templates)


@pytest.mark.parametrize("un", [1333, 1402, 3480, 3481])
def test_declarations_pin_authored_entry_identity_including_explicit_null_pg(un):
    import json

    entry = _entry(un)
    before = asdict(entry)
    facts = cargo_shipments.declare_entry_scenarios(entry, iso_type="22G1")
    assert facts["entry_id"] == entry.id
    assert facts["un_number"] == f"{entry.un_number:04d}"
    assert "packing_group" in facts
    assert facts["packing_group"] == entry.packing_group
    restored = json.loads(json.dumps(facts))
    assert {key: restored[key] for key in ("entry_id", "un_number", "packing_group")} == {
        "entry_id": entry.id, "un_number": f"{entry.un_number:04d}", "packing_group": entry.packing_group,
    }
    assert asdict(entry) == before
    if un in (3480, 3481):
        assert "packing_group" in restored and restored["packing_group"] is None


def test_h1_declaration_provenance_names_packing_certificate_and_transport_document():
    facts = cargo_shipments.declare_entry_scenarios(_entry(1333), iso_type="22G1")
    provenance = facts["h1"]["provenance"]
    assert "5.4.2" in provenance["clause"]
    assert "5.4.1" in provenance["clause"]
    assert "synthetic" in provenance["document"].lower()
    template = load_param_file("cargo", "declared_scenarios")["templates"]["h1"]
    assert "5.4.2" in template["source"]["locator"]
    assert "5.4.1" in template["source"]["locator"]


@pytest.mark.parametrize("un", [3480, 3481])
def test_explicit_sp377_recycling_scenario_is_non_short_and_changes_sw19_category(un):
    facts = cargo_shipments.declare_entry_scenarios(_entry(un), iso_type="22G1", scenario_key="sw19_recycling")
    record = facts["sw19"]
    assert record["battery_condition"] == "intact"
    assert record["transport_purpose"] == "recycling"
    assert record["special_provision_applicability"] == {"376": False, "377": True}
    assert record["transport_in_accordance"] is True
    voyage = record["voyage"]
    assert voyage["short_international_voyage"] is False
    assert {"document", "clause", "scenario_id"} == voyage["provenance"].keys()
    assert voyage["provenance"]["scenario_id"] != record["provenance"]["scenario_id"]
    assert set(facts["template_ids"]) == {record["provenance"]["scenario_id"], voyage["provenance"]["scenario_id"]}
    assert "special_provisions" not in facts
    assert assess_sw19(facts, un_number=f"{un:04d}").compliant is None
    scope = _bound(facts, un)
    source = voyage["provenance"]
    context = scope["context"]
    scope["registry"][source["scenario_id"]] = {
        **{name: value for name, value in asdict(context).items() if name != "slot"},
        "allowed_slots": [context.slot], "document": source["document"], "clause": source["clause"],
    }
    result = assess_sw19(facts, un_number=f"{un:04d}", **scope)
    assert (result.compliant, result.category) == (True, "C")
    scope["registry"].pop(source["scenario_id"])
    assert assess_sw19(facts, un_number=f"{un:04d}", **scope).compliant is None


def test_recycling_template_is_optional_independent_and_leaves_main_normal_route_unchanged():
    templates = load_param_file("cargo", "declared_scenarios")["templates"]
    before = deepcopy(templates)
    normal = cargo_shipments.declare_entry_scenarios(_entry(3480), iso_type="22G1", scenarios=templates)
    recycling = cargo_shipments.declare_entry_scenarios(
        _entry(3480), iso_type="22G1", scenarios=templates, scenario_key="sw19_recycling")
    assert normal["sw19"]["transport_purpose"] == "normal"
    assert "voyage" not in normal["sw19"]
    assert normal["sw19"]["special_provision_applicability"] == {"376": False, "377": False}
    assert recycling["sw19"]["transport_purpose"] == "recycling"
    assert templates == before
    assert cargo_shipments.declare_entry_scenarios(
        _entry(3480), iso_type="22T1", scenarios=templates, scenario_key="sw19_recycling") == {}


@pytest.mark.parametrize("mutation", ["missing_voyage", "numeric_short", "short_voyage", "missing_provenance", "packing_outcome"])
def test_recycling_template_needs_exact_declared_non_short_voyage_and_no_outcomes(mutation):
    templates = load_param_file("cargo", "declared_scenarios")["templates"]
    recycling = templates["sw19_recycling"]
    if mutation == "missing_voyage":
        recycling.pop("voyage")
    elif mutation == "numeric_short":
        recycling["voyage"]["declarations"]["short_international_voyage"] = 0
    elif mutation == "short_voyage":
        recycling["voyage"]["declarations"]["short_international_voyage"] = True
    elif mutation == "missing_provenance":
        recycling["voyage"].pop("provenance")
    else:
        recycling["declarations"]["packing_inspection_passed"] = True
    # An unused stress template cannot change the main normal scenario.
    assert cargo_shipments.declare_entry_scenarios(
        _entry(3480), iso_type="22G1", scenarios=templates)["sw19"]["transport_purpose"] == "normal"
    with pytest.raises(ValueError, match="synthetic|scenario"):
        cargo_shipments.declare_entry_scenarios(
            _entry(3480), iso_type="22G1", scenarios=templates, scenario_key="sw19_recycling")


def test_recycling_requirements_keep_binding_but_not_a_duplicate_voyage_unknown():
    entry = _entry(3480)
    facts = cargo_shipments.declare_entry_scenarios(entry, iso_type="22G1", scenario_key="sw19_recycling")
    requirements = cargo_shipments.shipment_requirements(entry, declarations=facts)
    assert "assessment_scenarios_binding" in requirements
    assert "special_provisions" in requirements
    assert "short_international_voyage" not in requirements


@pytest.mark.parametrize("mutation", ["absent", "null", "unknown_classification", "numeric_classification",
                                      "string_classification", "missing_provenance", "null_provenance", "empty_source"])
def test_malformed_recycling_voyage_declaration_remains_an_explicit_unknown(mutation):
    entry = _entry(3480)
    facts = cargo_shipments.declare_entry_scenarios(entry, iso_type="22G1", scenario_key="sw19_recycling")
    record = facts["sw19"]
    if mutation == "absent":
        record.pop("voyage")
    elif mutation == "null":
        record["voyage"] = None
    elif mutation == "unknown_classification":
        record["voyage"]["short_international_voyage"] = None
    elif mutation == "numeric_classification":
        record["voyage"]["short_international_voyage"] = 0
    elif mutation == "string_classification":
        record["voyage"]["short_international_voyage"] = "false"
    elif mutation == "missing_provenance":
        record["voyage"].pop("provenance")
    elif mutation == "null_provenance":
        record["voyage"]["provenance"] = None
    else:
        record["voyage"]["provenance"]["document"] = " "
    requirements = cargo_shipments.shipment_requirements(entry, declarations=facts)
    assert "short_international_voyage" in requirements
    assert "assessment_scenarios_binding" in requirements
