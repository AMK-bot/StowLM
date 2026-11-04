"""Contract fixtures, deterministic round trips and malformed-instance rejection."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from stowage.io import SchemaVersionError, load_instance, save_instance
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.model import fixtures as fixture_builder

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"


@pytest.mark.parametrize("layover,name", [(False, "empty_ship"), (True, "layover")])
def test_fixture_round_trip(tmp_path, layover, name):
    source = FIXTURES / f"{name}.json"
    fixture = load_instance(source)
    first = save_instance(fixture, tmp_path / "first.json")
    reloaded = load_instance(first)
    assert reloaded == fixture
    second = save_instance(reloaded, tmp_path / "second.json")
    assert first.read_bytes() == second.read_bytes() == source.read_bytes()
    assert bool(fixture.onboard.placements) is layover
    assert bool(fixture.discharge_list) is layover


@pytest.mark.parametrize("layover,name", [(False, "empty_ship"), (True, "layover")])
def test_builder_matches_fixture_after_component_version_bump(monkeypatch, layover, name):
    fixture = load_instance(FIXTURES / f"{name}.json")
    recorded_versions = fixture.provenance.versions.copy()
    # Simulate changes/additions to the live component registry, independently of
    # which versions happen to be installed when this regression test runs.
    live_versions = {key: "999.0-test" for key in recorded_versions}
    live_versions["future_component"] = "1.0-test"
    monkeypatch.setattr(fixture_builder, "component_versions", lambda: live_versions.copy())
    built = build_instance(layover=layover)
    assert built.provenance.versions == live_versions
    assert fixture.provenance.versions == recorded_versions
    # Historical fixtures retain their schema minor just like component versions;
    # current builders use the live schema. Major compatibility and all data still
    # have to agree across an additive schema release.
    assert built.schema_version.split(".")[0] == fixture.schema_version.split(".")[0]
    excluded = {"schema_version": True, "provenance": {"versions"}}
    assert built.model_dump(exclude=excluded) == fixture.model_dump(exclude=excluded)


def test_fixture_builder_independent():
    instance = build_instance()
    instance.load_list.clear()
    assert len(build_instance().load_list) == 4


def test_box_in_two_places_rejected():
    payload = build_instance().model_dump(mode="json")
    payload["onboard"]["placements"] = [dict(slot="0020182", container_id=payload["load_list"][0])]
    with pytest.raises(ValidationError, match="(?i)(multiple|two|duplicate|both|more than once)"):
        Instance.model_validate(payload)


def test_unknown_slot_rejected():
    payload = build_instance(layover=True).model_dump(mode="json")
    payload["onboard"]["placements"][0]["slot"] = "0990102"
    with pytest.raises(ValidationError, match="(?i)(slot|cell)"):
        Instance.model_validate(payload)


def test_unresolved_pod_rejected():
    payload = build_instance().model_dump(mode="json")
    payload["containers"][0]["pod"] = "SGSIN"
    with pytest.raises(ValidationError, match="(?i)(POD|discharge|rotation|call)"):
        Instance.model_validate(payload)


def test_bad_check_digit_rejected():
    payload = build_instance().model_dump(mode="json")
    cid = payload["containers"][0]["id"]
    payload["containers"][0]["id"] = cid[:-1] + str((int(cid[-1]) + 1) % 10)
    with pytest.raises(ValidationError, match="(?i)(check.?digit|6346)"):
        Instance.model_validate(payload)


def test_wrong_schema_major_rejected(tmp_path):
    payload = build_instance().model_dump(mode="json")
    payload["schema_version"] = "999.0.0"
    path = tmp_path / "future.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SchemaVersionError, match="(?i)(major|version|schema)"):
        load_instance(path)
