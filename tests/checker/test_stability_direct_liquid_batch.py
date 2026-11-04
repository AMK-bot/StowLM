"""Direct batch roots agree with independent tank geometry, including endpoints."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker.stability_direct_liquid_batch import prepare, centroids, evaluate, _integrate
from stowage.checker.stability_loading import CorrectionTank
from stowage.vessel.hull_tanks import SectionTankGeometry


def condition(fills=(.03, .37, .97)):
    tanks = []
    for i, fill in enumerate(fills):
        p = np.array([[0., 0.], [3., 0.], [2., 2.], [0., 1.]])+[i*4., 0.]
        q = (p-[i*4., 0.])*[.7, .9]+[i*4., 0.]
        solid = SectionTankGeometry(x_min_m=-1., x_max_m=1., y_min_m=i*4., y_max_m=i*4.+3.,
            z_min_m=0., z_max_m=2., x_m=np.array([-.6, .6]),
            integration_weights_m=np.ones(2), sections_yz_m=((p,), (q,)))
        tanks.append(CorrectionTank(str(i), 1000., solid.capacity_m3*fill, 0., solid))
    return SimpleNamespace(actual_tanks=tuple(tanks), mass_kg=100000., lcg_m=0., tcg_m=0., kg_m=3.,
                           upright_cache={}, precomputed_soundings=False, liquid_usage={})


@pytest.mark.parametrize('heel,trim', [(.04, .008), (.47, -.02), (-.7, .03), (0., 0.), (.9, -.05)])
def test_batch_centroids_match_actual_geometry(heel, trim):
    c = condition()
    batch = prepare(c)
    result = centroids(batch, heel, trim)
    reference = np.array([t.geometry.liquid(t.volume_m3, heel, trim).centroid_m for t in c.actual_tanks])
    assert result == pytest.approx(reference, abs=2e-9)
    expected = np.array([c.lcg_m, c.tcg_m, c.kg_m])
    for tank, moved in zip(c.actual_tanks, reference, strict=True):
        upright = tank.geometry.liquid(tank.volume_m3).centroid_m
        expected += tank.density_kg_m3*tank.volume_m3/c.mass_kg*(moved-upright)
    assert evaluate(c, heel, trim) == pytest.approx(expected, abs=1e-10)


def test_full_and_empty_tanks_have_zero_transfer():
    c = condition((0., 1., 0.))
    assert evaluate(c, .4, .03) == (c.lcg_m, c.tcg_m, c.kg_m)


def test_batch_volume_derivative_matches_independent_finite_difference():
    c = condition()
    batch = prepare(c)
    normal = SectionTankGeometry._normal(.31, .02)
    projections = batch.low_corners @ normal
    levels = (projections.min(axis=1)+projections.max(axis=1))/2
    _, derivative = _integrate(batch, levels, normal)
    epsilon = 1e-5
    plus, _ = _integrate(batch, levels+epsilon, normal)
    minus, _ = _integrate(batch, levels-epsilon, normal)
    assert derivative == pytest.approx((plus[:, 0]-minus[:, 0])/(2*epsilon), rel=1e-7)


def test_bad_angles_are_rejected():
    b = prepare(condition())
    for heel, trim in [(np.nan, 0.), (np.pi/2, 0.), (0., np.inf)]:
        with pytest.raises(ValueError):
            centroids(b, heel, trim)


def test_timeout_propagates(monkeypatch):
    import stowage.checker.stability_direct_liquid_batch as module
    from stowage.checker.stability_budget import StabilityTimeout
    b = prepare(condition())
    def poll(label):
        if label.endswith('refinement'):
            raise StabilityTimeout('test deadline')
    monkeypatch.setattr(module, 'check_budget', poll)
    with pytest.raises(StabilityTimeout):
        centroids(b, .4, .03)


def test_previous_liquid_plane_is_only_a_seed_and_new_volume_is_checked(monkeypatch):
    import stowage.checker.stability_direct_liquid_batch as module
    c = condition()
    b = prepare(c)
    original = module._integrate
    calls = []
    def counted(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(module, '_integrate', counted)
    centroids(b, .41, .02)
    cold_calls = len(calls)
    calls.clear()
    actual = centroids(b, .41, .0200001)
    assert len(calls) < cold_calls
    expected = [t.geometry.liquid(t.volume_m3, .41, .0200001).centroid_m for t in c.actual_tanks]
    assert actual == pytest.approx(np.asarray(expected), abs=2e-9)


def test_exact_collinear_subdivision_changes_no_direct_moments():
    c = condition()
    old = c.actual_tanks[0]
    solid = old.geometry
    refined = tuple(tuple(np.stack((p, (p+np.roll(p, -1, axis=0))/2), axis=1).reshape(-1, 2)
                          for p in section) for section in solid.sections_yz_m)
    other = SectionTankGeometry(x_min_m=solid.x_min_m, x_max_m=solid.x_max_m,
        y_min_m=solid.y_min_m, y_max_m=solid.y_max_m, z_min_m=solid.z_min_m, z_max_m=solid.z_max_m,
        x_m=solid.x_m, integration_weights_m=solid.integration_weights_m, sections_yz_m=refined)
    c.actual_tanks = (CorrectionTank('refined', old.density_kg_m3, old.volume_m3, 0., other),)
    result = evaluate(c, .41, .02)
    reference = np.array([c.lcg_m, c.tcg_m, c.kg_m])
    reference += old.density_kg_m3*old.volume_m3/c.mass_kg*(np.asarray(other.liquid(old.volume_m3, .41, .02).centroid_m)-
                                                            other.liquid(old.volume_m3).centroid_m)
    assert result == pytest.approx(reference, abs=1e-10)
