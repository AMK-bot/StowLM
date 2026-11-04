"""Synthetic shipment facts stay distinct from external clearance.

The tank fixtures below are invented 20-foot equipment envelopes and bounded
mass scenarios for API tests, not calibrated specifications or packing approvals.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from stowage.generate.cargo import CARGO_GENERATOR_VERSION, CargoGenerationReport, generate_load_list
from stowage.generate.cargo_shipments import SHIPMENT_POLICY_VERSION, declare_shipment, shipment_requirements
from stowage.model.cargo import DangerousGoods, PackingForm
from stowage.params import load_param_file
from stowage.rules.imdg.dgl import entries_for_un, lookup_dgl

UNKNOWN_FACTS = (
    "protected_from_heat", "clear_living_quarters", "ventilation_compliant",
    "shaded", "air_circulation", "dry", "handling_compliant",
    "subsidiary_hazards_verified", "short_international_voyage",
    "bulk_container_type", "verified_provisions", "verified_segregation_codes",
)
DEFAULT_UNS = (336, 1978, 1006, 1017, 1263, 1333, 1361, 1402, 1748, 3109, 2810, 3373, 2915, 1789, 3480)


def declared_dg(entry, iso_type="22G1", *, seed=81):
    return DangerousGoods(
        un_number=f"{entry.un_number:04d}", imdg_class=entry.hazard_class + (entry.compatibility_group or ""),
        packing_group=entry.packing_group, stowage_category=entry.stowage_category,
        subsidiary_risks=list(entry.subsidiary_risks),
        stowage_codes=[code for code in entry.stowage_codes if not code.startswith("H")],
        handling_codes=[code for code in entry.stowage_codes if code.startswith("H")],
        segregation_codes=list(entry.segregation_codes),
        **declare_shipment(entry, iso_type=iso_type, seed=seed, generator_version=CARGO_GENERATOR_VERSION),
    )


@pytest.mark.parametrize("un", DEFAULT_UNS)
def test_default_un_variants_are_closed_packages_without_clearance(un):
    entries = entries_for_un(un)
    assert entries
    for entry in entries:
        dg = declared_dg(entry)
        assert dg.packing_form == PackingForm.PACKAGED_GOODS_IN_CLOSED_FREIGHT_CONTAINERS
        assert dg.ctu_closed is True
        for field in UNKNOWN_FACTS:
            assert getattr(dg, field) is None, (entry.id, field)
        assert dg.special_provisions == (None if entry.special_provisions else [])
        assert dg.shipment_provenance.seed == 81
        assert dg.shipment_provenance.created_by == "generate.cargo.synthetic_shipper"
        assert dg.shipment_provenance.versions == {"cargo_generator": CARGO_GENERATOR_VERSION,
                                                  "dg_shipment_policy": SHIPMENT_POLICY_VERSION}
        assert dg.shipment_provenance.created_at is None
        assert dg.shipment_provenance.stubs == {}
        assert "synthetic-dg-shipment-policy-v1" in dg.shipment_provenance.parents
        assert f"imdg-42-24:{entry.id}" in dg.shipment_provenance.parents
        assert dg.model_dump_json() == declared_dg(entry).model_dump_json()
        reconstructed = DangerousGoods.model_validate_json(dg.model_dump_json())
        assert reconstructed == dg
        assert all(getattr(reconstructed, field) is None for field in UNKNOWN_FACTS)


@pytest.mark.parametrize("iso_type,expected,closed", [
    ("22G1", PackingForm.PACKAGED_GOODS_IN_CLOSED_FREIGHT_CONTAINERS, True),
    ("45R1", PackingForm.PACKAGED_GOODS_IN_CLOSED_FREIGHT_CONTAINERS, True),
    ("22U1", PackingForm.OPEN_FREIGHT_CONTAINER, False),
    ("45P3", PackingForm.OPEN_FREIGHT_CONTAINER, False),
    ("22T1", PackingForm.TANK_CONTAINER, True),
    ("22B0", None, None),
])
def test_equipment_declarations_are_explicit_and_conservative(iso_type, expected, closed):
    dg = declared_dg(lookup_dgl(1263, "II"), iso_type)
    assert dg.packing_form == expected
    assert dg.ctu_closed is closed
    assert all(getattr(dg, field) is None for field in UNKNOWN_FACTS)


@pytest.mark.parametrize("un", [1978, 1006, 1017, 1333, 3373, 2915, 3480])
def test_unsupported_tank_substance_remains_unknown(un):
    dg = declared_dg(entries_for_un(un)[0], "22T1")
    assert dg.packing_form is None
    assert dg.ctu_closed is None


@pytest.mark.parametrize("un,pg,required", [
    (336, None, {"protected_from_heat"}),
    (1978, None, {"clear_living_quarters", "special_provisions"}),
    (1333, "II", {"dry"}),
    (1361, "II", {"protected_from_heat", "verified_provisions", "special_provisions"}),
    (1748, "II", {"protected_from_heat", "shaded", "air_circulation", "special_provisions"}),
    (2211, "III", {"ventilation_compliant", "protected_from_heat"}),
    (2814, None, {"dry", "handling_compliant", "clear_living_quarters", "verified_segregation_codes"}),
    (3077, "III", {"bulk_container_type"}),
    (3480, None, {"special_provisions", "short_international_voyage"}),
    (2915, None, {"subsidiary_hazards_verified", "verified_provisions", "special_provisions"}),
])
def test_dgl_codes_report_missing_facts_without_asserting_them(un, pg, required):
    entry = lookup_dgl(un, pg)
    requirements = shipment_requirements(entry)
    assert required <= set(requirements)
    assert requirements == sorted(set(requirements))
    dg = declared_dg(entry)
    assert all(getattr(dg, field) is None for field in requirements)


def test_radioactive_guard_survives_json_without_clearance():
    dg = declared_dg(lookup_dgl(2915))
    assert "IMDG:7.1.4.5" in dg.stowage_codes
    assert dg.subsidiary_hazards_verified is None
    assert dg.verified_provisions is None
    recovered = DangerousGoods.model_validate_json(dg.model_dump_json())
    assert "IMDG:7.1.4.5" in recovered.stowage_codes
    assert recovered.verified_provisions is None
    assert recovered.subsidiary_hazards_verified is None


def tank_profile(un):
    """APPROXIMATION: invented test equipment, not a sourced real tank rating."""
    params = load_param_file("cargo", "feeder_export")
    equipment = deepcopy(next(e for e in params["equipment"] if e["id"] == "dry_20"))
    equipment.update(id="synthetic_tank_20", iso_size_type="22T1")
    # The standard 20-foot exterior envelope is explicit; masses below are an
    # invented internally consistent fixture, not inherited dry-box tare/rating.
    for key, number in (("tare_kg", 4000), ("max_gross_kg", 30000)):
        equipment[key] = {"value": number, "unit": "kg", "assumption": "Synthetic tank test fixture only; no physical calibration claim."}
    equipment["empty_weight_kg"]["value"] = dict(equipment["tare_kg"])
    equipment["empty_share"]["value"] = 0
    equipment["share"]["value"] = 1
    for key, number in (("min", 6000), ("mode", 16000), ("max", 28000)):
        equipment["loaded_weight_kg"][key] = {"value": number, "unit": "kg", "assumption": "Synthetic bounded mass fixture; not packing authorization."}
    params["equipment"] = [equipment]
    params["dg"]["eligible_equipment_ids"] = [equipment["id"]]
    params["dg"]["share_of_eligible_loaded_boxes"]["value"] = 1
    cls = deepcopy(next(c for c in params["dg"]["classes"] if any(int(u["un_number"]) == un for u in c["un_numbers"])))
    cls["share"]["value"] = 1
    params["dg"]["classes"] = [cls]
    params["special_stowage"]["share_of_loaded_boxes"]["value"] = 0
    return params


@pytest.mark.parametrize("un", [1263, 1789, 2810, 3109])
def test_explicit_liquid_tank_equipment_profile(un):
    audit = CargoGenerationReport()
    boxes = generate_load_list("straits", 0, 30, seed=37, params=tank_profile(un), report=audit)
    assert len(boxes) == 30
    for box in boxes:
        assert box.iso_type == "22T1" and box.length_ft == 20
        assert box.height_m == 2.591 and box.tare_kg == 4000
        assert 6000 <= box.gross_mass_kg <= 28000 < 30000
        assert box.dg.un_number == f"{un:04d}"
        assert box.dg.packing_form == PackingForm.TANK_CONTAINER
        assert box.dg.ctu_closed is True
        assert all(getattr(box.dg, field) is None for field in UNKNOWN_FACTS)
        assert box.category is None
    assert audit.realised["dg_packing_forms"] == {"tank_container": 30}
    assert audit.realised["dg_packing_forms_by_un"] == {f"{un:04d}": {"tank_container": 30}}
    assert audit.requested["dg_shipment_policy"] == SHIPMENT_POLICY_VERSION
    by_id = {box.id: box for box in boxes}
    assert set(audit.shipment_requirements) <= set(by_id)
    for box_id, fields in audit.shipment_requirements.items():
        dg = by_id[box_id].dg
        assert fields == shipment_requirements(lookup_dgl(un, dg.packing_group))
        assert all(getattr(dg, field) is None for field in fields)
