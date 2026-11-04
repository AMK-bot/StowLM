"""DG shipment evidence is typed cargo data, retained without rule overrides."""

import pytest
from pydantic import ValidationError

from stowage.frozen import StubTaintError
from stowage.io import load_instance, save_instance
from stowage.model import DangerousGoods, Instance, PackingForm, Provenance
from stowage.model.fixtures import build_instance


BOOLEAN_FACTS = (
    "ctu_closed", "protected_from_heat", "clear_living_quarters",
    "ventilation_compliant", "shaded", "air_circulation", "dry",
    "handling_compliant", "subsidiary_hazards_verified", "short_international_voyage",
)
LIST_FACTS = ("special_provisions", "verified_provisions", "verified_segregation_codes")
SHIPMENT_FACTS = (*BOOLEAN_FACTS, *LIST_FACTS, "packing_form", "bulk_container_type", "shipment_provenance")


def _dg(**facts):
    return DangerousGoods(un_number="1263", imdg_class="3", stowage_category="A", **facts)


def test_legacy_dg_json_preserves_unknown_shipment_facts(tmp_path):
    payload = build_instance().model_dump(mode="json")
    payload["schema_version"] = "1.1"
    for box in payload["containers"]:
        if box["dg"] is not None:
            for field in SHIPMENT_FACTS:
                box["dg"].pop(field, None)
    instance = Instance.model_validate(payload)
    dg = next(box.dg for box in instance.containers if box.dg is not None)
    assert all(getattr(dg, field) is None for field in SHIPMENT_FACTS)
    assert load_instance(save_instance(instance, tmp_path / "legacy.json")) == instance


@pytest.mark.parametrize("field", BOOLEAN_FACTS)
@pytest.mark.parametrize("value", ["true", "false", 0, 1])
def test_shipment_booleans_reject_coercion(field, value):
    with pytest.raises(ValidationError, match=field):
        _dg(**{field: value})


def test_packing_form_vocabulary_and_unknown_rejection():
    forms = {
        "packaged_goods_in_closed_freight_containers", "open_freight_container",
        "ibc", "portable_tank", "tank_container", "bulk",
    }
    assert {member.value for member in PackingForm} == forms
    for form in forms:
        assert _dg(packing_form=form).packing_form.value == form
    with pytest.raises(ValidationError, match="packing_form"):
        _dg(packing_form="portable_tanks")
    with pytest.raises(ValidationError, match="bulk_container_type"):
        _dg(bulk_container_type="BK4")


def test_optional_evidence_lists_distinguish_unknown_and_verified_empty():
    unknown = _dg()
    verified_empty = _dg(**{field: [] for field in LIST_FACTS})
    assert all(getattr(unknown, field) is None for field in LIST_FACTS)
    assert all(getattr(verified_empty, field) == [] for field in LIST_FACTS)
    dg = _dg(special_provisions=["377", "188", "377"],
             verified_provisions=["SW5", "IMDG:7.1.4.5", "SW5"],
             verified_segregation_codes=["SG72", "SG27", "SG72"])
    assert dg.special_provisions == ["188", "377"]
    assert dg.verified_provisions == ["IMDG:7.1.4.5", "SW5"]
    assert dg.verified_segregation_codes == ["SG27", "SG72"]
    with pytest.raises(ValidationError, match="special_provisions"):
        _dg(special_provisions=["SW19"])


def test_recursive_shipment_facts_round_trip_byte_identically(tmp_path):
    payload = build_instance(layover=True).model_dump(mode="json")
    dg = next(box["dg"] for box in payload["containers"] if box["dg"] is not None)
    evidence = dict(
        **{field: index % 2 == 0 for index, field in enumerate(BOOLEAN_FACTS)},
        packing_form="packaged_goods_in_closed_freight_containers",
        special_provisions=["188", "377"], bulk_container_type="BK3",
        verified_provisions=["IMDG:7.1.4.5", "SW5"],
        verified_segregation_codes=["SG27"],
        shipment_provenance=Provenance(parents=["synthetic-shipment-declaration"]).model_dump(mode="json"),
    )
    dg.update(evidence)
    dg["additional"] = [_dg(packing_form="portable_tank", dry=False,
                             verified_provisions=["IMDG:7.1.4.5"]).model_dump(mode="json")]
    instance = Instance.model_validate(payload)
    first = save_instance(instance, tmp_path / "first.json")
    restored = load_instance(first)
    assert restored == instance
    assert first.read_bytes() == save_instance(restored, tmp_path / "second.json").read_bytes()
    restored_dg = next(box.dg for box in restored.containers if box.dg is not None)
    assert restored_dg.additional[0].packing_form == PackingForm("portable_tank")
    assert restored_dg.additional[0].dry is False
    assert restored_dg.additional[0].verified_provisions == ["IMDG:7.1.4.5"]


@pytest.mark.parametrize("nested", [False, True])
def test_shipment_provenance_stub_taint_is_refused(tmp_path, nested):
    instance = build_instance()
    dg = next(box.dg for box in instance.containers if box.dg is not None)
    target = dg
    if nested:
        dg.additional = [_dg()]
        target = dg.additional[0]
    target.shipment_provenance = Provenance(stubs={"generator.generate": "synthetic test stub"})
    path = tmp_path / "tainted.json"
    with pytest.raises(StubTaintError, match="save_instance"):
        save_instance(instance, path)
    assert not path.exists()
    restored = load_instance(save_instance(instance, path, allow_stubs=True))
    # Validation accumulates nested evidence in the top-level lineage; direct
    # assignment above intentionally bypassed that boundary.
    assert restored == Instance.model_validate(instance.model_dump())
    assert restored.provenance.stubs == target.shipment_provenance.stubs
