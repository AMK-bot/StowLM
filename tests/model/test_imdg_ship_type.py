"""D59 declares the IMDG table domain; it never infers one from hatch geometry."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from stowage.io import load_instance, save_instance
from stowage.model import Instance, Vessel
from stowage.rules.catalogue import rule_freeze_v1
from stowage.rules.imdg.ship_type import resolve_ship_type

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"


@pytest.fixture(params=[False, True], autouse=True)
def activation(request):
    """The authored D59 fact and its serialization are independent of this switch."""
    with rule_freeze_v1(enabled=request.param):
        yield


def _payload():
    return dict(id="synthetic", name="Synthetic declared IMDG domain", nominal_teu=0,
                cells=[], stacks=[], hatch_covers=[])


@pytest.mark.parametrize("declared", [None, "closed_holds", "hatchless"])
def test_model_accepts_only_declared_ship_domains_or_unknown(declared):
    vessel = Vessel(**_payload(), imdg_ship_type=declared)
    assert vessel.imdg_ship_type == declared
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel


@pytest.mark.parametrize("invalid", ["", "ro_ro", "closed_holds ", "CLOSED_HOLDS", 1, True, [], {}, b"closed_holds"])
def test_model_rejects_invalid_native_domain(invalid):
    with pytest.raises(ValidationError, match="imdg_ship_type"):
        Vessel(**_payload(), imdg_ship_type=invalid)


def test_missing_and_explicit_null_domain_have_identical_legacy_json():
    absent = Vessel(**_payload())
    explicit_null = Vessel(**_payload(), imdg_ship_type=None)
    assert absent.imdg_ship_type is None
    assert "imdg_ship_type" not in absent.model_dump()
    assert "imdg_ship_type" not in absent.model_dump(mode="json")
    assert absent.model_dump_json() == explicit_null.model_dump_json()
    assert json.loads(absent.model_dump_json()) == absent.model_dump(mode="json")


@pytest.mark.parametrize("name", ["empty_ship", "layover"])
def test_legacy_instance_fixture_bytes_remain_exact_without_rewriting_source(tmp_path, name):
    source = FIXTURES / f"{name}.json"
    before = source.read_bytes()
    instance = load_instance(source)
    assert instance.vessel.imdg_ship_type is None
    assert resolve_ship_type(instance.vessel, {}) is None  # Covers do not declare the domain.
    destination = save_instance(instance, tmp_path / f"{name}.json")
    assert destination.read_bytes() == before
    assert source.read_bytes() == before
    assert load_instance(destination) == instance
    assert "imdg_ship_type" not in instance.model_dump(mode="json")["vessel"]


@pytest.mark.parametrize("declared", ["closed_holds", "hatchless"])
def test_declared_domain_round_trips_in_native_vessel_and_nested_instance(declared):
    payload = json.loads((FIXTURES / "empty_ship.json").read_bytes())
    payload["vessel"]["imdg_ship_type"] = declared
    instance = Instance.model_validate(payload)
    assert instance.model_dump(mode="json")["vessel"]["imdg_ship_type"] == declared
    assert Instance.model_validate_json(instance.model_dump_json()) == instance
    assert Vessel.model_validate_json(instance.vessel.model_dump_json()) == instance.vessel


@pytest.mark.parametrize("native", ["closed_holds", "hatchless"])
def test_existing_reader_consumes_native_fact_and_requires_legacy_agreement(native):
    vessel = Vessel(**_payload(), imdg_ship_type=native)
    before = vessel.model_dump_json()
    assert resolve_ship_type(vessel, {}) == native
    assert resolve_ship_type(vessel, {"ship_type": native}) == native
    conflict = "hatchless" if native == "closed_holds" else "closed_holds"
    legacy = {"ship_type": conflict}
    assert resolve_ship_type(vessel, legacy) is None
    assert legacy == {"ship_type": conflict}
    assert resolve_ship_type(vessel, {"ship_type": None}) is None
    assert vessel.model_dump_json() == before


@pytest.mark.parametrize("legacy,expected", [
    ({}, None), ({"ship_type": "closed_holds"}, "closed_holds"),
    ({"ship_type": "hatchless"}, "hatchless"), ({"ship_type": "ro_ro"}, None),
])
def test_unknown_native_retains_explicit_legacy_reader_semantics(legacy, expected):
    vessel = Vessel(**_payload(), imdg_ship_type=None)
    assert resolve_ship_type(vessel, legacy) == expected
    assert vessel.imdg_ship_type is None


def test_reader_rejects_mutated_invalid_native_even_with_valid_legacy_evidence():
    vessel = Vessel(**_payload(), imdg_ship_type="closed_holds")
    vessel.imdg_ship_type = "ro_ro"  # Models are mutable; a consumer still must fail closed.
    assert resolve_ship_type(vessel, {"ship_type": "closed_holds"}) is None


@pytest.mark.parametrize("far,expected", [(True, True), (False, False)])
def test_actual_pydantic_vessel_closes_native_model_to_checker_segregation(far, expected):
    """Exercise the actual checker with native evidence and no legacy declaration."""
    from stowage.checker import rules_imdg
    from tests.checker.test_rules_imdg import _case

    instance, ship, first, second = _case(2, far=far)
    instance.rule_overrides[rules_imdg.SHIP].pop("ship_type")
    payload = instance.vessel.model_dump(mode="python")
    payload["imdg_ship_type"] = "closed_holds"
    instance.vessel = Vessel.model_validate(payload)
    assert isinstance(instance.vessel, Vessel)
    cells = {cell.id: cell for cell in instance.vessel.cells}
    assert rules_imdg._geometry(instance, first, ship.placements[0].slot, second,
                                ship.placements[1].slot, 2, cells) is expected
    violations = rules_imdg.imdg_violations(instance, ship, call_index=0)
    if expected:
        assert violations == []
    else:
        assert any(violation.rule_id == rules_imdg.SHIP and "level 2" in violation.message
                   for violation in violations)
