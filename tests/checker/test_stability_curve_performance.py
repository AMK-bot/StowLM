"""Exact-state reuse must remove work without changing assessment coverage."""

from collections import Counter
import math

import numpy as np
import pytest

from stowage.checker.stability_curve import build_stability_curve
from tests.checker.test_stability_curve import EndpointGeometry, condition


@pytest.mark.parametrize('flood,vanish,sealed', [
    (42., 30., False), (20., 60., False), (55., 40.4, False), (42., 30., True),
])
def test_each_curve_state_is_solved_and_corrected_once(flood, vanish, sealed):
    geometry = EndpointGeometry(flood=flood, vanish=vanish, sealed=sealed)
    corrected = []
    def correction(loading, angles, trim):
        corrected.extend((float(phi), float(trim)) for phi in angles)
        return np.zeros(len(angles))
    curve = build_stability_curve(geometry, condition(), 1025., correction_solver=correction)
    # Flooding probes often coincide exactly with grid knots. The already
    # converged state is reusable; neither its solve nor liquid correction
    # needs repeating. Exact float keys deliberately avoid angular rounding.
    assert max(Counter(geometry.calls).values()) == 1
    assert max(Counter(corrected).values()) == 1
    before = len(geometry.calls)
    for phi, expected_gz, expected_drafts in zip(curve.angles, curve.gz, curve.drafts):
        gz, drafts = curve.at_angle(float(phi))
        assert gz == pytest.approx(expected_gz, abs=1e-9)
        assert drafts == pytest.approx(expected_drafts)
    assert len(geometry.calls) == before
    assert math.degrees(curve.angles[-1]) >= min(flood, 40., max(35., vanish))


def test_curve_state_reuse_does_not_cross_loading_conditions():
    geometry = EndpointGeometry()
    correction = lambda loading, angles, trim: np.zeros(len(angles))
    upright = condition()
    first = build_stability_curve(geometry, upright, 1025., correction_solver=correction)
    first_calls = Counter(geometry.calls)
    listed = condition()
    listed.tcg_m = .05
    second = build_stability_curve(geometry, listed, 1025., correction_solver=correction)
    # Reusing the hull object does not reuse a different condition's roots.
    assert Counter(geometry.calls)[0.] == first_calls[0.]+1
    assert second.gz[0] == pytest.approx(first.gz[0]-.05)


@pytest.mark.slow
def test_curve_orchestration_matches_frozen_reference_across_fleet_conditions():
    """Twelve direct-geometry dry/wet/listed cases against the pinned baseline.

    The reference is shipped beside this file; no temporary files, generated
    caches or qualified-table campaign are required. Both paths intentionally
    share today's geometry/liquid kernels, isolating the orchestration change.
    """
    from stowage.checker.stability_geometry import StabilityGeometry
    from stowage.checker.stability_loading import LoadingCondition, loading_condition
    from stowage.vessel.fleet import load_ship_params
    from tests.checker import test_stability_curve_performance_reference as reference
    from tests.checker.test_stability_review import balanced_feeder

    def calculate(builder, params, scenario, instance):
        geometry = StabilityGeometry(params)
        draught = geometry.design_draught_m
        if instance is None:
            submerged = geometry.full_submerged(draught, draught)
            loading = LoadingCondition(submerged.displacement_kg, submerged.lcb_m,
                .03 if scenario == 'dry_listed' else 0., 6., 0., 0.)
        else:
            loading = loading_condition(instance, instance.onboard, geometry=geometry,
                ship_params=params, precomputed_soundings=True, small_tank_exemption=True)
            assert not loading.errors, loading.errors
        solved_states = 0
        original = geometry.free_trim_curve
        def counted(*args, **kwargs):
            nonlocal solved_states
            result = original(*args, **kwargs)
            solved_states += len(result[0])
            return result
        geometry.free_trim_curve = counted
        curve = builder(geometry, loading, 1025., initial_drafts=(draught, draught))
        return curve, solved_states

    for name in ('feeder_500', 'feeder_800', 'feeder_1700'):
        params = load_ship_params(name)
        for scenario in ('dry_symmetric', 'dry_listed', 'wet_symmetric', 'wet_asymmetric'):
            instance = None
            if scenario.startswith('wet'):
                instance = balanced_feeder(name, half_ballast=True)
                if scenario == 'wet_asymmetric':
                    masses = dict(instance.onboard.tank_masses_kg)
                    masses['DB-M-S'] *= .4
                    instance.onboard.tank_masses_kg = masses
            before, before_states = calculate(reference.build_stability_curve, params, scenario, instance)
            after, after_states = calculate(build_stability_curve, params, scenario, instance)
            for field in ('angles', 'gz', 'negative_gz', 'drafts'):
                # Much tighter than any regulatory validation tolerance. This
                # permits only root-solver roundoff from different valid seeds.
                np.testing.assert_allclose(getattr(before, field), getattr(after, field),
                    rtol=0., atol=1e-7, err_msg=f'{name}/{scenario}/{field}')
            assert before.flooding_angle == after.flooding_angle, (name, scenario)
            assert after_states < before_states, (name, scenario, before_states, after_states)
