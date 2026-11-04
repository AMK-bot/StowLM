"""Real production geometry round-trips without constructing hull/KN tables."""
import numpy as np
import pytest

from stowage.linear.physics import _geometry_from_params
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.hydrostatics import HullGeometry


@pytest.fixture(autouse=True)
def clear_geometry_memory():
    getattr(_geometry_from_params,'_memory',{}).clear()
    yield
    getattr(_geometry_from_params,'_memory',{}).clear()


@pytest.mark.parametrize('name', ['feeder_500', 'feeder_800', 'feeder_1700'])
def test_production_geometry_cache_preserves_public_state_and_equilibrium(monkeypatch, name):
    params = load_ship_params(name)
    original = HullGeometry.from_params(params)
    stored = {}
    identity_checks=[]
    def checked_key(*args,**kwargs):
        identity_checks.append(1)
        return 'a'*64
    monkeypatch.setattr('stowage.linear.cache.vessel_cache_key', checked_key)
    monkeypatch.setattr('stowage.linear.cache.load_payload', lambda key, namespace: stored.get(namespace))
    def store(key, payload, *, namespace):
        stored[namespace] = payload
        return True
    monkeypatch.setattr('stowage.linear.cache.store_payload', store)

    fresh = _geometry_from_params(params)
    assert _geometry_from_params(params) is fresh
    assert len(identity_checks)==2  # Warm hits still validate current raw inputs.
    assert not fresh.x_m.flags.writeable
    # Exercise the separate persisted reconstruction as well as the warm path.
    _geometry_from_params._memory.clear()
    def unexpected_build(*args, **kwargs):
        raise AssertionError('valid geometry payload should restore without reconstruction')
    monkeypatch.setattr(HullGeometry, 'from_params', unexpected_build)
    restored = _geometry_from_params(params)
    public = {key for key in vars(original) if not key.startswith('_')}
    assert {key for key in vars(restored) if not key.startswith('_')} == public
    for key in public-{'sections', 'x_m', 'integration_weights_m'}:
        assert getattr(restored, key) == getattr(original, key)
    np.testing.assert_array_equal(restored.x_m, original.x_m)
    np.testing.assert_array_equal(restored.integration_weights_m, original.integration_weights_m)
    for expected_section, actual_section in zip(original.sections, restored.sections, strict=True):
        assert len(expected_section) == len(actual_section)
        for expected, actual in zip(expected_section, actual_section, strict=True):
            np.testing.assert_array_equal(actual, expected)
    assert restored.buoyant_x_min_m < 0 < restored.lpp_m < restored.buoyant_x_max_m
    assert restored.source_sha256 == params['hull_form']['offsets_sha256']
    assert restored._geometry_digest == original._geometry_digest == fresh._geometry_digest

    water = original.submerged(original.design_draught_m, original.design_draught_m)
    args = (water.displacement_kg, water.lcb_m, 0., original.depth_m*.4)
    expected = original.equilibrium(*args)
    actual = restored.equilibrium(*args)
    np.testing.assert_allclose([actual.t_aft_m, actual.t_fwd_m, actual.heel_rad],
                               [expected.t_aft_m, expected.t_fwd_m, expected.heel_rad], atol=1e-10)
    assert actual.submerged.displacement_kg == pytest.approx(water.displacement_kg, rel=1e-8)


def test_old_geometry_payload_missing_production_fields_is_rebuilt(monkeypatch):
    params = load_ship_params('feeder_500')
    source = HullGeometry.from_params(params)
    missing = {'buoyant_x_min_m', 'buoyant_x_max_m', 'source_sha256'}
    old = {key:value for key,value in vars(source).items() if not key.startswith('_') and key not in missing}
    saved = {}
    monkeypatch.setattr('stowage.linear.cache.vessel_cache_key', lambda *args, **kwargs: 'a'*64)
    monkeypatch.setattr('stowage.linear.cache.load_payload', lambda *args, **kwargs: old)
    monkeypatch.setattr('stowage.linear.cache.store_payload', lambda key,payload,**kwargs: saved.update(payload))
    restored = _geometry_from_params(params)
    assert missing <= saved.keys()
    assert restored.buoyant_x_min_m == source.buoyant_x_min_m
    assert restored.buoyant_x_max_m == source.buoyant_x_max_m
    assert restored.source_sha256 == source.source_sha256
