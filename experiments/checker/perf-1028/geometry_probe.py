"""Single-core focused report-difference and numerical-reuse A/B probe."""
from __future__ import annotations

import os
for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'
os.environ['STOWAGE_BENCH_LOADING'] = 'homogeneous'
os.environ['STOWAGE_BENCH_PRODUCTION_MODEL'] = '1'

from pathlib import Path
import json
import statistics
import sys
from time import perf_counter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import benchmark_full
from d57_benchmark import declare_voyage
from stowage.checker import full, strength_assessment
from stowage.checker.perf import without_reuse
from stowage.vessel.hydrostatics import clear_hydrostatic_caches


def differences(a, b, path='report'):
    result = []
    if isinstance(a, dict) and isinstance(b, dict):
        for key in a.keys() | b.keys():
            if key not in a or key not in b:
                result.append((path+'.'+key, a.get(key), b.get(key)))
            else:
                result.extend(differences(a[key], b[key], path+'.'+key))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            result.append((path+'.length', len(a), len(b)))
        for index, (left, right) in enumerate(zip(a, b)):
            result.extend(differences(left, right, f'{path}[{index}]'))
    elif a != b:
        result.append((path, a, b))
    return result


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else 'feeder_500'
    instance, plan = benchmark_full.condition(name)
    grouped = {tid for group in instance.vessel.voyage_policy.groups for tid in group.tank_ids}
    instance.onboard.tank_masses_kg.update({tank.id: .5*tank.capacity_m3*tank.content_density_kg_m3
        for tank in instance.vessel.tanks if tank.id in grouped})
    instance = declare_voyage(instance)
    clear_hydrostatic_caches()
    reports = []
    for label in ('cold', 'warm', 'warm2'):
        started = perf_counter()
        report = full.check(instance, plan).model_dump(mode='json')
        print(json.dumps({'label': label, 'seconds': perf_counter()-started}), flush=True)
        reports.append(report)
    for index in (1, 2):
        diff = differences(reports[0], reports[index])
        print(json.dumps({'cold_vs': index, 'differences': diff}), flush=True)
    with without_reuse():
        report = full.check(instance, plan).model_dump(mode='json')
    print(json.dumps({'reference_vs_warm': differences(report, reports[-1])}), flush=True)
    original_roots = strength_assessment._interior_roots

    def reference_roots(coefficients):
        with without_reuse():
            return original_roots(coefficients)

    labels = ('current', 'no_strength_reuse')
    samples = {label: [] for label in labels}
    for iteration in range(5):
        for label in (labels if iteration % 2 == 0 else labels[::-1]):
            strength_assessment._interior_roots = (original_roots if label == 'current' else reference_roots)
            started = perf_counter()
            full.check(instance, plan)
            samples[label].append(perf_counter()-started)
    strength_assessment._interior_roots = original_roots
    timings = {label: {'samples': values, 'median': statistics.median(values)}
               for label, values in samples.items()}
    print(json.dumps({'timings': timings}), flush=True)


if __name__ == '__main__':
    main()
