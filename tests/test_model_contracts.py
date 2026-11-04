"""Smoke tests of the data-model contracts (full fixtures + invariants are task 1.1's)."""

from __future__ import annotations

import pydantic
import pytest
from pydantic import TypeAdapter

from stowage import model as m
from stowage.conventions import make_container_id

CID = make_container_id("QZXU", 1)


def test_all_core_types_exported() -> None:
    for name in ["Container", "Vessel", "Yard", "YardState", "PortProfile", "ShipState", "Rotation", "Move",
                 "MovePlan", "Instance", "Violation", "CheckReport", "PlannerResult", "MoveCount", "Provenance"]:
        assert hasattr(m, name), name


def test_move_union_discriminates_and_round_trips() -> None:
    ta = TypeAdapter(list[m.Move])
    raw = [
        {"kind": "relocate", "container_id": CID, "src": "A1-001-01-02", "dst": "A1-002-01-01", "call_index": 0},
        {"kind": "load", "container_id": CID, "src": "A1-002-01-01", "dst": "0010102", "call_index": 0},
        {"kind": "hatch_cover_off", "cover_id": "HC-01", "call_index": 0},
        {"kind": "restow_off", "container_id": CID, "src": "0010182", "call_index": 0},
    ]
    moves = ta.validate_python(raw)
    assert [type(x).__name__ for x in moves] == ["Relocate", "Load", "HatchCoverOff", "RestowOff"]
    assert ta.validate_json(ta.dump_json(moves)) == moves


def test_unknown_fields_and_bad_ids_rejected() -> None:
    with pytest.raises(pydantic.ValidationError):
        m.Violation(rule_id="imdg.x", severity="hard", call_index=0, message="m", oops=1)
    with pytest.raises(pydantic.ValidationError):
        m.Violation(rule_id="NoDots", severity="hard", call_index=0, message="m")
    with pytest.raises(pydantic.ValidationError):
        m.Placement(slot="12345", container_id=CID)


def test_container_round_trip() -> None:
    c = m.Container(id=CID, iso_type="45R1", length_ft=40, height_m=2.896, tare_kg=4800, gross_mass_kg=27000,
                    pol="THLCH", pod="SGSIN", reefer=True, reefer_setpoint_c=-18.0,
                    special_stowage=[m.SpecialStowage.UNDER_DECK],
                    dg=m.DangerousGoods(un_number="1263", imdg_class="3", packing_group="II", stowage_category="B"),
                    oog=m.OutOfGauge(over_height_m=0.3))
    assert m.Container.model_validate_json(c.model_dump_json()) == c


def test_component_provenance_pins_catalogue_content() -> None:
    from hashlib import sha256
    from importlib.resources import files

    from stowage.versions import component_versions

    expected = "sha256:" + sha256(files("stowage.rules").joinpath("catalogue.toml").read_bytes()).hexdigest()
    assert component_versions()["rule_catalogue"] == expected


def test_component_provenance_pins_shared_infrastructure() -> None:
    from stowage.versions import component_versions

    assert component_versions()["infrastructure"] == "3.0"
