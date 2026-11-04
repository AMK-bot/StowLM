"""Strength's coupled tank/hull root: failed hybr roots get a damped-Newton retry.

The two failure modes are the ones observed on feeder_1700's one-side T4 replay
(s963: a heel trial of -91.33 rad raised in the tank model; s996: hybr status 5,
"not making good progress"). Both came from MINPACK's relative finite-difference
step at an upright seed (heel ~1e-15 rad). The retry solves the same residual
and keeps the 2e-6 acceptance; it rides the D64 rule switch. Under the switch an
upright seed heel is snapped to 0 and every accepted root must lie on the stable
upright branch (logic-strength.md §2).
"""
from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest
from scipy.optimize import OptimizeResult

from stowage.checker import strength
from stowage.model import ShipState
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet import load_vessel


NEW_METRICS = ("equilibrium_newton_fallback", "equilibrium_restoring_slope_m", "equilibrium_basin_bound_rad")


def _tanked(*, asymmetric=True, raise_kg_m=0.):
    vessel = load_vessel("feeder_500", with_hull=True)
    vessel.hull.lightship_vcg_m += raise_kg_m
    instance = build_instance()
    instance.vessel = vessel.model_copy(deep=True)
    masses = {}
    for i, pair in enumerate(vessel.tank_pairs):
        for tid, fill in ((pair.port_tank, .5), (pair.starboard_tank, .5 + .1*(i % 2)*asymmetric)):
            tank = next(t for t in vessel.tanks if t.id == tid)
            masses[tid] = fill*tank.capacity_m3*tank.content_density_kg_m3
    instance.onboard = ShipState(vessel_id=vessel.id, tank_masses_kg=masses)
    return instance


@pytest.fixture(scope="module")
def tanked():
    return _tanked()


def _check(instance, *, enabled):
    with rule_freeze_v1(enabled=enabled):
        return strength.check_strength(instance, instance.onboard, call_index=0)


def _wild_heel_root(residual, seed, **kwargs):
    # s963: hybr's zero heel Jacobian column sent the trial to -91.33 rad.
    residual([seed[0], seed[1], -91.33341864082075])
    raise AssertionError("tank model accepted a >90 degree heel")


def _stalled_root(residual, seed, **kwargs):
    # s996: status 5 at a near-converged but unaccepted point.
    return OptimizeResult(x=np.asarray(seed, dtype=float), success=False, status=5,
                          message="The iteration is not making good progress")


def test_switch_off_report_has_no_new_diagnostics_and_on_records_the_branch(tanked):
    off, on = _check(tanked, enabled=False), _check(tanked, enabled=True)
    assert off.ran and on.ran, (off.error, on.error)
    assert not set(NEW_METRICS) & set(off.metrics)
    assert on.metrics["equilibrium_newton_fallback"] == 0.
    assert on.metrics["equilibrium_restoring_slope_m"] > 0
    assert on.metrics["equilibrium_basin_bound_rad"] == tanked.vessel.hull.max_list_rad+1e-3
    assert [v.rule_id for v in on.violations] == [v.rule_id for v in off.violations]
    for key in ("sf_ratio", "bm_ratio"):
        assert on.margins[key] == pytest.approx(off.margins[key], abs=1e-9)


def test_upright_seed_heel_is_snapped_to_exact_zero_only_under_switch(tanked, monkeypatch):
    params, geometry = strength._geometry("feeder_500")

    class TinyHeelSeed:
        # The observed s963/s996 seeds: upright to roundoff (~1e-15 rad).
        def __getattr__(self, name):
            return getattr(geometry, name)

        def equilibrium(self, *args):
            return dataclasses.replace(geometry.equilibrium(*args), heel_rad=1.0073324676173714e-15)

    seeds = []
    original = strength.root

    def recording_root(residual, seed, **kwargs):
        seeds.append(list(seed))
        return original(residual, seed, **kwargs)

    monkeypatch.setattr(strength, "_geometry", lambda name: (params, TinyHeelSeed()))
    monkeypatch.setattr(strength, "root", recording_root)
    assert _check(tanked, enabled=False).ran
    assert _check(tanked, enabled=True).ran
    assert seeds[0][2] == 1.0073324676173714e-15
    assert seeds[1][2] == 0.0 and math.copysign(1., seeds[1][2]) == 1.


@pytest.mark.parametrize("failing_root,legacy_message", [
    (_wild_heel_root, "heel and trim must be finite and less than 90 degrees"),
    (_stalled_root, "coupled tank/hull floating equilibrium did not converge"),
])
def test_failed_hybr_root_gets_same_equations_verdict(tanked, monkeypatch, failing_root, legacy_message):
    reference = _check(tanked, enabled=True)
    assert reference.ran, reference.error
    monkeypatch.setattr(strength, "root", failing_root)
    # Red: the legacy path (switch off) is unchanged and has no verdict.
    legacy = _check(tanked, enabled=False)
    assert not legacy.ran
    assert legacy.error == f"Strength input/equilibrium error: {legacy_message}"
    assert legacy.violations[0].rule_id == "checker.missing_vessel_data"
    # Green: the retry reaches the same root and verdict as the converged hybr path.
    retried = _check(tanked, enabled=True)
    assert retried.ran and retried.error is None, retried.error
    assert [v.rule_id for v in retried.violations] == [v.rule_id for v in reference.violations]
    for key in ("draught_aft_m", "draught_fwd_m"):
        assert retried.metrics[key] == pytest.approx(reference.metrics[key], abs=1e-7)
    assert retried.metrics["list_rad"] == pytest.approx(reference.metrics["list_rad"], abs=1e-9)
    for key in ("sf_ratio", "bm_ratio"):
        assert retried.margins[key] == pytest.approx(reference.margins[key], abs=1e-6)
    assert retried.metrics["equilibrium_newton_fallback"] == 1.
    assert retried.metrics["equilibrium_restoring_slope_m"] > 0


def test_unconverged_retry_fails_closed_as_internal_error(tanked, monkeypatch):
    monkeypatch.setattr(strength, "root", _stalled_root)
    monkeypatch.setattr(strength, "_damped_newton", lambda residual, seed: (np.asarray(seed), 3e-6))
    report = _check(tanked, enabled=True)
    assert not report.ran and not report.margins
    assert report.error == ("Strength input/equilibrium error: "
                            "coupled tank/hull floating equilibrium did not converge")
    assert [v.rule_id for v in report.violations] == ["checker.internal_error"]


def test_out_of_domain_seed_fails_closed_as_internal_error(tanked, monkeypatch):
    monkeypatch.setattr(strength, "root", _wild_heel_root)
    original = strength._damped_newton
    monkeypatch.setattr(strength, "_damped_newton",
                        lambda residual, seed: original(residual, [seed[0], seed[1], 2.]))
    report = _check(tanked, enabled=True)
    assert not report.ran
    assert "outside the hull/tank domain" in report.error
    assert [v.rule_id for v in report.violations] == ["checker.internal_error"]


def test_unstable_upright_root_never_gets_a_strength_verdict():
    # KG raised 8 m on a symmetric loading: GM < 0, so upright heel = 0 is an
    # exact but unstable root of the same residual. Legacy hybr accepts it.
    instance = _tanked(asymmetric=False, raise_kg_m=8.)
    legacy = _check(instance, enabled=False)
    assert legacy.ran and legacy.margins and abs(legacy.metrics["list_rad"]) < 1e-9
    guarded = _check(instance, enabled=True)
    assert not guarded.ran and not guarded.margins
    assert "outside the stability basin" in guarded.error and "unstable root" in guarded.error
    assert [v.rule_id for v in guarded.violations] == ["checker.internal_error"]


def test_newton_landing_on_unstable_root_is_rejected(monkeypatch):
    # The code review's case: retry from a near-upright seed lands on the
    # unstable upright equilibrium; the residual alone accepts it.
    instance = _tanked(asymmetric=False, raise_kg_m=8.)
    monkeypatch.setattr(strength, "root", _stalled_root)
    original = strength._damped_newton
    landed = []

    def newton(residual, seed):
        x, error = original(residual, [seed[0], seed[1], 2e-3])
        landed.append((x, error))
        return x, error

    monkeypatch.setattr(strength, "_damped_newton", newton)
    report = _check(instance, enabled=True)
    assert landed and landed[0][1] <= 2e-6
    assert not report.ran and not report.margins
    assert "unstable root" in report.error
    assert [v.rule_id for v in report.violations] == ["checker.internal_error"]


@pytest.mark.parametrize("heel,trim_m", [(.0349065850+2e-3, 0.), (0., -60.)])
def test_basin_guard_bounds_heel_and_trim_angle(tanked, heel, trim_m):
    hull = tanked.vessel.hull
    hull = hull.model_copy(update={"max_list_rad": .0349065850})
    with pytest.raises(strength.EquilibriumError, match="outside the stability basin"):
        strength._basin_guard(lambda v: [0., 0., 0.], [9.+trim_m, 9., heel], hull)


def test_linear_solve_failure_in_retry_is_internal_error(tanked, monkeypatch):
    monkeypatch.setattr(strength, "root", _stalled_root)
    monkeypatch.setattr(np.linalg, "lstsq", lambda *a, **k: (_ for _ in ()).throw(
        np.linalg.LinAlgError("SVD did not converge in Linear Least Squares")))
    report = _check(tanked, enabled=True)
    assert not report.ran and not report.margins
    assert "linear solve failed: SVD did not converge" in report.error
    assert [v.rule_id for v in report.violations] == ["checker.internal_error"]


def test_damped_newton_halves_steps_that_leave_the_domain():
    rejected = []

    def residual(v):
        ta, tf, heel = v
        if not abs(heel) < math.pi/2:
            rejected.append(heel)
            raise ValueError("heel and trim must be finite and less than 90 degrees")
        return [ta-9., tf-8.7, math.sin(heel+.05)]

    x, error = strength._damped_newton(residual, [9., 8.7, 1.5])
    assert rejected, "no out-of-domain trial: halving was not exercised"
    assert error <= 1e-12
    assert x == pytest.approx([9., 8.7, -.05], abs=1e-12)


def test_damped_newton_uses_absolute_steps_at_an_upright_seed():
    # Root at heel 2e-7 rad from an upright 1e-15 seed: a relative FD step
    # (~1e-23 rad) would see no heel sensitivity here.
    calls = []

    def residual(v):
        ta, tf, heel = v
        calls.append(heel)
        if not abs(heel) < math.pi/2:
            raise ValueError("heel and trim must be finite and less than 90 degrees")
        return [ta-9., (ta-tf)-.3, 50.*math.sin(heel-2e-7)+(heel-2e-7)**3]

    x, error = strength._damped_newton(residual, [9.1, 8.9, 1e-15])
    assert error <= 1e-12
    assert x == pytest.approx([9., 8.7, 2e-7], abs=1e-12)
    assert all(abs(h) < math.pi/2 for h in calls)
