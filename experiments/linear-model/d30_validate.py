"""Independent physical held-out checks of D30's default directional reserves.

Run cache_warm first. Global component versions are recorded as provenance;
only physical content, the reference dispatch and margin-policy inputs can
invalidate this experiment. Never relax an equality or error tolerance here.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import resource
import runpy
import time

import numpy as np

HERE = Path(__file__).resolve().parent


def serializable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [serializable(item) for item in value]
    return value


def maximum_excess(observed, reserved):
    """Keep the maximum excess at every station; zero is a genuine pass."""
    return np.maximum(np.asarray(observed)-np.asarray(reserved), 0.).tolist()


def public_signed_evidence(affine, stability, strength, stations, reserves, gm_min_m):
    """Check actual predictor-minus-public residuals at every physical bound.

    This single captured state is a dispatch crosscheck, not a percentile
    population. Opposite-sign gaps are conservatism, not required reserves.
    List uses the same positive-restoring/violating-branch eligibility as the
    held-out statistics. Missing or nonfinite public metrics fail closed.
    """
    actual = {name: float(stability[key]) for name, key in (
        ('gm_m', 'gm_m'), ('trim_m', 'trim_m'), ('list_rad', 'list_rad'),
        ('draught_m', 'draught_mean_m'), ('draught_aft_m', 'draught_aft_m'),
        ('draught_forward_m', 'draught_fwd_m'))}
    prediction = dict(affine)
    prediction['draught_aft_m'] = affine['draught_m']+.5*affine['trim_m']
    prediction['draught_forward_m'] = affine['draught_m']-.5*affine['trim_m']
    for name in ('sf_n', 'bm_nm'):
        actual[name] = np.asarray([strength[f'{name}@{float(x):.12g}'] for x in stations])
    residuals = {name: np.asarray(prediction[name])-value for name, value in actual.items()}
    if not all(np.isfinite(value).all() for value in residuals.values()):
        raise ValueError('public crosscheck requires finite metrics at every bound/station')
    unsafe = {side: {} for side in ('lower_unsafe', 'upper_unsafe')}
    safe = {side: {} for side in ('lower_conservatism', 'upper_conservatism')}
    excess = {}
    eligibility = {}
    for name, residual in residuals.items():
        for side, sign in (('lower_unsafe', 1.), ('upper_unsafe', -1.)):
            if name == 'gm_m' and side == 'upper_unsafe':
                continue
            eligible = name != 'list_rad' or (affine['gm_m'] >= gm_min_m and
                (actual[name] < 0 if side == 'lower_unsafe' else actual[name] > 0))
            eligibility[f'{side}/{name}'] = bool(eligible)
            if not eligible:
                continue
            observed = np.maximum(sign*residual, 0.)
            unsafe[side][name] = observed.tolist()
            safe[side.replace('unsafe', 'conservatism')][name] = np.maximum(-sign*residual, 0.).tolist()
            excess[f'{side}/{name}'] = maximum_excess(observed, reserves[side][name])
    return dict(actual_metrics=actual, affine_solver_signed_residuals=residuals,
                affine_solver_unsafe=unsafe, affine_solver_conservatism=safe,
                bound_eligibility=eligibility, unsafe_excess_over_default_reserves=excess)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ship', required=True, choices=('feeder_500', 'feeder_1000', 'feeder_1700'))
    parser.add_argument('--level', required=True, choices=('light', 'medium', 'full'))
    parser.add_argument('--samples', type=int, default=128)
    parser.add_argument('--seed', type=int, default=3791)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from stowage.linear import build_linear_model, linear_constraints
    from stowage.linear.bands import band_for_instance
    from stowage.linear import physics
    from stowage.linear.cache import source_fingerprint
    from stowage.linear.dependencies import dependency_fingerprint
    from stowage.linear.family_calibration import reference_inputs, reference_cache_identity
    from stowage.linear.margin_policy import default_reserves, policy_inputs
    from stowage.vessel.fleet import load_ship_params
    from stowage.versions import component_versions

    helper = runpy.run_path(str(HERE/'narrow.py'))
    started = time.perf_counter()
    parameters = load_ship_params(args.ship)
    def physics_inputs():
        return {'coefficient_builder': dependency_fingerprint('coefficient'),
                'calibration_builder': dependency_fingerprint('calibration'),
                'parameters': helper['input_snapshot'](args.ship),
                'reference': reference_cache_identity(reference_inputs(parameters)),
                'margin_policy': policy_inputs()}
    before = physics_inputs()
    audit_start = {'source': source_fingerprint(), 'versions': component_versions()}
    instance, manifest = helper['cargo_instance'](args.ship, args.level)
    # Validate the production default band, rather than the historical study's
    # extra +/-0.4 m trim restriction. Cargo mass is still fixed per departure.
    band = band_for_instance(instance)
    model = build_linear_model(instance, band=band)
    reserves = default_reserves(model)
    assert reserves is not None, 'builder must install the default D30 margin policy'
    constraints = linear_constraints(model)  # Exercise the unmodified defaults.
    empty_intervals = [name for name, lo, hi in zip(constraints.names, constraints.lb, constraints.ub)
                       if lo > hi]
    zero_rows = np.all(constraints.a_cell == 0., axis=1) & np.all(constraints.a_tank == 0., axis=1)
    impossible_constant_rows = [name for name, zero, value, lo, hi in zip(
        constraints.names, zero_rows, constraints.const, constraints.lb, constraints.ub)
        if zero and not lo <= value <= hi]
    built = time.perf_counter()
    captured = []
    original = physics.exact_condition
    def capture(*positional, **keywords):
        exact = original(*positional, **keywords)
        if not captured and (band.displacement_min_kg <= exact['displacement_kg'] <= band.displacement_max_kg
                and band.draught_min_m <= exact['draught_m'] <= band.draught_max_m
                and abs(exact['trim_m']) <= band.max_abs_trim_m
                and abs(exact['list_rad']) <= band.max_abs_heel_rad):
            cell, tank = positional[2].copy(), positional[3].copy()
            captured.append((cell, tank, keywords['cell_vcg_m'].copy(), exact,
                             physics._linear_arrays(model, cell, tank, instance=instance, solver=True)))
        return exact
    if args.level == 'light':
        physics.exact_condition = capture
    try:
        errors, diagnostics = physics.measure_errors(model, instance, samples=args.samples, seed=args.seed)
    finally:
        physics.exact_condition = original
    swept = time.perf_counter()
    excess = {side: {metric: maximum_excess(values, reserves[side][metric])
                    for metric, values in diagnostics['signed_errors'][side].items()
                    if metric != 'gm_m' or side == 'lower_unsafe'}
              for side in ('lower_unsafe', 'upper_unsafe')}
    public = None
    public_covered = args.level != 'light'
    if captured:
        public = helper['public_crosscheck'](model, instance, captured[0][:4])
        public['captured_affine_prediction'] = captured[0][4]
        # Compare the actual affine prediction directly with public physics.
        # An absolute reference gap can be absorbed by existing conservatism;
        # it must not be reported as an observed unsafe solver residual.
        public['captured_sample'] = {'cell_masses_kg': captured[0][0],
                                    'tank_masses_kg': captured[0][1],
                                    'cell_vcg_m': captured[0][2]}
        try:
            evidence = public_signed_evidence(captured[0][4], public['stability_metrics'],
                public['strength_metrics'], model.station_x_m, reserves, model.gm_min_m)
            public.update(evidence)
            public_covered = all(np.max(value) == 0 for value in
                                 evidence['unsafe_excess_over_default_reserves'].values())
        except (KeyError, TypeError, ValueError) as exc:
            public['signed_validation_error'] = str(exc)
            public_covered = False
    after = physics_inputs()
    stable = before == after
    covered = all(np.max(value) == 0 for metrics in excess.values() for value in metrics.values())
    finished = time.perf_counter()
    result = {'ship': args.ship, 'level': args.level, 'pid': os.getpid(),
              'seed': args.seed, 'samples': args.samples, 'band': asdict(band),
              'manifest': manifest, 'physical_inputs_start': before, 'physical_inputs_end': after,
              'physical_inputs_unchanged': stable,
              'global_audit_start': audit_start,
              'global_audit_end': {'source': source_fingerprint(), 'versions': component_versions()},
              'calibration': model.measurement_diagnostics,
              'default_reserves': reserves, 'default_constraint_names': constraints.names,
              'empty_default_intervals': empty_intervals,
              'impossible_default_constant_rows': impossible_constant_rows,
              'default_structurally_feasible': not (empty_intervals or impossible_constant_rows),
              'default_intervals_nonempty': not empty_intervals,
              'public_crosscheck_population': 'one captured physical state at light cargo only; no public p99 estimate',
              'measured_absolute_errors': asdict(errors), 'diagnostics': diagnostics,
              'unsafe_excess_over_default_reserves': excess,
              'all_measured_unsafe_errors_covered': covered,
              'captured_public_residuals_covered': public_covered,
              'public_checker_comparison': public,
              'timings_seconds': {'context_and_build': built-started, 'heldout': swept-built,
                                  'public_check': finished-swept, 'total': finished-started},
              'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,
              'warning': 'Empirical held-out validation, not a uniform proof or full-plan acceptance.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(serializable(result), indent=2, allow_nan=False)+'\n'
    args.output.write_text(payload)
    print(json.dumps({'path': str(args.output), 'sha256': hashlib.sha256(payload.encode()).hexdigest(),
                      'covered': covered, 'public_covered': public_covered,
                      'stable': stable, 'seconds': finished-started}), flush=True)
    if not stable or not covered or not public_covered or empty_intervals or impossible_constant_rows:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
