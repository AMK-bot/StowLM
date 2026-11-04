"""The centroid-only acceleration retains the full liquid provider as oracle."""
import numpy as np
import pytest

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    'stability_centroid_only_prototype', Path(__file__).with_name('centroid_only_prototype.py'))
_prototype = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_prototype)
centroid_level = _prototype.centroid_level
PROVIDER_SOURCE_SHA256 = _prototype.PROVIDER_SOURCE_SHA256
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.tanks import TankGeometry


@pytest.fixture(scope='module', params=['feeder_500', 'feeder_1000', 'feeder_1700'])
def production_tank(request):
    params = load_ship_params(request.param)
    record = next(t for t in params['tanks'] if t['id'] == 'FO-A-P')
    return TankGeometry.from_params(record, ship_params=params)


def test_production_provider_agreement_and_volume_proof(production_tank):
    tank = production_tank
    assert tank.shape == 'hull_sections'
    rng = np.random.default_rng(31025)
    cases = [(0., .3, .02), (1., -.3, -.02), (1e-7, .42, -.03),
             (1-1e-7, -.42, .03), (.5, 0., 0.)]
    cases.extend((rng.uniform(.01, .99), rng.uniform(-.7, .7), rng.uniform(-.06, .06))
                 for _ in range(5))
    for fraction, heel, trim in cases:
        volume = fraction*tank.capacity_m3
        centroid, level = centroid_level(tank, volume, heel, trim)
        oracle = tank.liquid(volume, heel, trim)
        np.testing.assert_allclose(centroid, oracle.centroid_m, atol=1e-10, rtol=0)
        assert level == pytest.approx(oracle.plane_level_m, abs=1e-10)
        if 0 < fraction < 1:
            integrated = tank._integrate(level, tank._normal(heel, trim))[0]
            assert abs(integrated-volume) <= max(1e-10, 2e-10*volume)
        assert not centroid.flags.writeable
        assert centroid_level(tank, volume, heel, trim)[0] is centroid


def test_rectangle_delegates_without_shape_change():
    tank = TankGeometry(-1., 1., -2., 2., 0., 3.)
    centroid, level = centroid_level(tank, .37*tank.capacity_m3, -.23, .04)
    oracle = tank.liquid(.37*tank.capacity_m3, -.23, .04)
    np.testing.assert_allclose(centroid, oracle.centroid_m, atol=1e-10, rtol=0)
    assert level == oracle.plane_level_m


def test_provider_hash_and_validation(production_tank):
    assert len(PROVIDER_SOURCE_SHA256) == 64
    with pytest.raises(ValueError):
        centroid_level(production_tank, -1., .2, .02)
    with pytest.raises(ValueError):
        centroid_level(production_tank, 1., np.pi/2, 0.)
