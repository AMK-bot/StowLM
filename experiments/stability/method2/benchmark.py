"""Measure the selected IS B3.1.9.2 production method and transfer verification.

Use a fresh process per vessel. Vessel/instance construction is measured separately
and excluded from check timings. Existing on-disk qualified KN and sounding caches
are required for the cached cold benchmark; this script never qualifies a cache.
The first production check has empty checker process caches; five further checks
measure same-process warm performance. A forced-direct method .2 check measures
the loaded condition's interpolation error. Optional method .1 runs afterwards
once. By default an experiment-only loading wrapper applies production's same
small-tank omission IDs to method .1; the ordinary verification mode is unchanged.
Use --no-same-exemptions to compare the legacy verification policy instead.

Pointwise comparisons interpolate the recorded numerical curves piecewise linearly
on their union of nodes inside their common angular coverage. Consequently they
measure the represented curves, without inventing an extension beyond flooding or
vanishing stability. Reported criterion-area differences use checker metrics.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np

from stowage.checker import stability, stability_curve, stability_sounding
from stowage.versions import component_versions
from stowage.vessel.fleet import load_ship_params

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('method2_wet_departures', ROOT.parent/'hull_tanks'/'departures.py')
_departure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_departure)
FLEET = ('feeder_500', 'feeder_800', 'feeder_1700')


def save(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.partial')
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


@contextmanager
def selected_method(method):
    key = 'STOWAGE_STABILITY_FREE_SURFACE_METHOD'
    previous = os.environ.get(key)
    os.environ[key] = str(method)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


@contextmanager
def force_direct():
    key = 'STOWAGE_STABILITY_DIRECT'
    previous = os.environ.get(key)
    os.environ[key] = '1'
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def run_check(instance, method, *, same_exemptions=True):
    """Capture every completed curve, including a final direct fallback curve."""
    captured = []
    original = stability_curve.build_stability_curve
    original_loading = stability.loading_condition
    loading_details = []
    def load(*args, **kwargs):
        condition = original_loading(*args, **kwargs)
        unfiltered_fsc = condition.fsc_m
        omitted = condition.omitted_tanks
        if method == 1 and same_exemptions:
            production_kwargs = dict(kwargs, free_surface_method=2)
            production = original_loading(*args, **production_kwargs)
            omitted = production.omitted_tanks
            tanks = tuple(tank for tank in condition.tanks if tank.id not in omitted)
            fsm = sum(tank.fsm_kgm for tank in tanks)
            # Controlled experiment only: retain actual liquid mass/centres and
            # shape-aware motion, apply exactly the production omission IDs to
            # the regulatory transfer/FSM correction. Production is unmodified.
            condition = replace(condition, tanks=tanks, fsm_kgm=fsm,
                fsc_m=fsm/condition.mass_kg, omitted_tanks=omitted)
        loading_details.append(dict(omitted_tank_ids=list(omitted),
            omitted_tank_count=len(omitted), correction_fsc_m=condition.fsc_m,
            unfiltered_method_fsc_m=unfiltered_fsc,
            exemption_effect_on_fsc_m=condition.fsc_m-unfiltered_fsc,
            loading_centroid_m=[condition.lcg_m, condition.tcg_m, condition.kg_m]))
        return condition
    def capture(*args, **kwargs):
        curve = original(*args, **kwargs)
        captured.append(curve)
        return curve
    stability_curve.build_stability_curve = capture
    stability.loading_condition = load
    start = perf_counter()
    try:
        with selected_method(method):
            report = stability.check_stability(instance, instance.onboard, call_index=0)
        elapsed = perf_counter()-start
        result = dict(seconds=elapsed, ran=report.ran, error=report.error,
            metrics=report.metrics, margins=report.margins,
            violation_ids=sorted({v.rule_id for v in report.violations}),
            violations=[v.model_dump(mode='json') for v in report.violations],
            curve_build_count=len(captured))
        result['exemption_policy'] = ('same production omission IDs in both methods; experiment-only method1 override'
            if same_exemptions else 'legacy method1 retains every tank; production method2 applies small-tank exemption; GZ differences include exemption policy')
        result['loading_details'] = loading_details[-1] if loading_details else None
        if captured:
            curve = captured[-1]
            result['curve'] = dict(angles_rad=curve.angles.tolist(),
                gz_m=curve.gz.tolist(), negative_gz_m=curve.negative_gz.tolist(),
                flooding_angle_rad=float(curve.flooding_angle), drafts_m=curve.drafts.tolist())
        return result
    finally:
        stability_curve.build_stability_curve = original
        stability.loading_condition = original_loading


def compare(one, two):
    """Signed deltas are method .2 minus method .1 throughout."""
    a, b = one['curve'], two['curve']
    aa, bb = np.asarray(a['angles_rad']), np.asarray(b['angles_rad'])
    end = min(float(aa[-1]), float(bb[-1]))
    nodes = np.unique(np.r_[aa[aa <= end], bb[bb <= end], end])
    g1 = np.interp(nodes, aa, a['gz_m'])
    g2 = np.interp(nodes, bb, b['gz_m'])
    delta = g2-g1
    index = int(np.argmax(abs(delta)))
    area_deltas = {key: two['metrics'][key]-value for key, value in one['metrics'].items()
                   if 'area_' in key and key.endswith('mrad') and key in two['metrics']
                   and isinstance(value, (float, int)) and isinstance(two['metrics'][key], (float, int))}
    return dict(sign='method2 minus method1', common_end_rad=end,
        exemption_policy=one['exemption_policy'],
        method1_omitted_tank_count=one['loading_details']['omitted_tank_count'],
        method2_omitted_tank_count=two['loading_details']['omitted_tank_count'],
        common_end_deg=float(np.degrees(end)), max_abs_gz_difference_m=float(abs(delta[index])),
        signed_gz_difference_at_max_m=float(delta[index]),
        max_difference_angle_deg=float(np.degrees(nodes[index])),
        rms_gz_difference_m=float(np.sqrt(np.mean(delta**2))),
        common_interval_signed_area_difference_mrad=float(np.trapezoid(delta, nodes)),
        common_interval_absolute_difference_area_mrad=float(np.trapezoid(abs(delta), nodes)),
        criterion_area_metric_differences=area_deltas,
        gm_difference_m=two['metrics'].get('gm_m', 0)-one['metrics'].get('gm_m', 0),
        method1_violation_ids=one['violation_ids'], method2_violation_ids=two['violation_ids'],
        angles_rad=nodes.tolist(), method1_gz_m=g1.tolist(), method2_gz_m=g2.tolist(),
        gz_difference_m=delta.tolist())


def run(name, output, method1=False, same_exemptions=True):
    if os.environ.get('STOWAGE_STABILITY_DIRECT') == '1':
        raise ValueError('benchmark requires normal qualified-table dispatch; unset STOWAGE_STABILITY_DIRECT')
    params = load_ship_params(name)
    digest = hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()
    source_paths = sorted(Path(stability.__file__).parent.glob('stability*.py'))
    source_hashes = {str(path.relative_to(ROOT.parents[2])): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in source_paths}
    result = json.loads(output.read_text()) if output.exists() else {}
    if result and (result['parameters_sha256'] != digest or result['component_versions'] != component_versions()
                   or result.get('source_sha256') != source_hashes
                   or result.get('same_exemptions') != same_exemptions):
        raise ValueError('existing output provenance differs; use a fresh output path')
    started = perf_counter()
    instance, summary = _departure.build_departure(name)
    built = perf_counter()-started
    condition_digest = hashlib.sha256(instance.model_dump_json().encode()).hexdigest()
    if result and result['instance_sha256'] != condition_digest:
        raise ValueError('existing output condition differs; use a fresh output path')
    if not result:
        result = dict(summary, parameters_sha256=digest, instance_sha256=condition_digest,
            component_versions=component_versions(), source_sha256=source_hashes, instance_build_seconds=built,
            same_exemptions=same_exemptions,
            timing_scope='check_stability only; construction excluded; disk caches retained',
            complete=False, status='instance_built')
        save(output, result)
    # Construction does not invoke stability checks, but clear explicit checker
    # caches so the recorded first-check condition is reproducible nonetheless.
    stability._geometry.cache_clear()
    stability._parsed_parameter_payload.cache_clear()
    stability_sounding.lookup.cache_clear()
    if 'method2_cold' not in result:
        result['method2_cold'] = run_check(instance, 2, same_exemptions=same_exemptions)
        result['status'] = 'method2_cold_complete'
        save(output, result)
        if not result['method2_cold']['ran'] or result['method2_cold']['error']:
            raise RuntimeError('method2 check failed; partial output retained')
        if not result['method2_cold']['metrics'].get('geometry_table_used'):
            raise RuntimeError('qualified KN fast path was not used; timings retained but not cached-path results')
        result['method2_warm'] = []
        for _ in range(5):
            result['method2_warm'].append(run_check(instance, 2, same_exemptions=same_exemptions))
            save(output, result)
        times = [row['seconds'] for row in result['method2_warm']]
        result['method2_warm_statistics_seconds'] = dict(min=min(times), median=median(times), max=max(times))
        result['status'] = 'method2_complete'
        save(output, result)
    elif len(result.get('method2_warm', [])) != 5:
        raise ValueError('partial warm stage must be restarted with a fresh output to preserve same-process scope')
    if 'method2_direct' not in result:
        with force_direct():
            result['method2_direct'] = run_check(instance, 2, same_exemptions=same_exemptions)
        result['status'] = 'method2_direct_complete'
        save(output, result)
        if 'curve' in result['method2_direct'] and 'curve' in result['method2_cold']:
            result['interpolation_error_loaded'] = compare(result['method2_direct'], result['method2_cold'])
            result['interpolation_error_loaded']['sign'] = 'method2 table minus method2 direct'
            save(output, result)
    if method1 and 'method1' not in result:
        result['method1'] = run_check(instance, 1, same_exemptions=same_exemptions)
        result['status'] = 'method1_complete'
        save(output, result)
        if 'curve' in result['method1'] and 'curve' in result['method2_cold']:
            result['comparison'] = compare(result['method1'], result['method2_cold'])
            save(output, result)
    result.update(complete=True, status='complete', last_process_seconds=perf_counter()-started)
    save(output, result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vessel', choices=FLEET, required=True)
    parser.add_argument('--method1', action='store_true')
    parser.add_argument('--same-exemptions', action=argparse.BooleanOptionalAction, default=True,
        help='controlled method1 comparison uses production omitted tank IDs (default); --no-same-exemptions compares legacy policies too')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.vessel, args.output, args.method1, args.same_exemptions)
    print(json.dumps({key: result.get(key) for key in ('vessel', 'status', 'method2_warm_statistics_seconds', 'comparison')}, indent=2))
