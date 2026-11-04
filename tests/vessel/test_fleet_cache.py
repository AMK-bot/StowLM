"""Cached vessel JSON preserves physics and isolates mutable model instances."""

import re

import pytest

from stowage.model.fixtures import build_instance
from stowage.vessel import fleet_cache
from stowage.vessel.fleet import load_vessel
from stowage.vessel.fleet_slices import load_fleet_preset, slice_preset


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("STOWAGE_VESSEL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("STOWAGE_VESSEL_CACHE", raising=False)
    fleet_cache.clear_vessel_memo()
    yield
    fleet_cache.clear_vessel_memo()


@pytest.fixture
def cheap_physics(monkeypatch):
    from stowage.checker import strength_limits
    from stowage.vessel import hull, tanks

    fixture = build_instance().vessel
    calls = []

    def build(params):
        calls.append(params["id"])
        return fixture.hull.model_copy(deep=True)

    monkeypatch.setattr(hull, "build_hull_model", build)
    monkeypatch.setattr(tanks, "build_tanks", lambda params: [])
    monkeypatch.setattr(strength_limits, "permissible_stations",
                        lambda params, model: fixture.hull.stations)
    return calls


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800", "feeder_1700"])
def test_cached_and_uncached_full_vessel_json_identical(monkeypatch, cheap_physics, name):
    monkeypatch.setenv("STOWAGE_VESSEL_CACHE", "0")
    uncached = load_vessel(name).model_dump_json()
    assert not fleet_cache._MEMO
    assert not list(fleet_cache._cache_dir().glob("*.json"))
    monkeypatch.delenv("STOWAGE_VESSEL_CACHE")
    cold = load_vessel(name)
    assert cold.model_dump_json() == uncached
    cold.hull.stations[0].max_shear_n *= 2
    cold.cells[0].reefer_plug = not cold.cells[0].reefer_plug
    cold.dg_doc_areas[0].sources.append("caller-local mutation")
    cold.dg_doc_areas[0].packing_forms_allowed.clear()
    assert load_vessel(name).model_dump_json() == uncached
    assert len(cheap_physics) == 2
    assert fleet_cache._MEMO
    fleet_cache.clear_vessel_memo()
    assert load_vessel(name).model_dump_json() == uncached
    assert len(cheap_physics) == 2  # A fresh process would read the same disk JSON.


def test_explicit_bypass_ignores_existing_disk_and_memo(monkeypatch, cheap_physics):
    load_vessel("feeder_500")
    monkeypatch.setenv("STOWAGE_VESSEL_CACHE", "0")
    load_vessel("feeder_500")
    load_vessel("feeder_500")
    assert len(cheap_physics) == 3


def test_corrupt_disk_cache_rebuilds(cheap_physics):
    expected = load_vessel("feeder_500").model_dump_json()
    paths = list(fleet_cache._cache_dir().glob("*.json"))
    assert len(paths) == 1
    paths[0].write_text("{broken JSON", encoding="utf-8")
    fleet_cache.clear_vessel_memo()
    assert load_vessel("feeder_500").model_dump_json() == expected
    assert len(cheap_physics) == 2
    fleet_cache.clear_vessel_memo()
    assert load_vessel("feeder_500").model_dump_json() == expected
    assert len(cheap_physics) == 2


def test_key_covers_request_parameters_versions_and_source(monkeypatch, tmp_path):
    source = tmp_path / "source.py"
    source.write_text("version = 1\n", encoding="utf-8")
    monkeypatch.setattr(fleet_cache, "_source_files", lambda: [source])
    monkeypatch.setattr(fleet_cache.versions, "SCHEMA_VERSION", "1.0")
    key = lambda name="feeder_500", size="full", hull=True, payload=b"params": fleet_cache._cache_key(
        name, size, hull, payload)
    baseline = key()
    assert len({baseline, key(name="feeder_800"), key(size="tiny"),
                key(hull=False), key(payload=b"changed params")}) == 5
    source.write_text("version = 2\n", encoding="utf-8")
    source_changed = key()
    assert source_changed != baseline
    monkeypatch.setattr(fleet_cache.versions, "SCHEMA_VERSION", "2.0")
    assert key() != source_changed
    monkeypatch.setattr(fleet_cache, "repo_root", lambda: tmp_path)
    profile = tmp_path / "params/cargo/feeder_export.toml"
    profile.parent.mkdir(parents=True)
    profile.write_text('profile = "C3-v1"\n')
    profile_key = key()
    profile.write_text('profile = "C3-v2"\n')
    assert key() != profile_key
    harness = tmp_path / "experiments/physics-review/design/harness.py"
    harness.parent.mkdir(parents=True)
    harness.write_text('CONDITION_VERSION = 1\n')
    harness_key = key()
    harness.write_text('CONDITION_VERSION = 2\n')
    assert key() == harness_key  # C3 policy is ported code, not runtime harness execution.


def test_presets_derive_from_one_cached_full_vessel(monkeypatch, cheap_physics):
    full = load_vessel("feeder_500")
    for size in ("tiny", "small", "medium", "full"):
        cached = load_fleet_preset("feeder_500", size)
        expected = slice_preset(full, size).model_dump_json()
        assert cached.model_dump_json() == expected
        cached.cells[0].reefer_plug = not cached.cells[0].reefer_plug
        assert load_fleet_preset("feeder_500", size).model_dump_json() == expected
        fleet_cache.clear_vessel_memo()
        assert load_fleet_preset("feeder_500", size).model_dump_json() == expected
    assert len(cheap_physics) == 1
    monkeypatch.setenv("STOWAGE_VESSEL_CACHE", "0")
    assert load_fleet_preset("feeder_500", "tiny").model_dump_json() == slice_preset(full, "tiny").model_dump_json()
    assert len(cheap_physics) == 2


def test_design_parameter_change_rebuilds_full_and_slice_cache(monkeypatch, cheap_physics):
    from stowage.vessel import fleet

    original = fleet._parameter_bytes("feeder_500")
    revised = re.sub(rb"(?m)^(lightship_mass_kg\s*=\s*)([0-9.eE+-]+)\s*$",
                     lambda match: match[1] + str(float(match[2]) + 1.0).encode(),
                     original, count=1)
    assert revised != original
    active = [original]
    monkeypatch.setattr(fleet, "_parameter_bytes", lambda name: active[0])
    before = load_fleet_preset("feeder_500", "tiny").model_dump_json()
    fleet_cache.clear_vessel_memo()
    assert load_fleet_preset("feeder_500", "tiny").model_dump_json() == before
    assert len(cheap_physics) == 1
    active[0] = revised
    after = load_fleet_preset("feeder_500", "tiny").model_dump_json()
    assert after != before  # Updated parameter SHA is retained in provenance.
    assert len(cheap_physics) == 2
    assert len(list(fleet_cache._cache_dir().glob("*.json"))) == 4
    fleet_cache.clear_vessel_memo()
    assert load_fleet_preset("feeder_500", "tiny").model_dump_json() == after
    active[0] = original
    assert load_fleet_preset("feeder_500", "tiny").model_dump_json() == before
    assert len(cheap_physics) == 2


@pytest.mark.parametrize("cache_layer", ["memo", "disk"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_hull_cache_hit_revalidates_pinned_offsets(monkeypatch, tmp_path, cheap_physics,
                                                   cache_layer, damage):
    from stowage.params import repo_root
    from stowage.vessel import fleet
    import tomllib

    original = fleet._parameter_bytes("feeder_500")
    form = tomllib.loads(original.decode())["hull_form"]
    offsets = tmp_path / "kcs.fix"
    offsets.write_bytes((repo_root() / form["offsets_path"]).read_bytes())
    revised = original.replace(form["offsets_path"].encode(), str(offsets).encode())
    monkeypatch.setattr(fleet, "_parameter_bytes", lambda name: revised)
    load_vessel("feeder_500")
    if cache_layer == "disk":
        fleet_cache.clear_vessel_memo()
    if damage == "missing":
        offsets.unlink()
        expected_error = FileNotFoundError
    else:
        offsets.write_bytes(b"corrupted offsets")
        expected_error = ValueError
    with pytest.raises(expected_error):
        load_vessel("feeder_500")
    assert len(cheap_physics) == 1


def test_geometry_cache_does_not_require_offsets_source(monkeypatch, tmp_path):
    from stowage.vessel import fleet
    import tomllib

    original = fleet._parameter_bytes("feeder_500")
    form = tomllib.loads(original.decode())["hull_form"]
    revised = original.replace(form["offsets_path"].encode(), str(tmp_path / "missing.fix").encode())
    monkeypatch.setattr(fleet, "_parameter_bytes", lambda name: revised)
    assert load_vessel("feeder_500", with_hull=False).hull is None
    assert load_vessel("feeder_500", with_hull=False).hull is None


@pytest.mark.parametrize("cache_layer", ["memo", "disk"])
def test_unrelated_component_bumps_reuse_built_vessel(monkeypatch, cheap_physics, cache_layer):
    from stowage import versions

    expected = load_vessel("feeder_500").model_dump_json()
    if cache_layer == "disk":
        fleet_cache.clear_vessel_memo()
    monkeypatch.setattr(versions, "CHECKER_VERSION", "unrelated-checker-bump")
    monkeypatch.setattr(versions, "BALLAST_VERSION", "unrelated-ballast-bump")
    assert load_vessel("feeder_500").model_dump_json() == expected
    assert len(cheap_physics) == 1


def test_cache_sources_include_builders_not_global_versions_or_strength_checks():
    from stowage.params import repo_root

    paths = {path.relative_to(repo_root() / "src/stowage").as_posix()
             for path in fleet_cache._source_files()}
    assert "versions.py" not in paths
    assert "checker/strength_assessment.py" not in paths
    assert "checker/strength.py" not in paths
    assert {"checker/strength_limits.py", "model/vessel.py", "model/base.py",
            "model/provenance.py", "vessel/hull.py", "vessel/fleet.py"} <= paths


def test_vessel_builder_epoch_invalidates_cache_key(monkeypatch):
    before = fleet_cache._cache_key("feeder_500", "full", True, b"params")
    monkeypatch.setattr(fleet_cache, "VESSEL_BUILDER_VERSION", "next-builder", raising=False)
    assert fleet_cache._cache_key("feeder_500", "full", True, b"params") != before


def test_booking_reference_content_invalidates_built_vessel_key(monkeypatch):
    """D48 reference construction is a built-vessel dependency (D30)."""
    from pathlib import Path

    target = Path(fleet_cache.__file__).with_name('fleet_booking.py')
    real_read = Path.read_bytes
    content = [b'booking reference implementation one']
    monkeypatch.setattr(Path, 'read_bytes', lambda path: content[0] if path == target else real_read(path))
    first = fleet_cache._cache_key('feeder_500', 'medium', True, b'pinned parameters')
    content[0] = b'booking reference implementation two'
    assert fleet_cache._cache_key('feeder_500', 'medium', True, b'pinned parameters') != first


@pytest.mark.parametrize('relative', ['vessel/fleet_voyage.py', 'model/voyage.py'])
def test_voyage_policy_content_invalidates_built_vessel_key(monkeypatch, relative):
    """D50 policy construction and validation are real vessel dependencies."""
    from pathlib import Path

    target = Path(fleet_cache.__file__).resolve().parents[1] / relative
    real_read = Path.read_bytes
    content = [b'voyage policy implementation one']
    monkeypatch.setattr(Path, 'read_bytes', lambda path: content[0] if path == target else real_read(path))
    first = fleet_cache._cache_key('feeder_500', 'medium', True, b'pinned parameters')
    content[0] = b'voyage policy implementation two'
    assert fleet_cache._cache_key('feeder_500', 'medium', True, b'pinned parameters') != first


@pytest.mark.parametrize('relative', [
    'vessel/fleet_manual.py', 'vessel/fleet_lashing_design.py', 'vessel/fleet_manual_witness.py',
    'ports/profiles.py', 'model/port.py', 'model/ship.py',
])
def test_lashing_manual_content_invalidates_built_vessel_key(monkeypatch, relative):
    """D56 values depend on recipes, corrected GM, and resolved port density."""
    from pathlib import Path

    target = Path(fleet_cache.__file__).resolve().parents[1] / relative
    real_read = Path.read_bytes
    content = [b'D56 construction input one']
    monkeypatch.setattr(Path, 'read_bytes', lambda path: content[0] if path == target else real_read(path))
    first = fleet_cache._cache_key('feeder_500', 'full', True, b'parameters')
    content[0] = b'D56 construction input two'
    assert fleet_cache._cache_key('feeder_500', 'full', True, b'parameters') != first


@pytest.mark.parametrize('profile', ['laem_chabang_like', 'small_feeder'])
def test_loading_manual_density_content_invalidates_built_vessel_key(monkeypatch, profile):
    from pathlib import Path
    from stowage.params import repo_root

    target = repo_root() / f'params/ports/{profile}.toml'
    real_read = Path.read_bytes
    content = [b'D34 loading manual dock density one']
    monkeypatch.setattr(Path, 'read_bytes', lambda path: content[0] if path == target else real_read(path))
    first = fleet_cache._cache_key('feeder_500', 'full', True, b'parameters')
    content[0] = b'D34 loading manual dock density two'
    assert fleet_cache._cache_key('feeder_500', 'full', True, b'parameters') != first
