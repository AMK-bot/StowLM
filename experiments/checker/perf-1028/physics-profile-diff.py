"""Debug one exact benchmark worker; preserve unexplained profiled differences."""
import os
for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'

import cProfile
import json
from pathlib import Path
import pstats
import sys
from time import perf_counter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import benchmark as benchmark
import benchmark_full as fixture
from d57_benchmark import declare_voyage
from stowage.checker.full import check
from stowage.vessel.hydrostatics import clear_hydrostatic_caches
from stowage.checker.strength_assessment import clear_assessment_cache
from stowage.checker.stability_reuse import _axis_query
from stowage.checker.visibility_range import _range_intersections


def diff(expected, actual, path='$'):
    if type(expected) is not type(actual):
        return [dict(path=path, expected=expected, actual=actual, reason='type')]
    if isinstance(expected, dict):
        result = []
        for key in sorted(expected.keys() | actual.keys()):
            if key not in expected or key not in actual:
                result.append(dict(path=path+'.'+key, expected=expected.get(key), actual=actual.get(key), reason='missing'))
            else:
                result.extend(diff(expected[key], actual[key], path+'.'+key))
        return result
    if isinstance(expected, list):
        result = []
        if len(expected) != len(actual):
            result.append(dict(path=path, expected_length=len(expected), actual_length=len(actual), reason='length'))
        for index, (a, b) in enumerate(zip(expected, actual)):
            result.extend(diff(a, b, f'{path}[{index}]'))
        return result
    if expected != actual:
        result = dict(path=path, expected=expected, actual=actual)
        if type(expected) in (float, int) and type(actual) in (float, int):
            result.update(absolute_difference=abs(actual-expected),
                relative_difference=abs(actual-expected)/max(abs(actual), abs(expected), 1e-300))
        return [result]
    return []


def save(name, payload):
    (HERE/('physics-profile-diff-'+name+'.json')).write_text(json.dumps(payload, indent=2, sort_keys=True)+'\n')


def main():
    started = perf_counter()
    fixture.FLEET = (*fixture.FLEET, 'feeder_800')
    os.environ['STOWAGE_BENCH_LOADING'] = 'homogeneous'
    instance, plan = fixture.condition('feeder_800')
    grouped = {tid for group in instance.vessel.voyage_policy.groups for tid in group.tank_ids}
    masses = dict(instance.onboard.tank_masses_kg)
    masses.update({tank.id: .5*tank.capacity_m3*tank.content_density_kg_m3
                   for tank in instance.vessel.tanks if tank.id in grouped})
    instance.onboard.tank_masses_kg = masses
    instance = declare_voyage(instance)
    setup = perf_counter()-started
    clear_hydrostatic_caches()
    clear_assessment_cache()
    _axis_query.cache_clear()
    _range_intersections.cache_clear()
    before = benchmark.source_hashes()
    save('sources-before', before)
    timings, reports = [], []
    for iteration in range(6):
        mark = perf_counter()
        payload = check(instance, plan).model_dump(mode='json')
        timings.append(perf_counter()-mark)
        reports.append(payload)
        save(f'unprofiled-{iteration}', payload)
        save('progress', dict(setup_seconds=setup, unprofiled_seconds=timings,
                             equal_reports=all(report == reports[0] for report in reports)))
        print(json.dumps(dict(iteration=iteration, seconds=timings[-1], equal=payload==reports[0])), flush=True)
    save('expected', payload)
    profile = cProfile.Profile()
    mark = perf_counter()
    actual = profile.runcall(check, instance, plan).model_dump(mode='json')
    profile_seconds = perf_counter()-mark
    save('actual', actual)
    differences = diff(payload, actual)
    save('differences', differences)
    after = benchmark.source_hashes()
    save('sources-after', after)
    profile.dump_stats(str(HERE/'physics-profile-diff.prof'))
    with (HERE/'physics-profile-diff.txt').open('w') as stream:
        pstats.Stats(profile, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(80)
    summary = dict(setup_seconds=setup, unprofiled_seconds=timings, profile_seconds=profile_seconds,
        unprofiled_reports_equal=all(report == reports[0] for report in reports),
        profile_report_equal=not differences, differences=differences,
        sources_stable=before==after, source_differences=diff(before,after),
        total_seconds=perf_counter()-started)
    save('summary', summary)
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
