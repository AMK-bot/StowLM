"""Exact polygon/Gauss upright inversion, cache reuse and D30 input identity."""
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.optimize import brentq

import stowage.linear.upright_mass as module
from stowage.linear.physics import _geometry_from_params
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.hydrostatics import HullGeometry


@pytest.mark.parametrize('name', ['feeder_500', 'feeder_800', 'feeder_1700'])
def test_production_mass_and_inverse_equal_direct_geometry(name):
    geometry = _geometry_from_params(load_ship_params(name))
    upright = module.upright_mass(geometry)
    cap = load_ship_params(name)['hull']['scantling_draught_m']
    direct_cap = geometry.submerged(cap, cap, 0., 1025.).displacement_kg
    assert upright.capped_mass(geometry, cap, 1025.) <= direct_cap
    # Include polygon-vertex levels and almost-horizontal edge intervals.
    vertices = np.unique(np.concatenate([p[:, 1] for s in geometry.sections for p in s]))
    draughts = np.unique(np.r_[np.linspace(.001, geometry.max_z_m-.001, 7),
                               vertices[np.linspace(0, len(vertices)-1, 8, dtype=int)]])
    for density in (1000., 1025.):
        for draught in draughts:
            expected = geometry.submerged(draught, draught, 0., density).displacement_kg
            assert upright.mass(draught, density) == pytest.approx(expected, rel=2e-12, abs=1e-6)
    for draught in np.linspace(2., geometry.max_z_m-1., 3):
        mass = geometry.submerged(draught, draught, 0., 1025.).displacement_kg
        direct = brentq(lambda d: geometry.submerged(d, d, 0., 1025.).displacement_kg-mass,
                        .001, geometry.max_z_m-.001)
        assert upright.draught(mass, 1025., geometry.max_z_m) == pytest.approx(direct, abs=2e-11)


def _prism(width=4., weight=10.):
    geometry = HullGeometry.__new__(HullGeometry)
    geometry.sections = [[np.array([[-width/2, 0.], [width/2, 0.], [width/2, 5.], [-width/2, 5.]])]]
    geometry.integration_weights_m = np.array([weight])
    return geometry


def test_cap_intersects_clipped_endpoint_and_only_integrates_once(monkeypatch):
    upright = module.UprightMass(module._build(_prism()))
    polynomial = upright.mass(3., 1025.)
    clipped = np.nextafter(polynomial, -np.inf)
    calls = []
    geometry = SimpleNamespace(submerged=lambda *args:
        calls.append(args) or SimpleNamespace(displacement_kg=clipped))
    assert upright.capped_mass(geometry, 3., 1025.) == clipped
    assert upright.capped_mass(geometry, 3., 1025.) == clipped
    assert len(calls) == 1


def test_disk_reuse_ignores_background_density_and_global_versions(monkeypatch, tmp_path):
    import stowage.linear.cache as cache
    import stowage.versions as versions
    monkeypatch.setattr(cache, '_DEFAULT_ROOT', tmp_path)
    module._MEMORY.clear()
    geometry = _prism()
    first = module.upright_mass(geometry)
    assert first.mass(3., 1000.) == 120000.
    geometry.fixed_loads = ['background is not a volume input']
    geometry.density_kg_m3 = 999.
    monkeypatch.setattr(versions, 'CHECKER_VERSION', 'unrelated')
    key = module._key(geometry)
    module._MEMORY.clear()
    monkeypatch.setattr(module, '_build', lambda _: pytest.fail('valid disk coefficients rebuilt'))
    restored = module.upright_mass(geometry)
    assert restored.mass(3., 1025.) == 123000.
    assert module._key(geometry) == key


def test_key_uses_only_numerical_dependencies_and_builder_version(monkeypatch):
    geometry = _prism()
    key = module._key(geometry)
    geometry.x_m = np.array([123.])  # Upright volume has no x dependence.
    assert module._key(geometry) == key
    assert module._key(_prism(width=5.)) != key
    assert module._key(_prism(weight=11.)) != key
    monkeypatch.setattr(module, 'UPRIGHT_MASS_VERSION', 'next')
    assert module._key(geometry) != key


def test_custom_geometry_keeps_public_submerged_semantics():
    assert module.upright_mass(SimpleNamespace()) is None
    class Custom(HullGeometry):
        pass
    assert module.upright_mass(Custom.__new__(Custom)) is None


def test_corrupt_payload_rebuilds(monkeypatch, tmp_path):
    import stowage.linear.cache as cache
    monkeypatch.setattr(cache, '_DEFAULT_ROOT', tmp_path)
    module._MEMORY.clear()
    geometry = _prism()
    cache.store_payload(module._key(geometry), {'knots': np.zeros((1, 2))})
    assert module.upright_mass(geometry).mass(2., 1000.) == 80000.


def test_instance_band_endpoints_equal_direct_clipping(monkeypatch):
    from stowage.linear.bands import band_for_instance
    from stowage.model.fixtures import build_instance
    geometry = _geometry_from_params(load_ship_params('feeder_500'))
    monkeypatch.setattr('stowage.linear.physics.geometry_for', lambda _: geometry)
    instance = build_instance()
    fast = band_for_instance(instance)
    monkeypatch.setattr(module, 'upright_mass', lambda _: None)
    direct = band_for_instance(instance)
    assert fast.displacement_min_kg == pytest.approx(direct.displacement_min_kg, abs=1e-6)
    assert fast.displacement_max_kg == pytest.approx(direct.displacement_max_kg, abs=1e-6)
    assert fast.draught_min_m == pytest.approx(direct.draught_min_m, abs=2e-11)
    assert fast.draught_max_m == pytest.approx(direct.draught_max_m, abs=2e-11)
    assert fast._tank_mass_bounds_kg == direct._tank_mass_bounds_kg
