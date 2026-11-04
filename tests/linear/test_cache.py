"""Disk caches round-trip inert numerical data and invalidate all dependencies."""
from dataclasses import fields
import json
import subprocess
import sys

import numpy as np
import pytest

from stowage.linear.cache import (cache_key, load_model, load_payload, store_model,
                                  store_payload, vessel_cache_key)
from stowage.linear.model import LinearErrors, LinearModel, ValidityBand


def key(**changes):
    args = dict(vessel_id="synthetic", params={"beam": 10.}, inputs={"cargo": 20.},
                band=ValidityBand(1., 2., 1., 2., .1, .1), density=1025.,
                versions={"linear": "1"}, source_hash="abc")
    args.update(changes)
    return cache_key("model", **args)


def tiny_model():
    zeros = np.zeros(1)
    values = dict(vessel_id="synthetic", version="1", water_density_kg_m3=1025.,
        band=ValidityBand(100., 200., 1., 2., .1, .1),
        errors=LinearErrors(.1, .01, .001, .01, zeros, zeros),
        cell_ids=("0010102",), tank_ids=("ballast",), station_x_m=zeros,
        tank_adjustable=np.ones(1, dtype=bool),
        km_line=(0., 10.), trim_line=(0., 0., 0.), draught_line=(0., .01))
    for field in fields(LinearModel):
        if field.name in values:
            continue
        if field.name.startswith(("cell_", "tank_", "const_sf", "const_bm")) or field.name in (
                "sf_max_n", "sf_min_n", "bm_max_nm", "bm_min_nm"):
            values[field.name] = zeros.copy()
        elif "per_kg" in field.name:
            values[field.name] = np.zeros((1, 1))
        else:
            values[field.name] = 0.
    model = LinearModel(**values)
    object.__setattr__(model, "measurement_diagnostics", {"samples": 192, "max": {"gm_m": .1}})
    object.__setattr__(model, "hull_max_draught_m", 7.)
    object.__setattr__(model, "_fixed_tank_masses", {})
    object.__setattr__(model, "_cargo_mass_kg", 12.)
    object.__setattr__(model, "_manifest_ids", ("synthetic-box",))
    object.__setattr__(model, "_manifest_fingerprint", "manifest-sha")
    object.__setattr__(model, "_tank_mass_bounds_kg", {"ballast": (0., 50.)})
    object.__setattr__(model, "_reference_params", {"beam": {"value": 10., "unit": "m"}})
    return model


def test_canonical_key_and_dependency_invalidation():
    assert key(params={"a": 1, "b": 2}) == key(params={"b": 2, "a": 1})
    original = key()
    for changed in (dict(versions={"linear": "2"}), dict(params={"beam": 11.}),
                    dict(inputs={"cargo": 21.}), dict(source_hash="new"),
                    dict(density=1000.), dict(vessel_id="other"),
                    dict(band=ValidityBand(1., 3., 1., 2., .1, .1))):
        assert key(**changed) != original


def test_payload_arrays_and_model_metadata_roundtrip(tmp_path):
    model = tiny_model()
    assert store_model(key(), model, root=tmp_path)
    result = load_model(key(), root=tmp_path)
    assert result is not None
    assert result.cell_ids == model.cell_ids
    assert result.band == model.band
    assert result.km_line == model.km_line
    np.testing.assert_array_equal(result.sf_per_kg_tank, model.sf_per_kg_tank)
    assert result.tank_adjustable.dtype == np.dtype(bool)
    assert not result.cell_lcg_m.flags.writeable
    assert not result.errors.sf_n.flags.writeable
    assert result._reference_params == model._reference_params
    assert result.measurement_diagnostics == model.measurement_diagnostics
    assert result.hull_max_draught_m == 7.
    assert result._fixed_tank_masses == {}
    assert result._cargo_mass_kg == 12.
    assert tuple(result._manifest_ids) == ("synthetic-box",)
    assert result._manifest_fingerprint == "manifest-sha"
    assert result._tank_mass_bounds_kg == {"ballast": [0., 50.]}


def test_coefficient_certificate_binds_inputs_tables_and_own_builder(tmp_path):
    from stowage.linear.cache import _FAMILY_ARRAYS
    from stowage.linear.dependencies import LINEAR_COEFFICIENTS_VERSION
    model = tiny_model()
    for name in _FAMILY_ARRAYS:
        object.__setattr__(model, name, np.arange(3., dtype=float))
    original_key = key()
    assert store_model(original_key, model, root=tmp_path)
    restored = load_model(original_key, root=tmp_path)
    assert restored is not None
    certificate = restored.coefficient_certificate
    assert certificate["status"] == "coefficients_only_not_calibrated"
    assert certificate["inputs_hash"] == original_key
    assert certificate["builder_version"] == LINEAR_COEFFICIENTS_VERSION
    assert certificate["certified_at_utc"].endswith("+00:00")
    assert certificate == model.coefficient_certificate
    assert key() == original_key  # Certificate date is evidence, never a key input.
    payload = load_payload(original_key, namespace="model", root=tmp_path)
    payload["metadata"][_FAMILY_ARRAYS[0]] = payload["metadata"][_FAMILY_ARRAYS[0]].copy()
    payload["metadata"][_FAMILY_ARRAYS[0]][0] += 1.
    assert store_payload(original_key, payload, namespace="model", root=tmp_path)
    assert load_model(original_key, root=tmp_path) is None


@pytest.mark.parametrize("change", ["inputs_hash", "builder_version", "certified_at_utc", "missing"])
def test_coefficient_certificate_invalid_binding_is_cache_miss(tmp_path, change):
    from stowage.linear.cache import _FAMILY_ARRAYS
    model = tiny_model()
    for name in _FAMILY_ARRAYS:
        object.__setattr__(model, name, np.zeros(1))
    assert store_model(key(), model, root=tmp_path)
    payload = load_payload(key(), namespace="model", root=tmp_path)
    if change == "missing":
        del payload["metadata"]["coefficient_certificate"]
    else:
        payload["metadata"]["coefficient_certificate"][change] = "invalid"
    assert store_payload(key(), payload, namespace="model", root=tmp_path)
    assert load_model(key(), root=tmp_path) is None


def test_reusable_vessel_payload_is_separate_from_calibration(tmp_path):
    reusable = vessel_cache_key("synthetic", {"beam": 10.}, inputs={"nx": 48}, density=1025.)
    payload = {"jacobian": np.eye(2), "geometry": {"sections": [np.zeros((4, 2))]}}
    assert store_payload(reusable, payload, namespace="vessel", root=tmp_path)
    assert load_payload(reusable, namespace="model", root=tmp_path) is None
    result = load_payload(reusable, namespace="vessel", root=tmp_path)
    np.testing.assert_array_equal(result["jacobian"], np.eye(2))
    assert result["geometry"]["sections"][0].shape == (4, 2)


@pytest.mark.parametrize("corruption", ["manifest", "array", "missing", "format", "traversal"])
def test_corruption_is_a_cache_miss(tmp_path, corruption):
    assert store_payload(key(), {"a": np.arange(3.)}, root=tmp_path)
    manifest_path = tmp_path / "payload" / f"{key()}.json"
    record = json.loads(manifest_path.read_text())
    array_path = manifest_path.parent / record["file"]
    if corruption == "manifest":
        manifest_path.write_text("not json")
    elif corruption == "array":
        array_path.write_bytes(b"corrupt")
    elif corruption == "missing":
        array_path.unlink()
    elif corruption == "format":
        record["format"] = -1
        manifest_path.write_text(json.dumps(record))
    else:
        record["file"] = "../../outside.npz"
        manifest_path.write_text(json.dumps(record))
    assert load_payload(key(), root=tmp_path) is None


def test_object_arrays_and_nonfinite_values_are_not_cached(tmp_path):
    assert not store_payload(key(), {"a": np.array([object()], dtype=object)}, root=tmp_path)
    assert not store_payload(key(), {"a": np.array([np.nan])}, root=tmp_path)
    assert load_payload(key(), root=tmp_path) is None


def test_invalid_model_shape_is_a_miss(tmp_path):
    model = tiny_model()
    object.__setattr__(model, "sf_per_kg_tank", np.zeros((3, 2)))
    assert store_model(key(), model, root=tmp_path)
    assert load_model(key(), root=tmp_path) is None


def test_model_loads_in_a_fresh_process(tmp_path):
    assert store_model(key(), tiny_model(), root=tmp_path)
    script = "from stowage.linear.cache import load_model; import sys; m=load_model(sys.argv[1],root=sys.argv[2]); assert m is not None and m.measurement_diagnostics['samples']==192 and m._cargo_mass_kg==12."
    result = subprocess.run([sys.executable, "-c", script, key(), str(tmp_path)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_equal_public_bands_with_different_tank_windows_do_not_alias():
    a = ValidityBand(1., 2., 1., 2., .1, .1)
    b = ValidityBand(1., 2., 1., 2., .1, .1)
    object.__setattr__(a, "_tank_mass_bounds_kg", {"port": (0., 10.), "starboard": (10., 20.)})
    object.__setattr__(b, "_tank_mass_bounds_kg", {"port": (10., 20.), "starboard": (0., 10.)})
    assert a == b
    assert key(band=a) != key(band=b)
    object.__setattr__(b, "_tank_mass_bounds_kg", a._tank_mass_bounds_kg)
    assert key(band=a) == key(band=b)
    object.__setattr__(b, "_cargo_mass_kg", 25.)
    assert key(band=a) != key(band=b)


@pytest.mark.parametrize("namespace", ["geometry", "integration"])
def test_builder_numeric_namespaces_roundtrip(tmp_path, namespace):
    payload = {"stations": np.array([1., 2.]), "matrix": np.eye(2)}
    assert store_payload(key(), payload, namespace=namespace, root=tmp_path)
    result = load_payload(key(), namespace=namespace, root=tmp_path)
    assert result is not None
    np.testing.assert_array_equal(result["matrix"], payload["matrix"])
    np.testing.assert_array_equal(result["stations"], payload["stations"])


def test_restored_vessel_geometry_supports_equilibrium_memo(tmp_path, monkeypatch):
    """A restored section grid must rebuild task 2.1's derived geometry digest."""
    from stowage.linear import cache
    from stowage.linear.physics import _geometry_from_params
    from stowage.vessel.fleet import load_ship_params

    monkeypatch.setattr(cache, '_DEFAULT_ROOT', tmp_path)
    params = load_ship_params('feeder_500')
    getattr(_geometry_from_params, '_memory', {}).clear()
    fresh = _geometry_from_params(params)
    # Exercise persisted reconstruction rather than the separate warm memo.
    getattr(_geometry_from_params, '_memory', {}).clear()
    restored = _geometry_from_params(params)
    assert fresh is not restored
    row = restored.hydrostatic_row(restored.design_draught_m)
    condition = restored.equilibrium(row.displacement_kg, row.lcb_m, 0., row.kb_m)
    assert condition.residual_norm < 2e-6
    assert abs(condition.mean_draught_m-row.draught_m) < 1e-5


@pytest.mark.parametrize("kind", ["model", "vessel"])
def test_raw_offset_tampering_is_rejected_before_reusing_a_cache_key(tmp_path, kind):
    import hashlib
    path = tmp_path / "offsets.fix"
    path.write_bytes(b"original pinned public offset bytes")
    params = {"hull_form": {"family": "stowlm_offsets_v1", "offsets_path": str(path),
              "offsets_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    arguments = dict(vessel_id="synthetic", params=params, inputs={},
                     versions={"linear": "2"}, source_hash="fixed-source")
    original = cache_key(kind, **arguments)
    assert store_payload(original, {"matrix": np.eye(2)}, root=tmp_path)
    path.write_bytes(b"changed offset bytes without updating declared pin")
    with pytest.raises(ValueError, match="offset source checksum mismatch"):
        cache_key(kind, **arguments)
    params["hull_form"]["offsets_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    updated = cache_key(kind, **arguments)
    assert updated != original
    assert load_payload(updated, root=tmp_path) is None


def test_relative_offset_path_uses_geometry_repository_root(tmp_path, monkeypatch):
    import hashlib
    import stowage.params
    path = tmp_path / "public.fix"
    path.write_bytes(b"pinned bytes")
    params = {"hull_form": {"family": "stowlm_offsets_v1", "offsets_path": "public.fix",
              "offsets_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    monkeypatch.setattr(stowage.params, "repo_root", lambda: tmp_path)
    assert len(key(params=params)) == 64
    path.unlink()
    with pytest.raises(FileNotFoundError):
        key(params=params)


def test_identical_pinned_offset_relocation_preserves_numerical_keys(tmp_path):
    import hashlib
    from types import SimpleNamespace
    from stowage.linear.cache import response_calibration_identity
    first, second = tmp_path / "source.fix", tmp_path / "relocated.fix"
    first.write_bytes(b"identical checksum-pinned public offsets")
    second.write_bytes(first.read_bytes())
    form = {"family": "stowlm_offsets_v1", "offsets_path": str(first), "parent": "public-label",
            "offsets_sha256": hashlib.sha256(first.read_bytes()).hexdigest(), "reference_beam_m": 32.2}
    old, new = {"hull_form": form}, {"hull_form": {**form, "offsets_path": str(second), "parent": "renamed-label"}}
    for artifact in ("model", "vessel", "coefficient_family", "family_calibration"):
        args = dict(vessel_id="same", inputs={}, source_hash="same-numerics")
        assert cache_key(artifact, params=old, **args) == cache_key(artifact, params=new, **args)
    model = tiny_model()
    family = SimpleNamespace(template=model, parameters=old, **{name:np.zeros(1) for name in
        ('response_sf','response_bm','buoyancy_sf','buoyancy_bm','lightship_sf','lightship_bm')})
    original = response_calibration_identity(family)
    family.parameters = new
    assert response_calibration_identity(family) == original
    second.write_bytes(b"changed bytes, same declared pin")
    with pytest.raises(ValueError, match="offset source checksum mismatch"):
        response_calibration_identity(family)


def test_model_key_includes_production_station_values_and_limits():
    from stowage.linear.cache import model_cache_key
    from stowage.model.fixtures import build_instance
    instance = build_instance()
    original = model_cache_key(instance, None, 64, {})
    station = instance.vessel.hull.stations[0]
    changed = station.model_copy(update={"max_shear_n": station.max_shear_n*1.01})
    hull = instance.vessel.hull.model_copy(update={"stations": [changed, *instance.vessel.hull.stations[1:]]})
    updated = instance.model_copy(update={"vessel": instance.vessel.model_copy(update={"hull": hull})})
    assert model_cache_key(updated, None, 64, {}) != original


def test_model_key_includes_reference_free_surface_method(monkeypatch):
    from stowage.linear.cache import model_cache_key
    from stowage.model.fixtures import build_instance
    instance = build_instance()
    monkeypatch.delenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", raising=False)
    default = model_cache_key(instance, None, 64, {})
    vessel_default = vessel_cache_key(instance.vessel.id, {})
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "1")
    assert model_cache_key(instance, None, 64, {}) == default
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "2")
    assert model_cache_key(instance, None, 64, {}) != default
    assert vessel_cache_key(instance.vessel.id, {}) == vessel_default


@pytest.mark.parametrize("method", ["0", "3", "", "two", "1.5"])
def test_model_key_rejects_invalid_reference_free_surface_method(monkeypatch, method):
    from stowage.linear.cache import model_cache_key
    from stowage.model.fixtures import build_instance
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", method)
    with pytest.raises(ValueError, match="must be 1 or 2"):
        model_cache_key(build_instance(), None, 64, {})
