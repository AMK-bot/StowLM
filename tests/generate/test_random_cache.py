"""The random generator must retain the fleet builder's input validation.

Hull numerics are replaced by a cheap model; fleet loading, source validation
and both layers of the content-addressed vessel cache are real.
"""
from __future__ import annotations

import re
import tomllib

import pytest

from stowage.generate import random_yard
from stowage.model.fixtures import build_instance
from stowage.params import repo_root
from stowage.vessel import fleet, fleet_cache, hull, tanks
from stowage.checker import strength_limits


@pytest.fixture
def cached_full_vessel(monkeypatch, tmp_path):
    # Compatibility with the original wrapper lets these tests fail on its
    # stale hits, rather than contaminate each other with another test's hit.
    clear = getattr(random_yard._full_vessel, "cache_clear", lambda: None)
    clear()
    fleet_cache.clear_vessel_memo()
    monkeypatch.setenv("STOWAGE_VESSEL_CACHE_DIR", str(tmp_path / "vessels"))
    monkeypatch.delenv("STOWAGE_VESSEL_CACHE", raising=False)
    fixture = build_instance().vessel
    calls = []

    def build(params):
        calls.append(params["id"])
        return fixture.hull.model_copy(deep=True)

    monkeypatch.setattr(hull, "build_hull_model", build)
    monkeypatch.setattr(tanks, "build_tanks", lambda params: [])
    monkeypatch.setattr(strength_limits, "permissible_stations",
                        lambda params, model: fixture.hull.stations)
    yield calls
    clear()
    fleet_cache.clear_vessel_memo()


def test_full_vessel_refreshes_changed_fleet_parameters(monkeypatch, cached_full_vessel):
    original = fleet._parameter_bytes("feeder_500")
    revised = re.sub(rb"(?m)^(lightship_mass_kg\s*=\s*)([0-9.eE+-]+)\s*$",
                     lambda match: match[1] + str(float(match[2]) + 1).encode(),
                     original, count=1)
    assert revised != original
    active = [original]
    monkeypatch.setattr(fleet, "_parameter_bytes", lambda name: active[0])
    before = random_yard._full_vessel("feeder_500").model_dump_json()
    active[0] = revised
    after = random_yard._full_vessel("feeder_500").model_dump_json()
    assert after != before  # Parameter provenance must reflect the new source.
    assert len(cached_full_vessel) == 2
    active[0] = original
    assert random_yard._full_vessel("feeder_500").model_dump_json() == before
    assert len(cached_full_vessel) == 2  # Underlying content-key hit remains useful.


def test_full_vessel_refreshes_changed_dependency_version(monkeypatch, cached_full_vessel):
    versions = ["test-v1"]
    actual_version = fleet_cache.version
    monkeypatch.setattr(fleet_cache, "version", lambda name:
                        versions[0] if name == "scipy" else actual_version(name))
    random_yard._full_vessel("feeder_500")
    versions[0] = "test-v2"
    random_yard._full_vessel("feeder_500")
    assert len(cached_full_vessel) == 2


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_full_vessel_revalidates_physics_source(monkeypatch, tmp_path,
                                              cached_full_vessel, damage):
    original = fleet._parameter_bytes("feeder_500")
    form = tomllib.loads(original.decode())["hull_form"]
    source = tmp_path / "offsets.json"
    source.write_bytes((repo_root() / form["offsets_path"]).read_bytes())
    revised = original.replace(form["offsets_path"].encode(), str(source).encode())
    monkeypatch.setattr(fleet, "_parameter_bytes", lambda name: revised)
    random_yard._full_vessel("feeder_500")
    if damage == "missing":
        source.unlink()
        error = FileNotFoundError
    else:
        source.write_bytes(b"corrupted offsets")
        error = ValueError
    with pytest.raises(error):
        random_yard._full_vessel("feeder_500")
    assert len(cached_full_vessel) == 1
