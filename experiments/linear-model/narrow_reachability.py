"""Small exact ballast search at full cargo: observed reachability, never a proof.

Symmetric port/starboard tank-pair fills leave three bounded variables. Evaluate
all pair-fill corners and refine the best two with SLSQP. A local optimizer and
finite probes cannot certify that an operating condition is impossible.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import itertools
import json
from pathlib import Path
import runpy
import time

import numpy as np
from scipy.optimize import minimize

import stowage.linear.bands as bands
import stowage.linear.physics as physics

_HERE = Path(__file__).resolve().parent
_HELPERS = runpy.run_path(str(_HERE/'narrow.py'))


class Captured(Exception):
    pass


def full_model_and_loading(instance):
    """Stop before calibration, retaining one physical packing for exact probes.

    This experiment never returns/stores a falsely calibrated model: the builder
    raises before either its memory cache or disk artifact is populated.
    """
    original = bands.sample_manifest_loading
    captured = {}
    def capture(model, instance, tank, rng):
        start = time.perf_counter()
        loading = original(model, instance, tank, rng)
        if loading is None:
            return None
        captured.update(model=model, loading=loading, packing_seconds=time.perf_counter()-start)
        raise Captured
    bands.sample_manifest_loading = capture
    try:
        physics.build_model(instance, bands.band_for_instance(instance), _samples=1)
    except Captured:
        pass
    finally:
        bands.sample_manifest_loading = original
    if not captured:
        raise RuntimeError('Could not capture a physical full-cargo placement')
    return captured['model'], captured['loading'], captured['packing_seconds']


def search(name):
    started = time.perf_counter()
    instance, manifest = _HELPERS['cargo_instance'](name, 'full')
    model, (cargo, heights), packing_seconds = full_model_and_loading(instance)
    pairs = {}
    for i, tid in enumerate(model.tank_ids):
        if model.tank_adjustable[i]:
            pairs.setdefault(tid.rsplit('-', 1)[0], []).append(i)
    groups = sorted(pairs.values(), key=lambda group: float(np.mean(model.tank_lcg_m[group])))
    assert len(groups) == 3 and all(len(group) == 2 for group in groups)
    evaluations = []
    memo = {}
    def evaluate(fills):
        key = tuple(float(x) for x in fills)
        if key in memo:
            return memo[key]
        tank = np.zeros(len(model.tank_ids))
        for group, fill in zip(groups, fills):
            tank[group] = model.tank_capacity_kg[group]*fill
        exact = physics.exact_condition(model, instance, cargo, tank, cell_vcg_m=heights)
        row = {'pair_fills_aft_to_fore': list(key), 'ballast_mass_kg': float(tank.sum()),
               **{k: float(exact[k]) for k in ('gm_m', 'trim_m', 'list_rad', 'draught_m')}}
        evaluations.append(row); memo[key] = row
        return row
    for corner in itertools.product((0., 1.), repeat=3):
        evaluate(corner)
    evaluate((.5, .5, .5))
    def objective(fills):
        row = evaluate(fills)
        return abs(row['trim_m']) + 1000.*max(0., model.gm_min_m-row['gm_m'])
    starts = sorted(evaluations, key=lambda row: abs(row['trim_m']))[:2]
    optimizers = []
    for row in starts:
        result = minimize(objective, row['pair_fills_aft_to_fore'], method='SLSQP',
                          bounds=[(0.,1.)]*3, options={'maxiter': 12, 'ftol': 1e-8, 'eps': 1e-3})
        optimizers.append({'success': bool(result.success), 'message': str(result.message),
                           'iterations': int(result.nit)})
    admissible = [row for row in evaluations if row['gm_m'] >= model.gm_min_m
                  and abs(row['list_rad']) <= model.max_list_rad]
    best = min(admissible or evaluations, key=lambda row: abs(row['trim_m']))
    best_gm = max(evaluations, key=lambda row: row['gm_m'])
    result = {'ship': name, 'level': 'full', 'manifest': manifest, 'band': asdict(model.band),
              'station_count': len(model.station_x_m), 'best_observed': best,
              'best_observed_in_trim_band': abs(best['trim_m']) <= model.band.max_abs_trim_m,
              'found_gm_and_list_admissible': bool(admissible), 'best_gm_observed': best_gm,
              'packing_seconds': packing_seconds, 'evaluation_count': len(evaluations),
              'evaluations': evaluations, 'optimizers': optimizers,
              'elapsed_seconds': time.perf_counter()-started,
              'qualification': 'Exact sampled/local optimization evidence for one physical packing; not an infeasibility proof.'}
    (_HERE/f'narrow-reachability-{name}.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k: result[k] for k in ('ship', 'best_observed', 'best_observed_in_trim_band',
                                              'packing_seconds', 'evaluation_count', 'elapsed_seconds')}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ship', action='append', required=True)
    args = parser.parse_args()
    for name in args.ship:
        search(name)


if __name__ == '__main__':
    main()
