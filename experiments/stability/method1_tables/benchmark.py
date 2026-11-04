"""Time cached actual-transfer checks against retained direct-method curves.

Fresh-process cold timings retain disk tables, but clear every known checker
in-process LRU before the first check. Vessel and instance assembly are recorded
separately. Five successive checks provide warm min/median/max. Reference .1
curves are the previously measured direct-tank oracle, under the identical
small-tank omission policy. Raw parameters and physical departure controls must
match. A changed instance hash additionally requires a fresh method2 solution
and both represented GZ curves to match the archive within1e-7. Old checker
source hashes remain recorded as provenance rather than being asserted equal
to the intentionally changed interpolation code.

Curve comparisons use the union of the represented angular nodes on their common
domain. The previous reference already uses qualified KN interpolation; this
therefore isolates liquid-table changes, not total KN-plus-liquid error. The
independent KN qualification and random tank qualification are reported alongside
this loaded-condition regression. This diagnostic is not voyage approval.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np

from stowage.checker import stability, stability_sounding
from stowage.versions import component_versions
from stowage.vessel.fleet import load_ship_params

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('method1_benchmark_previous', ROOT.parent/'method2'/'benchmark.py')
_previous = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_previous)


def compare(reference, fast, *, scope='liquid tables; reference and interpolated check both use independently qualified KN tables'):
    a, b = reference['curve'], fast['curve']
    aa, bb = np.asarray(a['angles_rad']), np.asarray(b['angles_rad'])
    end = min(float(aa[-1]), float(bb[-1]))
    nodes = np.unique(np.r_[aa[aa <= end], bb[bb <= end], end])
    direct = np.interp(nodes, aa, a['gz_m'])
    interpolated = np.interp(nodes, bb, b['gz_m'])
    difference = interpolated-direct
    negative_direct = np.interp(nodes, aa, a['negative_gz_m'])
    negative_fast = np.interp(nodes, bb, b['negative_gz_m'])
    negative_difference = negative_fast-negative_direct
    areas = {key: fast['metrics'][key]-value for key, value in reference['metrics'].items()
             if 'area_' in key and key.endswith('mrad') and key in fast['metrics']}
    fractions = [abs(delta)/max(.0012, .05*abs(reference['metrics'][key]))
                 for key, delta in areas.items()]
    gm_delta = fast['metrics']['gm_m']-reference['metrics']['gm_m']
    return dict(sign='interpolated method1 minus reference method1',
        scope=scope,
        common_end_rad=end, angles_rad=nodes.tolist(), reference_gz_m=direct.tolist(),
        interpolated_gz_m=interpolated.tolist(), gz_difference_m=difference.tolist(),
        max_absolute_gz_difference_m=float(max(np.max(abs(difference)),np.max(abs(negative_difference)))),
        max_gz_l5_fraction=float(max(np.max(abs(difference)/np.maximum(.05, .05*abs(direct))),
            np.max(abs(negative_difference)/np.maximum(.05,.05*abs(negative_direct))))),
        reference_negative_gz_m=negative_direct.tolist(),
        interpolated_negative_gz_m=negative_fast.tolist(),
        negative_gz_difference_m=negative_difference.tolist(),
        max_absolute_positive_gz_difference_m=float(np.max(abs(difference))),
        max_absolute_negative_gz_difference_m=float(np.max(abs(negative_difference))),
        criterion_area_metric_differences=areas,
        max_area_l5_fraction=float(max(fractions, default=0)),
        gm_difference_m=gm_delta,
        gm_l5_fraction=abs(gm_delta)/max(.05, .01*abs(reference['metrics']['gm_m'])),
        common_domain_signed_area_difference_mrad=float(np.trapezoid(difference, nodes)),
        common_domain_signed_negative_area_difference_mrad=float(np.trapezoid(negative_difference,nodes)))


def physical_method2_guard(reference, current, *, tolerance=1e-7):
    """Verify a changed serialization still describes the same physical state.

    This remeasures .2 only; it never presents a retained direct .1 curve as a
    new measurement. Parameters and explicit departure controls are checked by
    the caller before this independent solution/curve comparison.
    """
    if not current.get('ran') or current.get('error') or 'curve' not in current:
        raise ValueError('current method2 migration sanity check did not complete')
    keys = ('displacement_kg', 'kg_m', 'draught_mean_m', 'trim_m', 'gm_m',
            'list_rad', 'windage_area_m2', 'windage_lever_m')
    deltas = {}
    for key in keys:
        a, b = reference['metrics'][key], current['metrics'][key]
        delta = abs(b-a)
        deltas[key] = delta
        if not np.isfinite(delta) or delta > tolerance:
            raise ValueError(f'physical method2 migration sanity check differs: {key} delta={delta}; rerun direct method1 reference')
    a, b = reference['curve'], current['curve']
    aa, bb = np.asarray(a['angles_rad']), np.asarray(b['angles_rad'])
    if not np.isfinite(aa).all() or not np.isfinite(bb).all() or abs(aa[-1]-bb[-1]) > tolerance:
        raise ValueError('physical method2 migration curve domain differs; rerun direct method1 reference')
    nodes = np.unique(np.r_[aa, bb])
    for key in ('gz_m', 'negative_gz_m'):
        difference = np.interp(nodes, bb, b[key])-np.interp(nodes, aa, a[key])
        delta = float(np.max(abs(difference)))
        deltas[key] = delta
        if not np.isfinite(delta) or delta > tolerance:
            raise ValueError(f'physical method2 migration curve differs: {key} delta={delta}; rerun direct method1 reference')
    flood_delta = abs(b['flooding_angle_rad']-a['flooding_angle_rad'])
    deltas['flooding_angle_rad'] = flood_delta
    if not np.isfinite(flood_delta) or flood_delta > tolerance:
        raise ValueError('physical method2 migration flooding angle differs; rerun direct method1 reference')
    return dict(absolute_tolerance=tolerance, maximum_absolute_differences=deltas,
                passed=True, scope='fresh method2 only; retained direct method1 is not remeasured')


def clear_checker_caches():
    stability._geometry.cache_clear()
    stability._parsed_parameter_payload.cache_clear()
    stability_sounding.lookup.cache_clear()
    from stowage.checker import stability_liquid
    stability_liquid._read_manifest.cache_clear()
    try:
        from stowage.checker import stability_liquid_tables as stability_transfer_tables
    except ImportError:
        return
    # Transfer-table provider owns its cache layout. Clear public/cache-decorated
    # functions if available; geometry instances have already been discarded.
    for name in ('_lookup', 'lookup', 'load', 'table', 'cached_table'):
        function = getattr(stability_transfer_tables, name, None)
        clear = getattr(function, 'cache_clear', None)
        if clear is not None:
            clear()


def run(name, output, repeats=5):
    if os.environ.get('STOWAGE_STABILITY_DIRECT') == '1':
        raise ValueError('unset STOWAGE_STABILITY_DIRECT for production cached timing')
    reference_path = ROOT.parent/'method2'/f'departure_{name}.json'
    retained = json.loads(reference_path.read_text())
    params = load_ship_params(name)
    digest = hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()
    if digest != retained['parameters_sha256']:
        raise ValueError('retained direct-method1 parameter hash differs; rerun direct reference')
    started = perf_counter()
    instance, summary = _previous._departure.build_departure(name)
    build_seconds = perf_counter()-started
    condition_digest = hashlib.sha256(instance.model_dump_json().encode()).hexdigest()
    # Shared component versions/metadata may change serialization independently
    # of the physical condition. Retained .1 can only be reused after matching
    # all raw parameters, explicit departure controls, and a fresh .2 solution.
    migration = None
    if condition_digest != retained['instance_sha256']:
        for key in ('container_count', 'gross_mass_per_box_kg', 'target_displacement_kg',
                    'tank_masses_kg', 'production_fixed_loads'):
            if retained[key] != summary[key]:
                raise ValueError(f'physical departure control changed: {key}')
        migration = dict(reason='serialization metadata changed; same raw parameters and physical departure controls, fresh method2 physical sanity check required',
                         retained_instance_sha256=retained['instance_sha256'],
                         current_instance_sha256=condition_digest)
    sources = sorted(Path(stability.__file__).parent.glob('stability*.py'))
    result = dict(summary, parameters_sha256=digest, instance_sha256=condition_digest,
        component_versions=component_versions(), source_sha256={
            str(path.relative_to(ROOT.parents[2])): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sources}, instance_build_seconds=build_seconds,
        reference_path=str(reference_path.relative_to(ROOT.parents[2])),
        reference_file_sha256=hashlib.sha256(reference_path.read_bytes()).hexdigest(),
        reference_source_sha256=retained['source_sha256'], instance_migration=migration,
        timing_scope='check_stability only, disk caches retained, fresh checker LRUs for cold',
        complete=False, status='instance_built')
    _previous.save(output, result)
    if migration is not None:
        # Deliberately outside measured cold scope. Explicitly clear the warmed
        # checker LRUs again immediately afterwards, retaining only disk caches.
        result['method2_migration_sanity_check'] = _previous.run_check(instance, 2, same_exemptions=False)
        _previous.save(output, result)
        result['instance_migration']['physical_guard'] = physical_method2_guard(
            retained['method2_cold'], result['method2_migration_sanity_check'])
        _previous.save(output, result)
    clear_checker_caches()
    result['method1_cold'] = _previous.run_check(instance, 1, same_exemptions=False)
    result['status'] = 'cold_complete'
    _previous.save(output, result)
    cold = result['method1_cold']
    if not cold['ran'] or cold['error'] or 'curve' not in cold:
        raise RuntimeError(f'production check failed: {cold.get("error")}')
    # The actual correction tank IDs must match, not just their counts.
    expected_omissions = retained['method1']['loading_details']['omitted_tank_ids']
    actual_omissions = cold['loading_details']['omitted_tank_ids']
    if sorted(expected_omissions) != sorted(actual_omissions):
        raise ValueError('retained reference omission IDs differ; rerun direct reference')
    result['method1_warm'] = []
    for _ in range(repeats):
        result['method1_warm'].append(_previous.run_check(instance, 1, same_exemptions=False))
        _previous.save(output, result)
    times = [row['seconds'] for row in result['method1_warm']]
    result['method1_warm_statistics_seconds'] = dict(min=min(times), median=median(times), max=max(times))
    result['loaded_interpolation_error'] = compare(retained['method1'], cold)
    result.update(complete=True, status='complete')
    _previous.save(output, result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vessel', choices=_previous.FLEET, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    result = run(args.vessel, args.output, args.repeats)
    print(json.dumps(dict(vessel=args.vessel,
        cold_seconds=result['method1_cold']['seconds'],
        warm_seconds=result['method1_warm_statistics_seconds'],
        errors={key: value for key, value in result['loaded_interpolation_error'].items()
                if key.endswith('l5_fraction')}), indent=2))
