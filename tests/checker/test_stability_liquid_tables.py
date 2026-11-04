"""Actual-transfer table reference, interpolation, and fail-closed tests."""
import json

import numpy as np
import pytest

from stowage.checker import stability_liquid_tables as tables
from stowage.vessel.tanks import TankGeometry


def solid():
    return TankGeometry.from_params(dict(shape='hull_sections', x_m=[1., 3.],
        integration_weights_m=[2., 2.],
        sections_yz_m=[[[[0., 0.], [2., 0.], [2., 3.]]],
                       [[[0., 0.], [4., 0.], [4., 3.]]]],
        x_min_m=0., x_max_m=4., y_min_m=0., y_max_m=4., z_min_m=0., z_max_m=4.))


@pytest.fixture
def table(tmp_path, monkeypatch):
    monkeypatch.setattr(tables, 'CACHE_DIRECTORY', tmp_path)
    tables._lookup.cache_clear()
    shape = solid()
    result = tables.build(shape, fills=(1-np.cos(np.linspace(0, np.pi, 65)))/2,
        heels=np.linspace(-.6, .6, 25), trims=np.linspace(-.06, .06, 7))
    yield shape, result
    tables._lookup.cache_clear()


@pytest.mark.parametrize('heel,trim', [(.0, .0), (.43, .025), (-.37, -.021)])
def test_plane_polynomials_match_direct_liquid_every_fill(heel, trim):
    shape = solid()
    fills = np.r_[0., .01, .15, .5, .9, .99, 1.]
    values = tables._angle_moments(shape, heel, trim, fills)
    for fraction, moment in zip(fills, values):
        volume = fraction*shape.capacity_m3
        np.testing.assert_allclose(moment, volume*np.asarray(shape.liquid(volume, heel, trim).centroid_m), atol=2e-11)


def test_interpolated_centroids_and_endpoints_match_shape(table):
    shape, grid = table
    for fraction in (.03, .3, .52, .91, 1.):
        for heel, trim in ((.21, .013), (-.31, -.023)):
            volume = fraction*shape.capacity_m3
            np.testing.assert_allclose(grid.centroid(volume, heel, trim), shape.liquid(volume, heel, trim).centroid_m, atol=.008)
    assert grid.centroid(0.) == shape.liquid(0.).centroid_m


@pytest.mark.parametrize('volume,heel,trim', [(-1., 0., 0.), (20., 0., 0.), (1., .8, 0.), (1., 0., .1), (1., float('nan'), 0.)])
def test_outside_grid_never_extrapolates(table, volume, heel, trim):
    with pytest.raises(ValueError):
        table[1].centroid(volume, heel, trim)


def test_envelope_covers_actual_fills_both_heel_signs(table):
    shape, grid = table
    for heel, trim in ((.35, .01), (-.35, -.01)):
        normal = np.array([np.tan(trim)*np.cos(heel), -np.sin(heel), np.cos(heel)])
        normal /= np.linalg.norm(normal)
        along = np.array([1., 0., -np.tan(trim)])
        along /= np.linalg.norm(along)
        direction = np.cross(normal, along)*np.sign(heel)
        sampled = []
        for volume in np.linspace(0, shape.capacity_m3, 151):
            shifted = np.asarray(shape.liquid(volume, heel, trim).centroid_m)-shape.liquid(volume).centroid_m
            sampled.append(volume*(shifted @ direction))
        actual = grid.max_transfer(heel, trim, tolerance_m4=.001)
        assert actual == pytest.approx(max(sampled), abs=.025)


def test_lookup_requires_sha_bound_qualification(table):
    shape, grid = table
    assert tables.lookup(shape) is None
    assert tables.lookup(shape, require_qualified=False) is not None
    tables.qualify(shape, dict(gz=.02, area=.03, gm=0., samples=10))
    assert tables.lookup(shape) is not None
    path = tables._path(shape)
    path.write_bytes(path.read_bytes()+b'changed')
    tables._lookup.cache_clear()
    assert tables.lookup(shape) is None


@pytest.mark.parametrize('statistics', [dict(gz=.21, area=.01, gm=0., samples=10), dict(gz=.01, area=.01, gm=float('nan'), samples=10), dict(gz=.01, area=.01, gm=0., samples=3)])
def test_qualification_rejects_missing_or_failed_error_evidence(table, statistics):
    with pytest.raises(ValueError):
        tables.qualify(table[0], statistics)


def test_corrupt_array_falls_back(table):
    shape, _ = table
    path = tables._path(shape)
    path.write_bytes(b'not a numpy table')
    tables._lookup.cache_clear()
    assert tables.lookup(shape, require_qualified=False) is None


def test_unrelated_versions_preserve_table_and_qualification(table, monkeypatch):
    from stowage import versions
    shape,grid=table
    tables.qualify(shape,dict(gz=.1,area=.1,gm=.1,samples=4))
    old=tables._path(shape)
    monkeypatch.setattr(versions,'component_versions',lambda:{'checker':'new','ballast':'new'})
    tables._lookup.cache_clear()
    assert tables._path(shape)==old
    assert tables.lookup(shape) is not None


def test_builder_version_requires_new_qualification(table,monkeypatch):
    shape,grid=table
    old=tables._path(shape)
    monkeypatch.setattr(tables,'STABILITY_LIQUID_TABLES_VERSION',tables.STABILITY_LIQUID_TABLES_VERSION+1)
    assert tables._path(shape) != old
    assert tables.lookup(shape) is None


def test_nan_sample_count_cannot_certify_or_load(table):
    shape, grid = table
    with pytest.raises(ValueError):
        tables.qualify(shape, dict(gz=0., area=0., gm=0., samples=float('nan')))
    tables.qualify(shape, dict(gz=0., area=0., gm=0., samples=4))
    path = tables._path(shape).with_suffix('.qualified.json')
    evidence = json.loads(path.read_text())
    evidence['samples'] = float('nan')
    path.write_text(json.dumps(evidence))
    tables._lookup.cache_clear()
    assert tables.lookup(shape) is None


@pytest.mark.parametrize('builder',['liquid','sounding'])
def test_legacy_qualification_cannot_cross_future_builder_version(table,monkeypatch,builder):
    from stowage.checker import stability_sounding
    shape,grid=table
    tables.qualify(shape,dict(gz=.1,area=.1,gm=.1,samples=4))
    if builder=='liquid':
        monkeypatch.setattr(tables,'STABILITY_LIQUID_TABLES_VERSION',tables.STABILITY_LIQUID_TABLES_VERSION+1)
    else:
        monkeypatch.setattr(stability_sounding,'STABILITY_SOUNDING_VERSION',2)
    monkeypatch.setattr(tables,'lookup',lambda *a,**kw:None)
    assert tables.migrate_legacy(shape)=='missing'


def test_default_domain_covers_full_fills_and_list_offset():
    assert len(tables.DEFAULT_FILLS)==257
    assert len(tables.DEFAULT_HEELS)==57
    assert len(tables.DEFAULT_TRIMS)==13
    assert tables.DEFAULT_FILLS[[0,-1]] == pytest.approx([0.,1.])
    assert tables.DEFAULT_HEELS[[0,-1]] == pytest.approx(np.radians([-70.,70.]))
    # KN's full +/- .06 L trim domain corresponds to atan(.06) tank trim.
    assert tables.DEFAULT_TRIMS[0] < -np.arctan(.06)
    assert tables.DEFAULT_TRIMS[-1] > np.arctan(.06)


def test_widening_liquid_grid_integrates_only_new_angular_nodes(tmp_path,monkeypatch):
    monkeypatch.setattr(tables,'CACHE_DIRECTORY',tmp_path)
    shape=solid(); calls=[]
    def moment(solid,heel,trim,fills):
        calls.append((heel,trim))
        return np.asarray(fills)[:,None]*solid.capacity_m3*np.array([heel,trim,1.])
    monkeypatch.setattr(tables,'_angle_moments',moment)
    fills=np.linspace(0.,1.,4); heels=np.linspace(-.3,.3,4); trims=np.linspace(-.03,.03,4)
    previous=tables.build(shape,fills=fills,heels=heels,trims=trims)
    calls.clear()
    widened=tables.build(shape,fills=fills,heels=np.r_[-.5,heels,.5],trims=trims)
    assert len(calls)==2*len(trims)
    assert widened.moments[1:-1] == pytest.approx(previous.moments,abs=0.)
    assert all(abs(heel)==.5 for heel,trim in calls)
    assert not list(tmp_path.glob('*.partial.npz'))


def test_liquid_grid_restarts_from_completed_heel_checkpoint(tmp_path,monkeypatch):
    monkeypatch.setattr(tables,'CACHE_DIRECTORY',tmp_path)
    shape=solid(); calls=[]
    fills=np.linspace(0.,1.,4); heels=np.linspace(-.3,.3,4); trims=np.linspace(-.03,.03,4)
    def interrupted(solid,heel,trim,fills):
        if heel==heels[1]: raise RuntimeError('simulated interruption')
        return np.asarray(fills)[:,None]*solid.capacity_m3*np.array([heel,trim,1.])
    monkeypatch.setattr(tables,'_angle_moments',interrupted)
    with pytest.raises(RuntimeError,match='interruption'):
        tables.build(shape,fills=fills,heels=heels,trims=trims)
    assert len(list(tmp_path.glob('*.partial.npz')))==1
    def resumed(solid,heel,trim,fills):
        calls.append((heel,trim))
        return np.asarray(fills)[:,None]*solid.capacity_m3*np.array([heel,trim,1.])
    monkeypatch.setattr(tables,'_angle_moments',resumed)
    built=tables.build(shape,fills=fills,heels=heels,trims=trims)
    assert len(calls)==(len(heels)-1)*len(trims)
    assert all(heel!=heels[0] for heel,trim in calls)
    assert np.isfinite(built.moments).all()
    assert not list(tmp_path.glob('*.partial.npz'))


def test_partial_wider_table_never_supplants_ready_table(table):
    shape,grid=table
    original_identity=tables._identity(shape)
    unfinished=tables._path(shape).with_name('liquid-'+tables._base_identity(shape)+'-unfinished.partial.npz')
    tables._write(unfinished,grid,shape)
    tables._lookup.cache_clear()
    assert tables._identity(shape)==original_identity
    assert tables.lookup(shape,require_qualified=False) is not None
