"""Structured DG DoC storage, structural integrity and legacy JSON compatibility."""

from copy import deepcopy
from pathlib import Path
import tomllib

import pytest
from pydantic import ValidationError

from stowage.io import load_instance, save_instance
from stowage.model import DgDocArea, Instance, Vessel
from stowage.model.fixtures import build_instance


def test_legacy_json_without_doc_fields_loads(tmp_path):
    payload = build_instance().model_dump(mode="json")
    payload["schema_version"] = "1.0"
    payload["vessel"].pop("dg_doc_areas")
    for stack in payload["vessel"]["stacks"]:
        stack.pop("dg_doc_area_id")
    legacy = Instance.model_validate(payload)
    assert legacy.vessel.dg_doc_areas == []
    assert all(s.dg_doc_area_id is None for s in legacy.vessel.stacks)
    assert load_instance(save_instance(legacy, tmp_path / "legacy.json")) == legacy


def test_doc_fields_round_trip_and_catalog_order(tmp_path):
    payload = build_instance(layover=True, with_dg_doc=True).model_dump(mode="json")
    payload["vessel"]["dg_doc_areas"].reverse()
    for area in payload["vessel"]["dg_doc_areas"]:
        area["stack_ids"].reverse()
        area["classes_allowed"].reverse()
        area["packing_forms_allowed"].append("portable_tanks")
        area["provenance"]["parents"] = ["synthetic-document"]
    instance = Instance.model_validate(payload)
    assert [a.id for a in instance.vessel.dg_doc_areas] == ["HOLD-01", "WEATHER-DECK"]
    first = save_instance(instance, tmp_path / "doc.json")
    restored = load_instance(first)
    assert restored == instance
    assert restored.vessel.dg_doc_areas[0].packing_forms_allowed == [
        "packaged_goods_in_closed_freight_containers", "portable_tanks"]
    assert first.read_bytes() == save_instance(restored, tmp_path / "doc2.json").read_bytes()


@pytest.mark.parametrize("defect,match", [
    ("duplicate_area", "duplicate dg_doc_areas"),
    ("unknown_stack", "unknown stack"),
    ("unknown_area", "membership disagree|unknown DG DoC area"),
    ("missing_membership", "missing from DG DoC area"),
    ("multiple_areas", "multiple DG DoC areas"),
    ("wrong_kind", "deck/hold kind disagree"),
])
def test_doc_reference_integrity(defect, match):
    payload = build_instance(with_dg_doc=True).vessel.model_dump(mode="json")
    areas = payload["dg_doc_areas"]
    if defect == "duplicate_area":
        areas.append(deepcopy(areas[0]))
    elif defect == "unknown_stack":
        areas[0]["stack_ids"].append("99901H")
    elif defect == "unknown_area":
        payload["stacks"][0]["dg_doc_area_id"] = "missing"
    elif defect == "missing_membership":
        areas[0]["stack_ids"].pop()
    elif defect == "multiple_areas":
        areas[1]["stack_ids"].append(areas[0]["stack_ids"][0])
    elif defect == "wrong_kind":
        areas[0]["kind"] = "deck"
    with pytest.raises(ValidationError, match=match):
        Vessel.model_validate(payload)


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800", "feeder_1700"])
def test_doc_area_shape_matches_fleet_parameters(name):
    path = Path(__file__).resolve().parents[2] / "params" / "fleet" / f"{name}.toml"
    document = tomllib.loads(path.read_text())["dg_document"]
    # Area-local fields pass directly; the loader can add document-level source
    # and guidance URLs to each area's sources without dropping input metadata.
    for raw in document["areas"]:
        area = DgDocArea.model_validate(raw)
        assert set(area.classes_allowed) == set(raw["classes_allowed"])
        assert area.equipment_assumptions == raw["equipment_assumptions"]
