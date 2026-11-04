"""Independent filling sweeps test the conservative shaped-tank envelope."""
import numpy as np
import pytest

from stowage.checker.stability_tank_transfer import max_transfer
from stowage.vessel.hull_tanks import SectionTankGeometry


def solid(polygon, mirror=False):
    p = np.asarray(polygon, dtype=float)
    if mirror:
        p = p[::-1].copy()
        p[:, 0] *= -1
    return SectionTankGeometry(x_min_m=-1., x_max_m=1.,
        y_min_m=float(p[:, 0].min()), y_max_m=float(p[:, 0].max()),
        z_min_m=float(p[:, 1].min()), z_max_m=float(p[:, 1].max()),
        x_m=np.array([-1/np.sqrt(3), 1/np.sqrt(3)]),
        integration_weights_m=np.ones(2), sections_yz_m=((p,), (p,)))


def moment(tank, fraction, heel, trim):
    volume = fraction*tank.capacity_m3
    normal = np.array([np.tan(trim)*np.cos(heel), -np.sin(heel), np.cos(heel)])
    normal /= np.linalg.norm(normal)
    along = np.array([1., 0., -np.tan(trim)])
    along /= np.linalg.norm(along)
    direction = np.cross(normal, along)*(-1 if heel < 0 else 1)
    cg = np.asarray(tank.liquid(volume, heel, trim).centroid_m)
    upright = np.asarray(tank.liquid(volume).centroid_m)
    return volume*float((cg-upright)@direction)


@pytest.mark.parametrize('heel,trim', [(0.4, 0.), (-0.4, 0.), (0.3, .12), (-.3, -.12), (0., .12)])
def test_envelope_bounds_independent_dense_sweep(heel, trim):
    tank = solid([(0, 0), (3, 0), (1.1, 2.5), (0, 1)])
    tolerance = .002
    upper = max_transfer(tank, heel, trim, tolerance_m4=tolerance)
    sweep = max(moment(tank, f, heel, trim) for f in np.linspace(0, 1, 251))
    assert upper >= sweep-1e-9
    # Dense grid has a small independent sampling gap; the algorithm internally
    # certifies its own lower/upper gap, rather than relying on this sweep.
    assert upper-sweep <= tolerance+.0003


def test_asymmetric_triangle_maximum_is_not_half_fill():
    tank = solid([(0, 0), (4, 0), (0, 3)])
    heel = .5
    fractions = np.linspace(0, 1, 101)
    values = [moment(tank, f, heel, 0.) for f in fractions]
    assert abs(fractions[np.argmax(values)]-.5) > .05
    assert max(values) > moment(tank, .5, heel, 0.)+.01
    upper = max_transfer(tank, heel, 0., tolerance_m4=.001)
    assert max(values) <= upper <= max(values)+.002


def test_mirrored_shapes_and_negative_heel_agree():
    polygon = [(0, 0), (3, 0), (1.1, 2.5), (0, 1)]
    positive = max_transfer(solid(polygon), .35, .08, tolerance_m4=.002)
    negative = max_transfer(solid(polygon, mirror=True), -.35, .08, tolerance_m4=.002)
    assert negative == pytest.approx(positive, abs=1e-8)


def test_zero_angle_exact_and_bad_input():
    tank = solid([(0, 0), (3, 0), (0, 2)])
    assert max_transfer(tank, 0., 0., tolerance_m4=.001) == 0.
    for heel, trim, tol in [(np.nan, 0., 1.), (0., np.pi/2, 1.), (.1, 0., 0.)]:
        with pytest.raises(ValueError):
            max_transfer(tank, heel, trim, tolerance_m4=tol)


def test_budget_failure_is_closed(monkeypatch):
    import stowage.checker.stability_tank_transfer as module
    monkeypatch.setattr(module, '_MAX_EVALUATIONS', 2)
    tank = solid([(0, 0), (3, 0), (0, 2)])
    with pytest.raises(ValueError, match='conservative tolerance budget'):
        max_transfer(tank, .3, .03, tolerance_m4=1e-7)


def test_concave_section_keeps_actual_shape():
    tank = solid([(0, 0), (3, 0), (3, 1), (1, 1), (1, 3), (0, 3)])
    tolerance = .003
    upper = max_transfer(tank, .42, -.06, tolerance_m4=tolerance)
    sweep = max(moment(tank, f, .42, -.06) for f in np.linspace(0, 1, 151))
    assert sweep <= upper+1e-9
    assert upper-sweep < tolerance+.001


def shaped_nonprismatic():
    p = np.array([[0., 0.], [3., 0.], [2., 2.], [0., 1.]])
    q = p*np.array([.7, .9])
    return SectionTankGeometry(x_min_m=-1., x_max_m=1., y_min_m=0., y_max_m=3.,
        z_min_m=0., z_max_m=2., x_m=np.array([-.6, .6]),
        integration_weights_m=np.ones(2), sections_yz_m=((p,), (q,)))


@pytest.mark.parametrize('heel,trim', [(.4, .03), (-.6, -.04), (0., .02), (.001, -.001)])
def test_exact_polynomial_centroids_and_levels_match_reference(heel, trim):
    from stowage.checker.stability_tank_transfer import _direct_liquid
    tank = shaped_nonprismatic()
    for fraction in [0., 1e-7, .013, .21, .67, .997, 1.]:
        volume = fraction*tank.capacity_m3
        expected = tank.liquid(volume, heel, trim)
        centroid, level = _direct_liquid(tank, volume, heel, trim)
        assert centroid == pytest.approx(expected.centroid_m, abs=2e-9)
        assert level == pytest.approx(expected.plane_level_m, abs=2e-10)


def test_nonprismatic_direct_envelope_bounds_actual_liquid_sweep():
    tank = shaped_nonprismatic()
    heel, trim, tolerance = .41, .025, .002
    upper = max_transfer(tank, heel, trim, tolerance_m4=tolerance)
    sweep = max(moment(tank, f, heel, trim) for f in np.linspace(0, 1, 151))
    assert sweep <= upper+1e-10
    assert upper-sweep < tolerance+.001


def test_centroid_only_path_never_calculates_liquid_surface(monkeypatch):
    from stowage.checker.stability_tank_transfer import liquid_centroid
    tank = shaped_nonprismatic()
    expected = tank.liquid(.37*tank.capacity_m3, .4, .03).centroid_m
    original = SectionTankGeometry._integrate
    calls = []
    def integrate(self, level, normal, **kwargs):
        assert not kwargs.get('surface', False)
        calls.append(level)
        return original(self, level, normal, **kwargs)
    monkeypatch.setattr(SectionTankGeometry, '_integrate', integrate)
    actual = liquid_centroid(tank, .37*tank.capacity_m3, .4, .03)
    assert actual == pytest.approx(expected, abs=1e-11)
    assert calls


def test_polynomial_envelope_never_calls_provider_liquid(monkeypatch):
    tank = shaped_nonprismatic()
    def forbidden(*args, **kwargs):
        raise AssertionError('expensive surface-inertia provider used')
    monkeypatch.setattr(SectionTankGeometry, 'liquid', forbidden)
    assert max_transfer(tank, .42, .021, tolerance_m4=.003) > 0.


@pytest.mark.parametrize('heel,trim', [(-.55, -.03), (.03, .015), (.7, -.025)])
def test_analytic_derivative_envelope_both_sides_and_near_upright(heel, trim):
    tank = shaped_nonprismatic()
    upper = max_transfer(tank, heel, trim, tolerance_m4=.002)
    sweep = max(moment(tank, f, heel, trim) for f in np.linspace(0, 1, 151))
    assert sweep <= upper+1e-10
    assert upper-sweep < .003


@pytest.mark.parametrize('heel,trim', [(.04, .008), (.47, -.02), (-.7, .03)])
def test_vector_angle_moments_matches_independent_geometry(heel, trim):
    from stowage.checker.stability_tank_transfer import angle_moments
    tank = shaped_nonprismatic()
    fractions = np.array([0., .001, .03, .37, .81, .999, 1.])
    actual = angle_moments(tank, heel, trim, fractions)
    expected = np.array([f*tank.capacity_m3*np.asarray(tank.liquid(f*tank.capacity_m3, heel, trim).centroid_m)
                         for f in fractions])
    assert actual == pytest.approx(expected, abs=1e-8)


def test_centroid_budget_exception_propagates(monkeypatch):
    import stowage.checker.stability_tank_transfer as module
    from stowage.checker.stability_budget import StabilityTimeout
    calls = []
    def poll(label):
        calls.append(label)
        if label.endswith('exit'):
            raise StabilityTimeout('test deadline')
    monkeypatch.setattr(module, 'check_budget', poll)
    tank = shaped_nonprismatic()
    with pytest.raises(StabilityTimeout, match='test deadline'):
        module.liquid_centroid(tank, .31*tank.capacity_m3, .3, .02)
    assert calls == ['direct tank centroid entry', 'direct tank centroid exit']


def test_envelope_polls_budget_during_refinement(monkeypatch):
    import stowage.checker.stability_tank_transfer as module
    from stowage.checker.stability_budget import StabilityTimeout
    calls = []
    def poll(label):
        calls.append(label)
        if label.endswith('refinement'):
            raise StabilityTimeout('test deadline')
    monkeypatch.setattr(module, 'check_budget', poll)
    with pytest.raises(StabilityTimeout, match='test deadline'):
        module.max_transfer(shaped_nonprismatic(), .331, .023, tolerance_m4=1e-7)
    assert 'direct tank-transfer envelope refinement' in calls


def test_reflected_contours_reuse_opposite_heel_envelope(monkeypatch):
    import stowage.checker.stability_tank_transfer as module
    polygon = [(0, 0), (4, 0), (2, 3), (0, 2)]
    port, starboard = solid(polygon), solid(polygon, mirror=True)
    positive = module.max_transfer(port, .371, .023, tolerance_m4=.002)
    def forbidden(*args):
        raise AssertionError('verified reflection should reuse its exact cached envelope')
    monkeypatch.setattr(module, '_edges', forbidden)
    negative = module.max_transfer(starboard, -.371, .023, tolerance_m4=.002)
    assert negative == positive


def test_contour_cyclic_start_does_not_change_identity():
    from stowage.checker.stability_tank_transfer import _canonical_cache_key
    polygon = np.array([(0, 0), (4, 0), (2, 3), (0, 2)])
    a, b = solid(polygon), solid(np.roll(polygon, 2, axis=0))
    assert _canonical_cache_key(a, .3, .02, .001) == _canonical_cache_key(b, .3, .02, .001)


def test_mirror_key_preserves_zero_heel_outward_direction():
    from stowage.checker.stability_tank_transfer import _canonical_cache_key
    polygon = [(0, 0), (4, 0), (2, 3), (0, 2)]
    a, b = solid(polygon), solid(polygon, mirror=True)
    # At zero heel both callers define outward as +y. Reflection maps +y to -y,
    # so sharing that key would incorrectly reuse an inward transfer envelope.
    assert _canonical_cache_key(a, 0., .02, .001) != _canonical_cache_key(b, 0., .02, .001)
    assert _canonical_cache_key(a, .3, .02, .001) == _canonical_cache_key(b, -.3, .02, .001)
    assert _canonical_cache_key(a, .3, .02, .001) != _canonical_cache_key(b, .3, .02, .001)


def test_one_ulp_coordinate_change_is_not_mirrored_identity():
    from stowage.checker.stability_tank_transfer import _canonical_cache_key
    polygon = np.array([(0., 0.), (4., 0.), (2., 3.), (0., 2.)])
    a = solid(polygon)
    polygon[2, 0] = np.nextafter(polygon[2, 0], np.inf)
    b = solid(polygon)
    assert _canonical_cache_key(a, .3, .02, .001) != _canonical_cache_key(b, .3, .02, .001)


def test_capacity_and_tolerance_remain_in_exact_envelope_key():
    from types import SimpleNamespace
    from stowage.checker.stability_tank_transfer import _canonical_cache_key
    a = solid([(0, 0), (4, 0), (2, 3), (0, 2)])
    attributes = dict(a.__dict__)
    attributes['shape'] = a.shape
    attributes['capacity_m3'] = np.nextafter(a.capacity_m3, np.inf)
    b = SimpleNamespace(**attributes)
    assert _canonical_cache_key(a, .3, .02, .001) != _canonical_cache_key(b, .3, .02, .001)
    assert _canonical_cache_key(a, .3, .02, .001) != _canonical_cache_key(a, .3, .02, .002)
