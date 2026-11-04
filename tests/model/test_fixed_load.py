"""Background loads retain mass compatibility and explicit cargo geometry."""

import json

import pytest
from pydantic import ValidationError

from stowage.io import load_instance, save_instance
from stowage.model import FixedLoad, FixedLoadSilhouette, Instance
from stowage.model.fixtures import build_instance


def _box(**changes):
    return dict(x_min_m=77.0, x_max_m=83.0, y_min_m=-1.3, y_max_m=1.3,
                z_min_m=2.0, z_max_m=6.0) | changes


@pytest.mark.parametrize("legacy", [(80.0, 0.0, 4.0, 25000.0), [80.0, 0.0, 4.0, 25000.0]])
def test_legacy_fixed_load_keeps_mass_api(legacy):
    load = FixedLoad.model_validate(legacy)
    assert load.kind == "unknown"
    assert load.silhouette is None
    x, y, z, mass = load
    assert (x, y, z, mass) == tuple(legacy)
    assert len(load) == 4
    assert load[0] == x
    assert load[-1] == mass
    assert tuple(load[1:3]) == (y, z)
    assert sum(item[3] for item in [load]) == mass


def test_legacy_json_migrates_to_canonical_objects(tmp_path):
    payload = build_instance().model_dump(mode="json")
    payload["schema_version"] = "1.2"
    payload["vessel"]["fixed_loads"] = [[80.0, 0.0, 4.0, 25000.0]]
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(payload), encoding="utf-8")
    instance = load_instance(legacy)
    load = instance.vessel.fixed_loads[0]
    assert isinstance(load, FixedLoad)
    assert load.kind == "unknown" and load.silhouette is None
    first = save_instance(instance, tmp_path / "first.json")
    serialized = json.loads(first.read_text())["vessel"]["fixed_loads"][0]
    assert serialized == dict(x_m=80.0, y_m=0.0, z_m=4.0, mass_kg=25000.0,
                              kind="unknown", silhouette=None, cargo_boxes=None)
    restored = load_instance(first)
    assert restored == instance
    assert first.read_bytes() == save_instance(restored, tmp_path / "second.json").read_bytes()


@pytest.mark.parametrize("kind", ["cargo", "other"])
@pytest.mark.parametrize("has_silhouette", [False, True])
def test_structured_fixed_load_round_trip(tmp_path, kind, has_silhouette):
    payload = build_instance(layover=True).model_dump(mode="json")
    geometry = _box() if has_silhouette else None
    payload["vessel"]["fixed_loads"] = [dict(x_m=80.0, y_m=0.0, z_m=4.0,
                                          mass_kg=25000.0, kind=kind, silhouette=geometry)]
    instance = Instance.model_validate(payload)
    load = instance.vessel.fixed_loads[0]
    assert load.kind == kind
    if has_silhouette:
        assert isinstance(load.silhouette, FixedLoadSilhouette)
        assert load.silhouette.model_dump() == geometry
    else:
        assert load.silhouette is None
    first = save_instance(instance, tmp_path / "first.json")
    restored = load_instance(first)
    assert restored == instance
    assert first.read_bytes() == save_instance(restored, tmp_path / "second.json").read_bytes()


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("delta", [0.0, -1.0])
def test_silhouette_requires_positive_extent(axis, delta):
    box = _box()
    box[f"{axis}_max_m"] = box[f"{axis}_min_m"] + delta
    with pytest.raises(ValidationError):
        FixedLoadSilhouette.model_validate(box)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["x_m", "y_m", "z_m", "mass_kg"])
def test_fixed_load_rejects_nonfinite_values(field, value):
    payload = dict(x_m=80.0, y_m=0.0, z_m=4.0, mass_kg=25000.0) | {field: value}
    with pytest.raises(ValidationError, match=field):
        FixedLoad.model_validate(payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", list(_box()))
def test_silhouette_rejects_nonfinite_bounds(field, value):
    with pytest.raises(ValidationError, match=field):
        FixedLoadSilhouette.model_validate(_box(**{field: value}))


def test_fixed_load_mass_and_kind_constraints():
    with pytest.raises(ValidationError, match="mass_kg"):
        FixedLoad(x_m=0, y_m=0, z_m=0, mass_kg=-1)
    assert FixedLoad(x_m=0, y_m=0, z_m=0, mass_kg=0).mass_kg == 0
    with pytest.raises(ValidationError, match="kind"):
        FixedLoad(x_m=0, y_m=0, z_m=0, mass_kg=1, kind="ballast")
