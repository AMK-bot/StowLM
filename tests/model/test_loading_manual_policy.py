"""D40 loading-manual policy keeps missing values unknown and retains evidence."""

import json

import pytest
from pydantic import ValidationError

import stowage.model as model
from stowage.frozen import StubTaintError
from stowage.io import load_instance, save_instance
from stowage.model import HullModel, Instance
from stowage.model.fixtures import build_instance


POLICY_FIELDS = (
    "min_trim_m", "max_trim_m", "min_displacement_kg", "max_displacement_kg",
    "bilge_keel_area_m2",
)


def _hull_payload(**changes):
    payload = build_instance().model_dump(mode="json")["vessel"]["hull"]
    for field in POLICY_FIELDS:
        payload.pop(field, None)
        payload.pop(f"{field}_provenance", None)
    return payload | changes


@pytest.mark.parametrize("lower,upper", [(-0.7, 1.2), (-1.2, -0.2), (0.2, 1.2), (0.0, 0.0)])
def test_signed_trim_policy_preserves_asymmetric_and_one_sided_bounds(lower, upper):
    hull = HullModel.model_validate(_hull_payload(min_trim_m=lower, max_trim_m=upper))
    assert hull.min_trim_m == lower
    assert hull.max_trim_m == upper


@pytest.mark.parametrize("lower,upper", [(1.0, 2.0), (1.0, 1.0)])
def test_displacement_policy_accepts_ordered_positive_bounds(lower, upper):
    hull = HullModel.model_validate(_hull_payload(min_displacement_kg=lower, max_displacement_kg=upper))
    assert hull.min_displacement_kg == lower
    assert hull.max_displacement_kg == upper


@pytest.mark.parametrize("lower,upper", [
    ("min_trim_m", "max_trim_m"),
    ("min_displacement_kg", "max_displacement_kg"),
])
def test_loading_manual_policy_rejects_reversed_bounds(lower, upper):
    with pytest.raises(ValidationError, match=lower):
        HullModel.model_validate(_hull_payload(**{lower: 2.0, upper: 1.0}))


@pytest.mark.parametrize("field", POLICY_FIELDS)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_loading_manual_policy_rejects_nonfinite_values(field, value):
    with pytest.raises(ValidationError, match=field):
        HullModel.model_validate(_hull_payload(**{field: value}))


@pytest.mark.parametrize("field", POLICY_FIELDS)
@pytest.mark.parametrize("value", [False, True])
def test_loading_manual_policy_rejects_boolean_numbers(field, value):
    with pytest.raises(ValidationError, match=field):
        HullModel.model_validate(_hull_payload(**{field: value}))


@pytest.mark.parametrize("field", ["min_displacement_kg", "max_displacement_kg"])
@pytest.mark.parametrize("value", [0.0, -1.0])
def test_displacement_policy_requires_strictly_positive_mass(field, value):
    with pytest.raises(ValidationError, match=field):
        HullModel.model_validate(_hull_payload(**{field: value}))


def test_bilge_keel_area_accepts_declared_zero_and_rejects_negative():
    assert HullModel.model_validate(_hull_payload(bilge_keel_area_m2=0.0)).bilge_keel_area_m2 == 0.0
    with pytest.raises(ValidationError, match="bilge_keel_area_m2"):
        HullModel.model_validate(_hull_payload(bilge_keel_area_m2=-0.1))


def test_missing_loading_manual_policy_is_unknown():
    hull = HullModel.model_validate(_hull_payload())
    for field in POLICY_FIELDS:
        assert getattr(hull, field) is None
        assert getattr(hull, f"{field}_provenance") is None


def test_legacy_loading_manual_json_keeps_existing_max_trim_without_evidence(tmp_path):
    payload = build_instance().model_dump(mode="json")
    payload["vessel"]["hull"] = _hull_payload(max_trim_m=1.0)
    path = tmp_path / "legacy-policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    hull = load_instance(path).vessel.hull
    assert hull.max_trim_m == 1.0
    assert hull.min_trim_m is None
    assert hull.max_trim_m_provenance is None
    assert hull.min_displacement_kg is None
    assert hull.max_displacement_kg is None
    assert hull.bilge_keel_area_m2 is None


@pytest.mark.parametrize("field,value,opposite", [
    ("min_trim_m", -0.4, "max_trim_m"),
    ("max_trim_m", 1.4, "min_trim_m"),
    ("min_displacement_kg", 1000.0, "max_displacement_kg"),
    ("max_displacement_kg", 2000.0, "min_displacement_kg"),
])
def test_one_policy_bound_does_not_infer_the_other(field, value, opposite):
    hull = HullModel.model_validate(_hull_payload(**{field: value}))
    assert getattr(hull, field) == value
    assert getattr(hull, opposite) is None


@pytest.mark.parametrize("legacy_flag", [False, True])
def test_bilge_keel_boolean_does_not_imply_area(legacy_flag):
    hull = HullModel.model_validate(_hull_payload(bilge_keel=legacy_flag))
    assert hull.bilge_keel_area_m2 is None


@pytest.mark.parametrize("record", [
    {"source": "params/fleet/synthetic.toml"},
    {"assumption": "APPROXIMATION: synthetic loading-manual interval"},
    {"source": "manual section 4", "assumption": "APPROXIMATION: synthetic policy"},
])
def test_vessel_field_provenance_accepts_source_or_assumption(record):
    evidence = model.VesselFieldProvenance.model_validate(record)
    assert evidence.source == record.get("source")
    assert evidence.assumption == record.get("assumption")
    assert not evidence.provenance.tainted


@pytest.mark.parametrize("record", [
    {}, {"source": None, "assumption": None}, {"source": ""}, {"assumption": " \t"},
    {"source": " ", "assumption": "synthetic policy"},
    {"source": "manual section 4", "assumption": ""},
])
def test_vessel_field_provenance_requires_nonblank_evidence(record):
    with pytest.raises(ValidationError, match="source|assumption"):
        model.VesselFieldProvenance.model_validate(record)


def _full_policy():
    policy = dict(min_trim_m=-0.4, max_trim_m=1.1, min_displacement_kg=1000.0,
                  max_displacement_kg=2000.0, bilge_keel_area_m2=0.0)
    for field in POLICY_FIELDS:
        policy[f"{field}_provenance"] = dict(source=f"synthetic loading manual: {field}",
                                            assumption="APPROXIMATION: declared synthetic policy")
    return policy


def test_loading_manual_policy_typed_provenance_json_and_io_round_trip(tmp_path):
    payload = build_instance().model_dump(mode="json")
    payload["vessel"]["hull"] = _hull_payload(**_full_policy())
    instance = Instance.model_validate(payload)
    hull = instance.vessel.hull
    assert HullModel.model_validate_json(hull.model_dump_json()) == hull
    for field in POLICY_FIELDS:
        assert isinstance(getattr(hull, f"{field}_provenance"), model.VesselFieldProvenance)
    path = save_instance(instance, tmp_path / "loading-policy.json")
    assert load_instance(path) == instance
    serialized = json.loads(path.read_text())["vessel"]["hull"]
    for field, value in _full_policy().items():
        if field.endswith("_provenance"):
            assert serialized[field]["source"] == value["source"]
        else:
            assert serialized[field] == value


@pytest.mark.parametrize("field", POLICY_FIELDS)
def test_loading_manual_nested_evidence_taints_instance_and_blocks_clean_io(tmp_path, field):
    payload = build_instance().model_dump(mode="json")
    policy = _full_policy()
    marker = {f"loading_manual.{field}": "synthetic evidence stub"}
    policy[f"{field}_provenance"]["provenance"] = dict(stubs=marker, parents=["manual-evidence"])
    payload["vessel"]["hull"] = _hull_payload(**policy)
    instance = Instance.model_validate(payload)
    assert instance.provenance.stubs == marker
    assert "manual-evidence" in instance.provenance.parents
    with pytest.raises(StubTaintError, match=field):
        save_instance(instance, tmp_path / "must-not-write.json")
    path = save_instance(instance, tmp_path / "tainted-policy.json", allow_stubs=True)
    assert load_instance(path) == instance
