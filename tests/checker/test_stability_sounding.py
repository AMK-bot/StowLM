"""Analytic precomputed soundings agree with the independent clipping solver."""
import numpy as np
import pytest

from stowage.checker import stability_sounding as subject
from stowage.vessel.tanks import TankGeometry


def solid():
    return TankGeometry.from_params(dict(shape='hull_sections', x_m=[1., 3.],
        integration_weights_m=[2., 2.],
        sections_yz_m=[[[[0., 0.], [2., 0.], [2., 3.]]],
                       [[[0., 0.], [4., 0.], [4., 3.]]]],
        x_min_m=0., x_max_m=4., y_min_m=0., y_max_m=4., z_min_m=0., z_max_m=4.))


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, 'CACHE_DIRECTORY', tmp_path)
    subject.lookup.cache_clear()
    yield
    subject.lookup.cache_clear()


@pytest.mark.parametrize('fraction', [0., 1e-6, .01, .2, .5, .8, .99, 1.-1e-6, 1.])
def test_analytic_lookup_matches_direct_shape_soundings(fraction):
    tank = solid()
    reference = tank.sounding(tank.capacity_m3*fraction)
    actual = subject.get_sounding(tank, reference.volume_m3)
    for key in ('lcg_m', 'tcg_m', 'vcg_m', 'fsm_m4'):
        assert getattr(actual, key) == pytest.approx(getattr(reference, key), abs=2e-8, rel=2e-8)


def test_disk_and_memory_hits_do_not_integrate_geometry(monkeypatch):
    tank = solid()
    expected = subject.get_sounding(tank, 7.)
    subject.lookup.cache_clear()
    def fail(*args, **kwargs):
        raise AssertionError('runtime must not integrate geometry')
    monkeypatch.setattr(type(tank), 'sounding', fail)
    monkeypatch.setattr(type(tank), 'liquid', fail)
    monkeypatch.setattr(subject, '_reference_maximum', fail)
    assert subject.get_sounding(tank, 7.) == expected
    assert subject.max_fsm(tank)[0] > 0


def test_endpoint_discontinuity_and_whole_fill_maximum():
    tank = solid()
    assert subject.get_sounding(tank, 0.).fsm_m4 == 0.
    assert subject.get_sounding(tank, tank.capacity_m3).fsm_m4 == 0.
    # This triangular shape is broadest at the bottom; supremum occurs at 0+.
    maximum, volume = subject.max_fsm(tank)
    assert maximum > subject.get_sounding(tank, .5*tank.capacity_m3).fsm_m4
    assert volume == pytest.approx(0.)
    assert subject.get_sounding(tank, 1e-9*tank.capacity_m3).fsm_m4 == pytest.approx(maximum, rel=1e-8)


def test_different_geometry_never_reuses_table():
    first = solid()
    second = TankGeometry.from_params(dict(shape='hull_sections', x_m=[1., 3.],
        integration_weights_m=[2., 2.],
        sections_yz_m=[[[[0., 0.], [2., 0.], [2., 3.]]],
                       [[[0., 0.], [3., 0.], [3., 3.]]]],
        x_min_m=0., x_max_m=4., y_min_m=0., y_max_m=4., z_min_m=0., z_max_m=4.))
    assert subject._key(first) != subject._key(second)
    assert subject.lookup(first).capacity != subject.lookup(second).capacity


def test_rectangle_closed_form_needs_no_disk():
    tank = TankGeometry(0., 2., -2., 2., 0., 3.)
    assert subject.get_sounding(tank, 8.) == tank.sounding(8.)
    assert subject.max_fsm(tank)[0] == pytest.approx(2.*4.**3/12)


@pytest.mark.parametrize('volume', [-1., np.inf, np.nan, 19.])
def test_volume_validation(volume):
    with pytest.raises(ValueError, match='tank volume'):
        subject.get_sounding(solid(), volume)


@pytest.mark.parametrize('polygon', [
    [[-1.,0.],[1.,0.],[1.,1.],[3.,1.],[3.,3.],[-3.,3.],[-3.,1.],[-1.,1.]],
    [[-1.,0.],[1.,0.],[3.,2.],[1.,4.],[-1.,4.],[-3.,2.]],
])
def test_ledges_and_sloping_edges_preserve_all_mass_moments(polygon):
    from stowage.vessel.hull_tanks import SectionTankGeometry
    p = np.array(polygon)+[1000.,200.]
    tank = SectionTankGeometry(x_min_m=100., x_max_m=102., y_min_m=p[:,0].min(),
        y_max_m=p[:,0].max(), z_min_m=p[:,1].min(), z_max_m=p[:,1].max(),
        x_m=np.array([100.5,101.5]), integration_weights_m=np.ones(2),
        sections_yz_m=((p,),(p,)))
    for fraction in np.random.default_rng(43).uniform(.001,.999,15):
        actual = subject.get_sounding(tank, tank.capacity_m3*fraction)
        expected = tank.sounding(tank.capacity_m3*fraction)
        for key in ('lcg_m','tcg_m','vcg_m','fsm_m4'):
            assert getattr(actual,key) == pytest.approx(getattr(expected,key), abs=2e-7, rel=2e-8)


def test_corrupt_cache_is_disposable():
    tank = solid()
    expected = subject.get_sounding(tank, 8.)
    path = subject.CACHE_DIRECTORY / ('sounding-'+subject._key(tank)+'.npz')
    subject.lookup.cache_clear()
    path.write_bytes(b'not a numpy archive')
    assert subject.get_sounding(tank, 8.) == expected


def test_production_near_horizontal_edges_use_original_endpoints():
    from stowage.vessel.fleet import load_ship_params
    params = load_ship_params('feeder_500')
    record = next(r for r in params['tanks'] if r['id'] == 'HEEL-S')
    tank = TankGeometry.from_params(record, ship_params=params)
    # Reconstructing end=start+(end-start) had moved an edge across a vertex
    # height and exaggerated volume by0.00345m3; the provider is the oracle.
    for fraction in (.02, .2, .5, .83, .98):
        actual = subject.get_sounding(tank, fraction*tank.capacity_m3)
        expected = tank.sounding(fraction*tank.capacity_m3)
        for key in ('lcg_m', 'tcg_m', 'vcg_m', 'fsm_m4'):
            assert getattr(actual, key) == pytest.approx(getattr(expected, key), abs=2e-8, rel=2e-8)


def test_reference_envelope_near_horizontal_wing_edges_match_cached_maximum():
    from stowage.checker.stability_tank_envelope import max_fsm
    from stowage.vessel.fleet import load_ship_params
    params = load_ship_params('feeder_500')
    record = next(r for r in params['tanks'] if r['id'] == 'WING-A-P')
    tank = TankGeometry.from_params(record, ship_params=params)
    assert max_fsm(tank) == pytest.approx(subject.maximum_fsm(tank), abs=1e-9, rel=1e-11)


@pytest.mark.parametrize('field,bad_value', [
    ('maximum', np.array([-1., 2.])),
    ('maximum', np.array([2., 99.])),
    ('maximum', np.array([np.nan, 0.])),
    ('capacity', np.array(-18.)),
    ('capacity', np.array(17.)),
    ('capacity', np.array([18.])),
    ('empty_centroid', np.array([1., np.inf, 0.])),
    ('integrals', np.zeros((1, 4))),
    ('levels', np.array([2., 1.])),
])
def test_invalid_cache_metadata_rebuilds_exact_table(field, bad_value):
    tank = solid()
    expected = subject.get_sounding(tank, 8.)
    maximum = subject.maximum_fsm(tank)
    path = subject.CACHE_DIRECTORY / ('sounding-'+subject._key(tank)+'.npz')
    with np.load(path, allow_pickle=False) as saved:
        arrays = dict(saved)
    arrays[field] = bad_value
    np.savez_compressed(path, **arrays)
    subject.lookup.cache_clear()
    assert subject.get_sounding(tank, 8.) == expected
    assert subject.maximum_fsm(tank) == maximum


def test_unwritable_cache_keeps_computed_exact_table(monkeypatch):
    tank = solid()
    expected = tank.sounding(8.)
    def unwritable(*args, **kwargs):
        raise PermissionError('simulated readonly cache')
    monkeypatch.setattr(subject.tempfile, 'mkstemp', unwritable)
    actual = subject.get_sounding(tank, 8.)
    for key in ('lcg_m','tcg_m','vcg_m','fsm_m4'):
        assert getattr(actual, key) == pytest.approx(getattr(expected, key), abs=1e-11)
    assert subject.maximum_fsm(tank)[0] > 0


def test_unrelated_component_version_preserves_cache_identity(monkeypatch):
    from stowage import versions
    tank = solid()
    original = subject._key(tank)
    monkeypatch.setattr(versions, 'component_versions', lambda: {'checker': 'future-test-version'})
    assert subject._key(tank) == original
