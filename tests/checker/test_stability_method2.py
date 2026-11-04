"""IS Code B3.1.9.2 upright-inertia booklet method and B3.1.12 exemption.

The permitted method raises virtual KG by FSM/displacement; its signed lever
correction is therefore FSC sin(heel). Tests keep the actual-transfer method as
an independent finite-angle verification path rather than silently replacing it.
"""
from math import radians, sin

import numpy as np
import pytest

from stowage.checker.stability_loading import (
    LoadingCondition, curve_correction, loading_condition,
)
from stowage.model import ShipState
from tests.checker.test_stability_policy_fixtures import build_instance
from stowage.vessel.tanks import build_tanks


def _condition(*, lengths=(1.,), fraction=.5, kind='ballast'):
    """rho I/lightship=.01 exactly for a one-metre-long, 12 m wide tank."""
    instance = build_instance()
    instance.vessel.hull.lightship_mass_kg = 14_400_000.
    raw = [dict(id=f'tank-{index}', kind=kind,
                content_density_kg_m3=1000., capacity_m3=24.*length,
                x_aft_m=10., x_fore_m=10.+length,
                geometry=dict(x_min_m=10., x_max_m=10.+length,
                              y_min_m=-6., y_max_m=6.,
                              z_min_m=0., z_max_m=2.))
           for index, length in enumerate(lengths)]
    params = {'tanks': raw}
    instance.vessel.tanks = build_tanks(params)
    state = ShipState(vessel_id=instance.vessel.id,
                      tank_masses_kg={tank.id: fraction*tank.capacity_m3*1000.
                                      for tank in instance.vessel.tanks})
    return instance, state, params


def test_method2_is_signed_sine_of_upright_inertia_without_liquid_geometry():
    # No geometry objects: method .2 must use the supplied upright FSM only.
    condition = LoadingCondition(1e6, 30., .1, 5., .2, 2e5)
    angles = np.radians([-65., -30., -1., 0., 1., 30., 65.])
    actual = curve_correction(condition, angles, method=2)
    np.testing.assert_allclose(actual, .2*np.sin(angles), atol=1e-15)
    assert actual[3] == 0.
    assert actual == pytest.approx(-actual[::-1])


@pytest.mark.parametrize('trim', [0., .03, -.04])
def test_method2_booklet_correction_uses_heel_not_a_new_transfer_solve(trim):
    condition = LoadingCondition(1e6, 30., 0., 5., .35, 3.5e5)
    angles = np.radians([0., 10., 30., 55.])
    actual = curve_correction(condition, angles, trim, method=2)
    assert actual == pytest.approx(.35*np.sin(angles), abs=1e-15)


@pytest.mark.parametrize('length,omitted', [(.75, True), (1., False), (1.25, False)])
def test_small_tank_exemption_is_strict_and_uses_minimum_displacement(length, omitted):
    instance, state, params = _condition(lengths=(length,))
    result = loading_condition(instance, state, ship_params=params,
                               free_surface_method=2)
    assert not result.errors
    # Loading is heavier than lightship (fixed loads and the liquid itself).
    # Dividing by actual departure mass would incorrectly omit the equality case.
    assert result.mass_kg > instance.vessel.hull.lightship_mass_kg
    expected = 0. if omitted else 144_000.*length
    assert result.fsm_kgm == pytest.approx(expected)
    assert result.fsc_m == pytest.approx(expected/result.mass_kg)
    assert bool(result.omitted_tanks) is omitted
    assert len(result.actual_tanks) == 1  # Exemption does not discard liquid mass.
    empty = loading_condition(instance, ShipState(vessel_id=instance.vessel.id),
                              ship_params=params, free_surface_method=2)
    assert result.mass_kg-empty.mass_kg == pytest.approx(
        sum(state.tank_masses_kg.values()))


def test_small_tank_exemption_is_per_tank_not_the_sum_of_a_transverse_pair():
    instance, state, params = _condition(lengths=(.75, .75))
    result = loading_condition(instance, state, ship_params=params,
                               free_surface_method=2)
    # Each 0.0075 m is exempt, although their sum exceeds 0.01 m.
    assert result.fsm_kgm == 0.
    assert len(result.omitted_tanks) == 2
    assert len(result.actual_tanks) == 2
    assert result.mass_kg > instance.vessel.hull.lightship_mass_kg


def test_exemption_changes_correction_without_changing_mass_moments():
    instance, state, params = _condition(lengths=(.75,))
    verification = loading_condition(instance, state, ship_params=params,
                                     free_surface_method=1)
    production = loading_condition(instance, state, ship_params=params,
                                   free_surface_method=2)
    assert verification.fsm_kgm > 0.
    assert production.fsm_kgm == 0.
    for field in ('mass_kg', 'lcg_m', 'tcg_m', 'kg_m'):
        assert getattr(production, field) == pytest.approx(getattr(verification, field))
    assert not verification.omitted_tanks


def test_method2_consumable_maximum_survives_nominal_full_actual_fill():
    instance, state, params = _condition(lengths=(1.25,), fraction=1., kind='fuel_oil')
    result = loading_condition(instance, state, ship_params=params,
                               free_surface_method=2)
    # A pressed-full current tank has zero instantaneous free surface; carried
    # consumables nevertheless use the most adverse envisaged slack filling.
    assert result.fsm_kgm == pytest.approx(180_000.)
    assert result.tanks[0].variable
    assert not result.omitted_tanks
    assert curve_correction(result, [radians(30.)], method=2)[0] == pytest.approx(
        result.fsc_m*.5)


def test_actual_transfer_remains_distinct_at_large_heel_and_matches_initial_gm():
    instance, state, params = _condition(lengths=(1.25,))
    condition = loading_condition(instance, state, ship_params=params,
                                  free_surface_method=1)
    angle = 1e-4
    transfer = curve_correction(condition, [angle], method=1)[0]
    assert transfer/angle == pytest.approx(condition.fsc_m, rel=1e-6)
    heel = radians(50.)
    actual = curve_correction(condition, [heel], method=1)[0]
    inertia = curve_correction(condition, [heel], method=2)[0]
    assert inertia == pytest.approx(condition.fsc_m*sin(heel))
    assert abs(actual-inertia) > .001


def test_legacy_loading_and_transfer_defaults_remain_verification_method():
    instance, state, params = _condition(lengths=(.75,))
    default = loading_condition(instance, state, ship_params=params)
    explicit = loading_condition(instance, state, ship_params=params,
                                 free_surface_method=1)
    assert default.fsm_kgm == explicit.fsm_kgm > 0.
    assert curve_correction(default, [.6]) == pytest.approx(
        curve_correction(explicit, [.6], method=1))


@pytest.mark.parametrize('selected,expected', [(None, 1), ('1', 1), ('2', 2)])
def test_production_selects_one_booklet_method_per_check(monkeypatch, selected, expected):
    from stowage.checker import stability
    instance = build_instance()
    state = ShipState(vessel_id=instance.vessel.id)
    if selected is None:
        monkeypatch.delenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD', raising=False)
    else:
        monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD', selected)
    monkeypatch.setattr(stability, '_context', lambda vessel: (None, {}))
    selected_methods = []
    def capture_loading(*args, **kwargs):
        selected_methods.append(kwargs['free_surface_method'])
        # Isolate dispatch from the expensive physical hull calculation.
        return LoadingCondition(1e6, 20., 0., 5., 0., 0.,
                                errors=('test stops after booklet method selection',))
    monkeypatch.setattr(stability, 'loading_condition', capture_loading)
    report = stability.check_stability(instance, state, call_index=0)
    assert selected_methods == [expected]
    assert not report.ran
    assert report.error == 'test stops after booklet method selection'


def test_method2_free_trim_uses_virtual_rise_without_actual_liquid_integration(monkeypatch):
    from stowage.checker import stability_curve
    from stowage.checker.stability_loading import CorrectionTank

    def forbidden_transfer(*args, **kwargs):
        raise AssertionError('production method .2 must not integrate liquid transfer')
    monkeypatch.setattr(stability_curve, 'liquid_cg', forbidden_transfer)

    class PoisonLiquidGeometry:
        liquid = sounding = forbidden_transfer

    class FreeTrimGeometry:
        lpp_m = 100.
        openings = [dict(x_m=50., y_m=7., z_m=5.+7.*np.tan(radians(40.)))]

        def __init__(self):
            self.callback_centres = []
            self.physical_kgs = []

        def _axes(self, *args):
            return np.array([0., 0., 1.]), np.array([1., 0., 0.]), np.array([0., 1., 0.])

        def free_trim_curve(self, angles, mass, lcg, kg, density, *,
                            cg_callback=None, initial_drafts=None, stop_when=None):
            arms, drafts = [], []
            for phi in angles:
                self.callback_centres.append(cg_callback(float(phi), .03))
                self.physical_kgs.append(kg)
                raw = (2.-kg)*np.sin(phi)
                arms.append(raw)
                drafts.append((5., 5.))
                if stop_when is not None and stop_when(float(phi), raw, (5., 5.)):
                    break
            return np.array(arms), np.array(drafts)

    tank = CorrectionTank('poison', 1000., 1., 100., PoisonLiquidGeometry())
    condition = LoadingCondition(1e6, 50., 0., 1., .1, 1e5,
                                 actual_tanks=(tank,))
    geometry = FreeTrimGeometry()
    curve = stability_curve.build_stability_curve(
        geometry, condition, 1025., free_surface_method=2)
    assert geometry.callback_centres
    assert np.asarray(geometry.callback_centres) == pytest.approx(
        np.tile([50., 0., 1.1], (len(geometry.callback_centres), 1)))
    assert geometry.physical_kgs == [1.]*len(geometry.physical_kgs)
    # The raw lever uses physical KG; subtract the virtual-rise lever once.
    assert curve.gz == pytest.approx(.9*np.sin(curve.angles), abs=1e-14)
    assert curve.flooding_angle == pytest.approx(radians(40.), abs=1e-8)
