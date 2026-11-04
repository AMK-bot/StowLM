"""Independent random loaded-condition validation of actual-transfer tables.

By default the complete production curve is compared with a direct hull AND
direct tank reference. --no-full-direct-reference retains the same independently
qualified KN geometry on both sides to isolate liquid-table error instead.
The direct tank mode retains exact tank integrations and the bounded
full-filling-range consumable envelope. Every liquid kind is varied, including
empty/full endpoints and partial peaks; direct mode therefore exercises the new
bounded transfer implementation. Separate full-filling-range envelope holdouts
and former slow-fallback regressions supplement the free-trim curve evidence.

Random numerical point-load holdouts span the feasible light-to-scantling mass
range, every tank fill0–1, explicit±10° list and operating trim limits. This varies loading KG, LCG, TCG, equilibrium list/trim,
downflooding angle and all criterion areas. These are numerical holdouts, not
approved loading conditions. Full reports and curves are retained to expose
uncovered-grid fallbacks and near-boundary direct rechecks.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.util
import json
import math
import os
from pathlib import Path
from time import perf_counter

import numpy as np

from stowage.model import Instance
from stowage.checker import stability_liquid
from stowage.checker.stability_loading import loading_condition
from stowage.vessel.fleet import load_ship_params

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('method1_qualification_benchmark', ROOT/'benchmark.py')
_benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_benchmark)
_domain_spec = importlib.util.spec_from_file_location('liquid_qualification_cases', ROOT.parent/'fallback'/'qualification_cases.py')
_domain = importlib.util.module_from_spec(_domain_spec)
_domain_spec.loader.exec_module(_domain)


@contextmanager
def direct_liquids():
    key = 'STOWAGE_STABILITY_LIQUID_DIRECT'
    old = os.environ.get(key)
    os.environ[key] = '1'
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


@contextmanager
def reference_budget(seconds=120.):
    """Explicit bounded offline allowance; restore the production environment.

    Only a direct-reference call uses this longer deadline. Fast-path timings
    keep their normal production budget (30seconds unless the caller set it).
    An expired reference is incomplete evidence, never an interpolation error.
    """
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('finite positive direct-reference budget required')
    key='STOWAGE_STABILITY_TIME_BUDGET_S'
    previous=os.environ.get(key)
    os.environ[key]=str(float(seconds))
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key,None)
        else:
            os.environ[key]=previous


@contextmanager
def offline_per_tank_qualification():
    """Measure interpolation without the production near-limit direct recheck.

    Tank certificates remain mandatory; only the whole-vessel gate is deferred.
    The guard normally restarts a near-limit curve with direct physics. That is
    proper for an operational verdict but would hide the very interpolation
    error this experiment measures. Observe its original decision, then return
    False in this context only. Actual OutsideTable/numerical failures retain
    their ordinary direct fallback and fail this experiment's coverage checks.
    This measurement mode cannot approve a voyage; production is unchanged.
    """
    from stowage.checker import stability
    original_guard=stability._near_table_limit
    guard_evaluations=[]
    def measurement_guard(metrics,margins):
        would_trigger=bool(original_guard(metrics,margins))
        guard_evaluations.append(would_trigger)
        return False
    original = stability_liquid.attach_tables
    def attach(condition, params, **kwargs):
        kwargs['require_manifest'] = False
        return original(condition, params, **kwargs)
    stability_liquid.attach_tables = attach
    stability._near_table_limit=measurement_guard
    try:
        yield guard_evaluations
    finally:
        stability_liquid.attach_tables = original
        stability._near_table_limit=original_guard


def run(name, output, samples=8, seed=3911, tank_evidence=None, certify=False,
        full_direct_reference=True, kn_evidence=None, direct_budget_s=120.):
    if not isinstance(samples,int) or samples < 1:
        raise ValueError('positive integer sample count required')
    if certify and samples < 8:
        raise ValueError('expanded-domain certification requires eight loaded conditions')
    if not math.isfinite(direct_budget_s) or direct_budget_s <= 0:
        raise ValueError('finite positive direct-reference budget required')
    started = perf_counter()
    base, summary = _benchmark._previous._departure.build_departure(name)
    result = dict(vessel=name, seed=seed, random_conditions=samples,
        condition_policy='all tank kinds fills0–1 incl explicitempty/full/asymmetric; feasiblelight→scantlingmass; synthetic nonnegative point loads set±10deglist and trimlimits; ordinaryregulatoryviolations retained',
        comparison_scope=('full production curves against direct hull AND tank geometry'
            if full_direct_reference else 'full production curves with same qualified KN; direct tank geometry versus independently qualified liquid tables'),
        source='actual provider solid and bounded reference transfer, no box replacement',
        measurement_mode='near-limit direct-recheck guard observed but disabled for fast interpolation measurement only; no operational/voyage approval',
        complete=False, status='base_instance_built', cases=[],
        direct_reference_budget_s=float(direct_budget_s),production_default_budget_s=30.,
        fast_budget_s=os.environ.get('STOWAGE_STABILITY_TIME_BUDGET_S','30'))
    rng = np.random.default_rng(seed)
    _benchmark._previous.save(output, result)
    for index in range(samples):
        instance,domain_point = _domain.domain_condition(name,rng,index)
        # Record fast first: direct-oracle integrations must not warm the timed
        # production path or manufacture a hit for an uncovered table query.
        with offline_per_tank_qualification() as guard_evaluations:
            fast = _benchmark._previous.run_check(instance, 1, same_exemptions=False)
        with reference_budget(direct_budget_s),direct_liquids():
            if full_direct_reference:
                with _benchmark._previous.force_direct():
                    direct = _benchmark._previous.run_check(instance, 1, same_exemptions=False)
            else:
                direct = _benchmark._previous.run_check(instance, 1, same_exemptions=False)
        row = dict(index=index, domain_point=domain_point, tank_masses_kg=instance.onboard.tank_masses_kg,
                   fast=fast, direct=direct,
                   production_guard_would_trigger=any(guard_evaluations) if guard_evaluations else None,
                   production_guard_evaluations=list(guard_evaluations))
        row['reference_timed_out']=bool(direct.get('metrics',{}).get('time_budget_exceeded',0))
        if row['reference_timed_out']:
            row['passed']=False
            row['failure']=f'direct reference exhausted its {direct_budget_s:g}s offline budget; qualification incomplete, no interpolation verdict'
        elif not fast['ran'] or fast['error'] or not direct['ran'] or direct['error']:
            row['passed'] = False
            row['failure'] = 'one numerical check failed'
        elif 'curve' not in fast or 'curve' not in direct:
            row['passed'] = False
            row['failure'] = 'one check did not produce a GZ curve'
        else:
            row['errors'] = _benchmark.compare(direct, fast, scope=result['comparison_scope'])
            row['passed'] = all(row['errors'][key] <= .2 for key in (
                'max_gz_l5_fraction', 'max_area_l5_fraction', 'gm_l5_fraction'))
            row['table_queries'] = fast['metrics'].get('liquid_table_queries', 0)
            row['direct_queries'] = fast['metrics'].get('liquid_direct_queries', 0)
            if not row['table_queries'] and any(instance.onboard.tank_masses_kg.values()):
                row['passed'] = False
                row['failure'] = 'no qualified liquid-table query on a wet condition: reference fallback is not interpolation evidence'
            if row['direct_queries']:
                row['passed'] = False
                row['failure'] = 'fast measurement used direct liquid queries after a domain or numerical fallback; cannot qualify full table coverage'
        result['cases'].append(row)
        result['status'] = f'case_{index}_complete'
        _benchmark._previous.save(output, result)
    compared=[case['errors'] for case in result['cases'] if 'errors' in case]
    result['compared_conditions']=len(compared)
    result['reference_timeouts']=sum(case['reference_timed_out'] for case in result['cases'])
    # Missing measurements remain JSON null, never Infinity or an invented error.
    result['maxima']={key:max((row[key] for row in compared),default=None)
                      for key in ('max_gz_l5_fraction','max_area_l5_fraction','gm_l5_fraction')}
    result['all_passed'] = all(row['passed'] for row in result['cases'])
    result['complete']=len(compared)==samples
    result['status']='complete' if result['complete'] else 'incomplete_reference_or_numerical_failure'
    _benchmark._previous.save(output,result)
    if certify:
        if not result['all_passed'] or tank_evidence is None:
            raise ValueError('vessel certificate requires passing loaded cases and tank-component evidence')
        tanks = json.loads(tank_evidence.read_text())
        if not tanks['all_passed'] or not tanks.get('certified') or tanks['vessel'] != name:
            raise ValueError('invalid or unqualified tank-component evidence')
        # Add independently observed KN interpolation fractions conservatively.
        # This is an empirical error budget over both qualification sets, not
        # a uniform proof for every possible operating condition.
        kn_source = Path(kn_evidence) if kn_evidence is not None else ROOT/'kn_validation_current.json'
        kn_rows = [row for row in json.loads(kn_source.read_text())['validation'] if row['vessel'] == name]
        if not kn_rows:
            raise ValueError('current independently measured KN evidence required')
        kn = {key: max((row['validation']['max_l5_fraction'][key] if 'validation' in row
                       else row['errors'][field]['max_l5_fraction']) for row in kn_rows)
              for key, field in dict(gz='gz_m', area='area_mrad', gm='gm_m').items()}
        mapping = dict(gz='max_gz_l5_fraction', area='max_area_l5_fraction', gm='gm_l5_fraction')
        liquid = {key: max(tanks['maxima'][key], result['maxima'][metric])
                  for key, metric in mapping.items()}
        combined = {key: max(tanks['maxima'][key]+kn[key], result['maxima'][mapping[key]])
                    if full_direct_reference else liquid[key]+kn[key] for key in kn}
        result['empirical_error_budget'] = dict(liquid=liquid, kn=kn, combined=combined,
            kn_source=str(kn_source), full_direct_reference=full_direct_reference)
        if any(value > .2 for value in combined.values()):
            raise ValueError('combined independently observed KN+liquid budget exceeds0.2L5')
        params = load_ship_params(name)
        condition = loading_condition(base, base.onboard, ship_params=params,
            free_surface_method=1, precomputed_soundings=True, small_tank_exemption=True)
        evidence = dict(combined, samples=samples, source=str(output),
            tank_component_source=str(tank_evidence), empirical_error_budget=result['empirical_error_budget'],
            scope='independent tank component and full light-to-scantling loaded curves, endpoints/asymmetric tanks, ±10deglist and trimlimits; KN fractions added conservatively; formerfallbackregressions separate')
        result['manifest'] = str(stability_liquid.qualify_vessel(params, condition, evidence))
    result.update(complete=len(compared)==samples,
        status='complete' if len(compared)==samples else 'incomplete_reference_or_numerical_failure',
        elapsed_seconds=perf_counter()-started)
    _benchmark._previous.save(output, result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vessel', choices=_benchmark._previous.FLEET, required=True)
    parser.add_argument('--samples', type=int, default=8)
    parser.add_argument('--seed', type=int, default=3911)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tank-evidence', type=Path)
    parser.add_argument('--kn-evidence', type=Path,
        help='current KN validator evidence or SHA-bound cache certificate evidence')
    parser.add_argument('--certify', action='store_true')
    parser.add_argument('--direct-budget-s',type=float,default=120.,
        help='offline direct-reference deadline; production fast calls retain their normal budget')
    parser.add_argument('--full-direct-reference', action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    result = run(args.vessel, args.output, args.samples, args.seed, args.tank_evidence, args.certify,
                 args.full_direct_reference, args.kn_evidence,args.direct_budget_s)
    print(json.dumps({key: result[key] for key in ('vessel', 'elapsed_seconds', 'all_passed', 'maxima')}, indent=2))
    if not result['all_passed']:
        raise SystemExit(1)
