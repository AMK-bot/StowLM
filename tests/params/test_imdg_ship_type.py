"""Explicit synthetic ship-type facts use the IMDG table domain in both modes."""

import json
import re

import pytest
from pydantic import ValidationError

from stowage.model import Vessel
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel import fleet
from stowage.vessel import fleet_cache
from stowage.vessel.fleet_slices import slice_preset


SHIPS = ("feeder_500", "feeder_800", "feeder_1700")
IMO_SOURCE = "https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.556(108).pdf"


@pytest.fixture(autouse=True)
def geometry_only_without_shared_cache(monkeypatch):
    monkeypatch.setenv("STOWAGE_VESSEL_CACHE", "0")


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("name", SHIPS)
def test_explicit_raw_ship_type_and_provenance_survive_loading_in_both_modes(name, enabled):
    params = fleet.load_ship_params(name)
    assert params["imdg_ship_type"] == "closed_holds"
    provenance = params["imdg_ship_type_provenance"]
    assert all(isinstance(provenance[key], str) and provenance[key].strip()
               for key in ("assumption", "source_definition", "url", "clause"))
    assert "synthetic cellular feeders with hatch covers" in provenance["assumption"]
    assert "not inferred" in provenance["assumption"]
    assert provenance["url"] == IMO_SOURCE
    assert "7.4.3.1.2" in provenance["clause"]
    assert "7.4.3.2" in provenance["clause"]
    assert "7.4.3.3" in provenance["clause"]
    with rule_freeze_v1(enabled=enabled):
        vessel = fleet.load_vessel(name, with_hull=False)
    assert vessel.imdg_ship_type == "closed_holds"
    assert vessel.hatch_covers  # This supporting geometry does not declare the fact.
    for key in ("assumption", "source_definition", "url"):
        assert provenance[key] in vessel.sources
    assert any(provenance["clause"] in note for note in vessel.sources)
    assert Vessel.model_validate_json(vessel.model_dump_json()).imdg_ship_type == "closed_holds"


@pytest.mark.parametrize("enabled", [False, True])
def test_missing_parameter_remains_unknown_despite_hatch_covers_and_provenance(monkeypatch, enabled):
    original = fleet._parameter_bytes("feeder_500")
    missing, count = re.subn(rb'^imdg_ship_type = [^\n]*\n', b"", original, flags=re.M)
    assert count == 1
    monkeypatch.setattr(fleet, "_parameter_bytes", lambda name: missing)
    with rule_freeze_v1(enabled=enabled):
        vessel = fleet.load_vessel("feeder_500", with_hull=False)
    assert vessel.hatch_covers
    assert vessel.imdg_ship_type is None
    assert "imdg_ship_type" not in json.loads(vessel.model_dump_json())


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("value", [b'"open_deck"', b'true', b'42'])
def test_invalid_native_parameter_is_rejected_in_both_modes(monkeypatch, enabled, value):
    original = fleet._parameter_bytes("feeder_500")
    changed, count = re.subn(rb'^imdg_ship_type = [^\n]*', b'imdg_ship_type = ' + value, original, flags=re.M)
    assert count == 1
    monkeypatch.setattr(fleet, "_parameter_bytes", lambda name: changed)
    with rule_freeze_v1(enabled=enabled), pytest.raises(ValidationError):
        fleet.load_vessel("feeder_500", with_hull=False)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("name", SHIPS)
def test_real_geometry_tiny_slice_and_json_preserve_explicit_ship_type(name, enabled):
    with rule_freeze_v1(enabled=enabled):
        vessel = fleet.load_vessel(name, with_hull=False)
        tiny = slice_preset(vessel, "tiny")
    assert len(tiny.cells) < len(vessel.cells)
    assert tiny.slice_of == vessel.id
    assert tiny.imdg_ship_type == "closed_holds"
    restored = Vessel.model_validate_json(tiny.model_dump_json())
    assert restored.imdg_ship_type == "closed_holds"


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("stale_type", [None, "hatchless"])
def test_loader_rebuilds_cached_ship_type_that_disagrees_with_raw_declaration(monkeypatch, enabled, stale_type):
    payload = fleet._parameter_bytes("feeder_500")
    with rule_freeze_v1(enabled=enabled):
        stale = fleet._build_vessel(payload, "feeder_500", False).model_copy(
            update={"imdg_ship_type": stale_type})
        monkeypatch.setattr(fleet_cache, "cached_vessel", lambda *args, **kwargs: stale)
        actual = fleet.load_vessel("feeder_500", with_hull=False)
    assert actual.imdg_ship_type == "closed_holds"
    assert actual is not stale
    assert stale.imdg_ship_type == stale_type


@pytest.mark.parametrize("enabled", [False, True])
def test_loader_rebuilds_stale_declaration_when_authoritative_raw_type_is_absent(monkeypatch, enabled):
    original = fleet._parameter_bytes("feeder_500")
    missing, count = re.subn(rb'^imdg_ship_type = [^\n]*\n', b"", original, flags=re.M)
    assert count == 1
    with rule_freeze_v1(enabled=enabled):
        stale = fleet._build_vessel(original, "feeder_500", False)
        assert stale.imdg_ship_type == "closed_holds"
        monkeypatch.setattr(fleet, "_parameter_bytes", lambda name: missing)
        monkeypatch.setattr(fleet_cache, "cached_vessel", lambda *args, **kwargs: stale)
        actual = fleet.load_vessel("feeder_500", with_hull=False)
    assert actual.imdg_ship_type is None
    assert "imdg_ship_type" not in json.loads(actual.model_dump_json())
    assert actual is not stale
    assert stale.imdg_ship_type == "closed_holds"
