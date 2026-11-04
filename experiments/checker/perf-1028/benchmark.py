"""Single-thread fresh-process A/B timings of the identical wet D57 condition.

Cold excludes vessel/fixture setup and clears hydrostatic result caches. Each
cold sample starts a new interpreter; persistent qualified geometry files remain
available, as in d57_benchmark.py. The original reference bypasses all 1028 reuse.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import os
from pathlib import Path
import pstats
import statistics
import subprocess
import sys
from time import perf_counter

for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'experiments/checker'))


def source_hashes():
    source_root = Path(os.environ.get('STOWAGE_PERF_SOURCE_ROOT', ROOT)).resolve()
    paths = sorted({*source_root.joinpath('src/stowage').rglob('*.py'),
                    *source_root.joinpath('src/stowage').rglob('*.toml'),
                    *source_root.joinpath('src/stowage').rglob('*.json'),
                    *source_root.joinpath('params').rglob('*.toml')})
    hashes = {str(path.relative_to(source_root)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in paths}
    hashes['experiment_harness:benchmark.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for name in ('benchmark_full.py', 'd57_benchmark.py'):
        path = ROOT/'experiments/checker'/name
        hashes['experiment_harness:'+name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def worker(args):
    import benchmark_full as fixture
    from d57_benchmark import declare_voyage
    from stowage.checker.full import check, check_reference
    from stowage.vessel.hydrostatics import clear_hydrostatic_caches
    from stowage.checker.strength_assessment import clear_assessment_cache
    from stowage.checker.stability_reuse import _axis_query
    from stowage.checker.visibility_range import _range_intersections

    # The fleet spelling may change concurrently; the same physical ship and
    # homogeneous loading recipe are used for both reference and optimized.
    fixture.FLEET = (*fixture.FLEET, args.worker)
    os.environ['STOWAGE_BENCH_LOADING'] = 'homogeneous'
    setup_start = perf_counter()
    instance, plan = fixture.condition(args.worker)
    grouped = {tid for group in instance.vessel.voyage_policy.groups for tid in group.tank_ids}
    masses = dict(instance.onboard.tank_masses_kg)
    masses.update({tank.id: .5*tank.capacity_m3*tank.content_density_kg_m3
                   for tank in instance.vessel.tanks if tank.id in grouped})
    instance.onboard.tank_masses_kg = masses
    instance = declare_voyage(instance)
    setup_seconds = perf_counter()-setup_start
    clear_hydrostatic_caches()
    clear_assessment_cache()
    _axis_query.cache_clear()
    _range_intersections.cache_clear()
    function = check_reference if args.mode == 'reference' else check
    before = source_hashes()
    samples, reports = [], []
    for _ in range(args.repeats+1):
        start = perf_counter()
        report = function(instance, plan)
        samples.append(perf_counter()-start)
        payload = report.model_dump(mode='json')
        reports.append(hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
    if len(set(reports)) != 1:
        raise AssertionError('cold/warm reports differ')
    if args.profile:
        prefix = Path(args.output).parent / f'{Path(args.output).stem}-profile-{args.worker}'
        profile = cProfile.Profile()
        profiled = profile.runcall(function, instance, plan)
        if profiled.model_dump(mode='json') != payload:
            raise AssertionError('profile report differs')
        profile.dump_stats(str(prefix.with_suffix('.prof')))
        with prefix.with_suffix('.txt').open('w') as stream:
            pstats.Stats(profile, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(65)
    after = source_hashes()
    print(json.dumps(dict(ship=instance.vessel.id, mode=args.mode,
        setup_seconds=setup_seconds, cold_seconds=samples[0], warm_seconds=samples[1:],
        report_sha256=reports[0], passed=report.passed,
        part_errors={key: part.error for key, part in report.parts.items() if part.error},
        source_sha256=before, sources_stable=before == after,
        source_sha256_after=after if before != after else None)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker')
    parser.add_argument('--ships', nargs='+')
    parser.add_argument('--mode', choices=('reference', 'optimized'), default='optimized')
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    from stowage.vessel.fleet import list_fleet
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    start = perf_counter()
    for ship in args.ships or list_fleet():
        samples = []
        for run in range(args.runs):
            command = [sys.executable, __file__, '--worker', ship, '--mode', args.mode,
                       '--repeats', str(args.repeats if run == 0 else 0), '--output', str(output)]
            if args.profile and run == 0:
                command.append('--profile')
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            if result.returncode:
                sys.stderr.write(result.stderr)
                raise SystemExit(result.returncode)
            samples.append(json.loads(result.stdout))
            checkpoint = dict(mode=args.mode, completed=rows, active_ship=ship,
                              active_samples=samples, elapsed_seconds=perf_counter()-start)
            output.with_suffix('.checkpoint.json').write_text(json.dumps(checkpoint, indent=2)+'\n')
        warm = [value for sample in samples for value in sample['warm_seconds']]
        row = dict(ship=ship, mode=args.mode, samples=samples,
                   cold_median_seconds=statistics.median(s['cold_seconds'] for s in samples),
                   warm_median_seconds=statistics.median(warm), warm_samples=len(warm),
                   equal_reports=len({s['report_sha256'] for s in samples}) == 1,
                   sources_stable=all(s['sources_stable'] for s in samples))
        rows.append(row)
        output.write_text(json.dumps(dict(rows=rows, elapsed_seconds=perf_counter()-start), indent=2)+'\n')
        print(json.dumps({key: value for key,value in row.items() if key != 'samples'}), flush=True)


if __name__ == '__main__':
    main()
