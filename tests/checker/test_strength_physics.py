"""Independent beam equilibria, with the Station up-force/hogging convention.

These are closed-form force and moment balances, not real-hull validation.
Composite Gauss stations are deliberately nonuniform, like HullGeometry's grid.
"""

from __future__ import annotations

import numpy as np
import pytest

from stowage.checker.strength import _tank_segments, integrate_strength
from stowage.vessel.tanks import TankGeometry


G = 9.80665


def _gauss_grid(edges):
    x, weights = [], []
    for a, b in zip(edges, edges[1:]):
        midpoint, half = (a + b) / 2, (b - a) / 2
        x.extend([midpoint - half / np.sqrt(3), midpoint + half / np.sqrt(3)])
        weights.extend([half, half])
    return np.asarray(x), np.asarray(weights)


@pytest.mark.parametrize("edges", [[0, 100], [0, 3, 19, 50, 58, 97, 100]])
def test_uniform_box_barge_uniform_weight_has_zero_sf_bm(edges):
    x, weights = _gauss_grid(edges)
    stations = np.linspace(0, 100, 101)
    curves = integrate_strength(
        x, weights, np.full_like(x, 20_000 * G),
        [(0, 100, 2_000_000)], [], stations, lpp_m=100,
    )
    np.testing.assert_allclose(curves.shear_n, 0, atol=1e-7)
    np.testing.assert_allclose(curves.bending_nm, 0, atol=1e-5)
    assert curves.raw_end_shear_n == pytest.approx(0, abs=1e-7)
    assert curves.raw_end_bending_nm == pytest.approx(0, abs=1e-5)


def test_midship_point_weight_uniform_buoyancy_matches_closed_form_sagging():
    length, mass = 100.0, 250_000.0
    x, weights = _gauss_grid([0, 7, 22, 49, 51, 78, 100])
    # Include both limits of the SF jump. The value exactly at the load is the
    # forward-side cut, while BM is continuous there.
    stations = np.array([0, 10, 25, 49.999999, 50, 50.000001, 75, 90, 100])
    curves = integrate_strength(
        x, weights, np.full_like(x, mass * G / length),
        [], [(length / 2, mass)], stations, lpp_m=length,
    )
    expected_shear = mass * G * stations / length - mass * G * (stations >= length / 2)
    expected_bending = -mass * G * stations**2 / (2 * length)
    expected_bending += mass * G * np.maximum(0, stations - length / 2)
    np.testing.assert_allclose(curves.shear_n, expected_shear, atol=1e-7)
    np.testing.assert_allclose(curves.bending_nm, expected_bending, atol=1e-5)
    assert curves.bending_nm[4] == pytest.approx(-mass * G * length / 8)
    assert curves.raw_end_shear_n == pytest.approx(0, abs=1e-7)
    assert curves.raw_end_bending_nm == pytest.approx(0, abs=1e-5)


def test_end_weights_uniform_buoyancy_produce_hogging():
    length, mass = 80.0, 100_000.0
    x, weights = _gauss_grid([0, 9, 40, 71, 80])
    stations = np.array([1, 20, 40, 60, 79, 80])
    curves = integrate_strength(
        x, weights, np.full_like(x, mass * G / length),
        [], [(0, mass / 2), (length, mass / 2)], stations, lpp_m=length,
    )
    expected = mass * G * stations / 2 - mass * G * stations**2 / (2 * length)
    np.testing.assert_allclose(curves.bending_nm, expected, atol=1e-6)
    assert curves.bending_nm[2] == pytest.approx(mass * G * length / 8)
    assert curves.shear_n[-1] == pytest.approx(0, abs=1e-7)


def test_linear_buoyancy_uses_gauss_weights_and_preserves_uncorrected_imbalance():
    length = 60.0
    x, weights = _gauss_grid([0, 1, 8, 27, 60])
    stations = np.array([0, 2, 11, 30, 60])
    a, b = 125.0, 17.0
    curves = integrate_strength(
        x, weights, a + b * x, [], [], stations, lpp_m=length, close=False,
    )
    np.testing.assert_allclose(curves.shear_n, a * stations + b * stations**2 / 2, atol=1e-8)
    np.testing.assert_allclose(curves.bending_nm, -a * stations**2 / 2 - b * stations**3 / 6, atol=1e-7)
    assert curves.raw_end_shear_n == pytest.approx(a * length + b * length**2 / 2)
    assert curves.raw_end_bending_nm == pytest.approx(-a * length**2 / 2 - b * length**3 / 6)


def test_affine_load_closure_reports_raw_errors_and_closes_both_curves():
    length = 60.0
    x, weights = _gauss_grid([0, 1, 8, 27, 60])
    stations = np.array([0, 2, 11, 30, 60])
    a, b = 125.0, 17.0
    curves = integrate_strength(
        x, weights, a + b * x, [], [], stations, lpp_m=length,
    )
    assert curves.raw_end_shear_n == pytest.approx(a * length + b * length**2 / 2)
    assert curves.raw_end_bending_nm == pytest.approx(-a * length**2 / 2 - b * length**3 / 6)
    np.testing.assert_allclose(curves.shear_n, 0, atol=1e-8)
    np.testing.assert_allclose(curves.bending_nm, 0, atol=1e-7)


@pytest.mark.parametrize("fill", [0.0, 0.04, 0.5, 0.97, 1.0])
@pytest.mark.parametrize("heel,trim", [(0.0, 0.0), (0.12, -0.08), (-0.16, 0.13)])
def test_tilted_tank_distribution_matches_independent_solid_volume_and_centroid(fill, heel, trim):
    # LiquidCondition integrates the clipped 3-D solid via tetrahedra. Strength
    # separately clips transverse sections and integrates piecewise polynomials;
    # agreement checks both total force and longitudinal first moment.
    solid = TankGeometry(7, 31, -5, 3, 0.6, 6.1)
    volume = fill * solid.capacity_m3
    liquid = solid.liquid(volume, heel_rad=heel, trim_rad=trim)
    density, length = 1025.0, 50.0
    segments = _tank_segments(solid, liquid, heel, trim, density)
    x, weights = _gauss_grid([0, 8, 33, length])
    curves = integrate_strength(
        x, weights, np.zeros_like(x), [], [], [0, length],
        lpp_m=length, close=False, distributed_segments=segments,
    )
    weight = volume * density * G
    assert curves.raw_end_shear_n == pytest.approx(-weight, rel=2e-10, abs=1e-6)
    assert curves.raw_end_bending_nm == pytest.approx(
        weight * (length - liquid.centroid_m[0]), rel=2e-10, abs=1e-5,
    )


def test_smooth_sectional_load_reconstruction_converges_under_grid_doubling():
    length = 100.0
    stations = np.linspace(0, length, 1003)
    errors = []
    for panels in (8, 16):
        x, weights = _gauss_grid(np.linspace(0, length, panels + 1))
        curves = integrate_strength(
            x, weights, 3 * x**2, [], [], stations, lpp_m=length, close=False,
        )
        errors.append((
            np.max(np.abs(curves.shear_n - stations**3)),
            np.max(np.abs(curves.bending_nm + stations**4 / 4)),
        ))
        # Two-point Gauss exactly integrates this quadratic's total force and
        # its cubic first moment even before reconstruction is refined.
        assert curves.raw_end_shear_n == pytest.approx(length**3)
        assert curves.raw_end_bending_nm == pytest.approx(-length**4 / 4)
    assert errors[1][0] < errors[0][0] / 7
    assert errors[1][1] < errors[0][1] / 14


def _regression_vessel(name):
    from stowage.vessel.fleet import load_vessel

    return load_vessel(name, with_hull=True)


def _trimmed_full_load_instance():
    from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id
    from stowage.model import Placement, ShipState
    from stowage.model.fixtures import build_instance
    from stowage.vessel.fleet import load_vessel

    instance = build_instance()
    instance.vessel = _regression_vessel('feeder_800')
    # Synthetic high, aft fixed load deliberately produces a trim couple above
    # the former gate while preserving a physically solved floating condition.
    instance.vessel.fixed_loads.append((instance.vessel.hull.lpp_m*.1,0.,50.,1e6))
    # Deliberately isolate closure physics from the independently tested limits.
    for station in instance.vessel.hull.stations:
        station.min_shear_n = station.min_bending_nm = -1e14
        station.max_shear_n = station.max_bending_nm = 1e14
    template = instance.containers[0]
    instance.containers = []
    placements = []
    for i,cell in enumerate(instance.vessel.cells):
        cid = make_container_id(SYNTHETIC_OWNER_CODES[0],100000+i)
        instance.containers.append(template.model_copy(update={
            'id':cid,'length_ft':20,'gross_mass_kg':10_000.,'oog':None}))
        placements.append(Placement(slot=cell.id,container_id=cid))
    instance.onboard = ShipState(vessel_id=instance.vessel.id,placements=placements)
    return instance


def test_real_trim_couple_above_old_closure_guard_is_not_strength_failure():
    from stowage.checker.strength import check_strength

    instance = _trimmed_full_load_instance()
    report = check_strength(instance,instance.onboard,call_index=0)
    assert report.ran, report.error
    assert report.metrics['raw_closure_moment_fraction'] > 1e-3
    assert report.metrics['closure_moment_fraction'] < 1e-8
    assert report.metrics['raw_end_bending_nm'] == pytest.approx(
        report.metrics['predicted_trim_end_bending_nm'],abs=1.)
    assert not report.violations


def test_genuine_numerical_imbalance_cannot_be_closed_into_a_strength_verdict(monkeypatch):
    from dataclasses import replace
    from stowage.checker import strength

    instance = _trimmed_full_load_instance()
    original = strength.integrate_strength
    def corrupted(*args,**kwargs):
        curve = original(*args,**kwargs)
        return replace(curve,raw_end_bending_nm=curve.raw_end_bending_nm+1e11)
    monkeypatch.setattr(strength,'integrate_strength',corrupted)
    report = strength.check_strength(instance,instance.onboard,call_index=0)
    assert not report.ran
    assert 'Numerical load-curve imbalance' in report.error
    assert all(v.rule_id != 'iacs.s11a.longitudinal_strength' for v in report.violations)
    assert report.metrics['closure_moment_fraction'] > 1e-3


def test_open_dry_hatch_cover_relocates_weight_without_buoyancy_rejection():
    from stowage.checker.strength import check_strength
    from stowage.model import ShipState
    from stowage.model.fixtures import build_instance
    from stowage.vessel.fleet import load_vessel

    instance = build_instance()
    instance.vessel = _regression_vessel('feeder_500')
    cover = instance.vessel.hatch_covers[0]
    ship = ShipState(vessel_id=instance.vessel.id,covers_off={cover.id:'quay'})
    report = check_strength(instance,ship,call_index=0)
    assert report.ran, report.error
    expected = instance.vessel.hull.lightship_mass_kg-cover.mass_kg
    expected += sum(load[3] for load in instance.vessel.fixed_loads)
    assert report.metrics['displacement_kg'] == pytest.approx(expected)
    assert report.metrics['closure_moment_fraction'] < 1e-8


def test_immersed_open_coaming_is_a_hard_physical_violation(monkeypatch):
    from copy import deepcopy
    from stowage.checker import strength
    from stowage.model import ShipState
    from stowage.model.fixtures import build_instance

    instance = build_instance()
    instance.vessel = _regression_vessel('feeder_500')
    cover = instance.vessel.hatch_covers[0]
    ship = ShipState(vessel_id=instance.vessel.id,covers_off={cover.id:'quay'})
    params,geometry = strength._geometry('feeder_500')
    params = deepcopy(params)
    # Inject a genuinely submerged coaming lip; root geometry remains
    # independently fixed so this tests only the open-lip immersion gate.
    for coaming in params['envelope']['hatch_coamings']:
        if coaming['id'] == cover.id:
            coaming['z_max_m'] = -100.
    monkeypatch.setattr(strength,'_geometry',lambda name:(params,geometry))
    report = strength.check_strength(instance,ship,call_index=0)
    assert report.ran
    assert report.error is None
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.rule_id == 'port.stability.cargo_operations'
    assert violation.severity == 'hard'
    assert violation.call_index == 0
    assert 'immersed' in violation.message


def test_missing_open_coaming_geometry_remains_unavailable(monkeypatch):
    from copy import deepcopy
    from stowage.checker import strength
    from stowage.model import ShipState
    from stowage.model.fixtures import build_instance

    instance = build_instance()
    instance.vessel = _regression_vessel('feeder_500')
    cover = instance.vessel.hatch_covers[0]
    ship = ShipState(vessel_id=instance.vessel.id,covers_off={cover.id:'quay'})
    params,geometry = strength._geometry('feeder_500')
    params = deepcopy(params)
    params['envelope']['hatch_coamings'] = [c for c in params['envelope']['hatch_coamings']
                                           if c['id'] != cover.id]
    monkeypatch.setattr(strength,'_geometry',lambda name:(params,geometry))
    report = strength.check_strength(instance,ship,call_index=0)
    assert not report.ran
    assert 'lacks coaming geometry' in report.error
    assert report.violations[0].rule_id == 'checker.missing_vessel_data'
    assert report.violations[0].severity == 'hard'


def test_buoyant_overhangs_contribute_to_sf_bm_at_perpendiculars():
    # Uniform box extends 5 m beyond both perpendiculars. All weight is
    # inside AP/FP: the overhang buoyancy must reach the AP/FP cut forces.
    x, weights = _gauss_grid([-5, 0, 17, 50, 83, 100, 105])
    stations = np.array([-5., 0., 50., 100., 105.])
    curve = integrate_strength(x, weights, np.full_like(x, 1000*G),
        [(0., 100., 110_000.)], [], stations, lpp_m=100.)
    assert curve.raw_end_shear_n == pytest.approx(0, abs=1e-7)
    assert curve.raw_end_bending_nm == pytest.approx(0, abs=1e-5)
    assert curve.shear_n[1] == pytest.approx(5000*G)
    assert curve.shear_n[3] == pytest.approx(-5000*G)
    assert curve.bending_nm[1] == pytest.approx(-1000*G*5**2/2)
    assert curve.bending_nm[3] == pytest.approx(-1000*G*5**2/2)
    np.testing.assert_allclose(curve.shear_n[[0, 4]], 0, atol=1e-7)
    np.testing.assert_allclose(curve.bending_nm[[0, 4]], 0, atol=1e-5)


def test_affine_closure_uses_full_shifted_span_and_accepts_overhang_loads():
    x, weights = _gauss_grid([-6, 0, 27, 80, 107])
    stations = np.array([-6., 0., 107.])
    q = 125+17*(x+6)
    # Pure affine load is removed exactly over 113m, not the 100m rule length.
    curve = integrate_strength(x, weights, q, [], [], stations, lpp_m=100.)
    np.testing.assert_allclose(curve.shear_n, 0, atol=1e-7)
    np.testing.assert_allclose(curve.bending_nm, 0, atol=1e-5)
    assert curve.raw_end_shear_n == pytest.approx(125*113+17*113**2/2)
    # Loads legitimately situated outside the perpendiculars are retained.
    curve = integrate_strength(x, weights, np.zeros_like(x),
        [(-5., -1., 4000.)], [(104., 1000.)], stations, lpp_m=100., close=False)
    assert curve.raw_end_shear_n == pytest.approx(-5000*G)
    assert curve.raw_end_bending_nm == pytest.approx(G*(4000*110+1000*3))
