"""Full-check timing with actual method .1 liquids on production ships.

ASSUMPTION: all tanks are half full and identical dry cargo fills every hold
and the lowest two deck tiers. Cargo mass is reduced uniformly to retain the
dry benchmark's 90%-design displacement. No moments or limits are adjusted.
This diagnostic is not an operational voyage prescription or approval.
Sources: production fleet parameters and IS Code B3.1.9.1; see benchmark_full.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
from time import perf_counter

for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'

from benchmark_full import ROOT, FLEET, homogeneous_departure
from stowage.checker.full import check
from stowage.model import BallastCondition, Instance, MovePlan
from stowage.versions import component_versions
from stowage.vessel.hydrostatics import clear_hydrostatic_caches


def measure(name, repeats):
    setup = perf_counter()
    instance = homogeneous_departure(name)
    tanks = {tank.id: .5 * tank.capacity_m3 * tank.content_density_kg_m3
             for tank in instance.vessel.tanks}
    reduction = sum(tanks.values()) / len(instance.containers)
    for box in instance.containers:
        box.gross_mass_kg -= reduction
        if box.gross_mass_kg < box.tare_kg:
            raise ValueError('half-tank benchmark leaves insufficient cargo mass')
    instance.onboard.tank_masses_kg = tanks
    instance = Instance.model_validate(instance.model_dump())
    plan = MovePlan(id=f'half-tank-{name}', instance_id=instance.id, call_index=0,
                    ballast=BallastCondition(source='planner', tank_masses_kg={
                        tank.id: tanks[tank.id] for tank in instance.vessel.tanks if tank.adjustable}))
    setup_seconds = perf_counter() - setup
    paths = [*ROOT.joinpath('src/stowage/checker').glob('*.py'),
             *ROOT.joinpath('src/stowage/vessel').glob('*.py'),
             *ROOT.joinpath('src/stowage/model').glob('*.py'),
             ROOT / 'src/stowage/versions.py', ROOT / f'params/fleet/{name}.toml',
             Path(__file__), Path(__file__).with_name('benchmark_full.py')]
    def hashes():
        return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in paths}
    before = hashes()
    clear_hydrostatic_caches()
    times, reference = [], None
    for _ in range(repeats + 1):
        start = perf_counter()
        report = check(instance, plan)
        times.append(perf_counter() - start)
        assert all(part.ran for part in report.parts.values()), report.parts
        dumped = report.model_dump(mode='json')
        if reference is None:
            reference = dumped
        else:
            assert reference == dumped, 'cold/warm full reports differ'
    return dict(ship=name, cells=len(instance.vessel.cells), boxes=len(instance.containers),
                tank_count=len(tanks), tank_fill_fraction=.5, setup_seconds=setup_seconds,
                cold_seconds=times[0], warm_seconds=times[1:],
                warm_median_seconds=statistics.median(times[1:]),
                passed=report.passed, all_parts_ran=True, equal_reports=True,
                rule_ids=sorted({v.rule_id for v in report.violations}),
                table_metrics={key: value for key, value in report.parts['stability'].metrics.items()
                               if 'table' in key or 'direct' in key or 'method' in key},
                peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss /
                            (1024**3 if sys.platform == 'darwin' else 1024**2),
                versions=component_versions(), source_sha256=before,
                sources_stable_during_calls=before == hashes())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', choices=FLEET)
    parser.add_argument('--sizes', nargs='+', choices=FLEET, default=list(FLEET))
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--output', default='experiments/checker/v13-timings-partial-tanks.json')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('repeats must be positive')
    if args.worker:
        print(json.dumps(measure(args.worker, args.repeats)), flush=True)
    else:
        rows = []
        for name in args.sizes:
            result = subprocess.run([sys.executable, __file__, '--worker', name,
                                     '--repeats', str(args.repeats)],
                                    capture_output=True, text=True, check=True)
            rows.append(json.loads(result.stdout))
            Path(args.output).write_text(json.dumps(rows, indent=2) + '\n')
            print(json.dumps(rows[-1]), flush=True)
