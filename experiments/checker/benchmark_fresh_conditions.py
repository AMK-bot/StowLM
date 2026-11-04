"""Measure one novel loading condition after warming each production vessel.

A rollout changes cargo mass and moments, so exact-input memo hits for repeated
identical plans overstate its throughput. Reuse task 3.1's documented dry,
homogeneous loading benchmark; add exactly 1 kg to one nonempty box's verified
gross mass, remaining below 30,480 kg. No caches are cleared between the warm-up
and this new condition, and no physical inputs or limits are relaxed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter

os.environ['STOWAGE_BENCH_LOADING'] = 'homogeneous'
os.environ['STOWAGE_BENCH_PRODUCTION_MODEL'] = '1'
from benchmark_full import FLEET, ROOT, condition
from stowage.checker.full import check
from stowage.model import Container, Instance
from stowage.versions import component_versions
from stowage.vessel.hydrostatics import hydrostatic_cache_info


def hashes(name):
    paths = [*sorted((ROOT/'src/stowage/checker').glob('*.py')),
             ROOT/'src/stowage/vessel/hydrostatics.py',
             ROOT/'src/stowage/vessel/hull_offsets.py',
             ROOT/f'params/fleet/{name}.toml', ROOT/'src/stowage/versions.py']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths}


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--sizes', nargs='+', choices=FLEET, default=list(FLEET))
parser.add_argument('--output', type=Path, default=Path(__file__).with_name('timings-kcs-fresh-conditions.json'))
args = parser.parse_args()
rows = []
target = args.output
for name in args.sizes:
    setup_started = perf_counter()
    instance, plan = condition(name)
    setup_seconds = perf_counter()-setup_started
    started = perf_counter()
    warm_report = check(instance, plan)
    warm_up_seconds = perf_counter()-started
    novel = instance.model_copy(deep=True)
    index = next(i for i, box in enumerate(novel.containers)
                 if not box.empty and box.gross_mass_kg+1 < 30480.)
    box = novel.containers[index]
    before_mass = box.gross_mass_kg
    novel.containers[index] = Container.model_validate(
        {**box.model_dump(), 'gross_mass_kg': before_mass+1.})
    novel = Instance.model_validate(novel.model_dump())
    source_before, cache_before = hashes(name), hydrostatic_cache_info()
    started = perf_counter()
    report = check(novel, plan)
    seconds = perf_counter()-started
    if any(v.rule_id == 'checker.invalid_ballast' for v in report.violations):
        raise AssertionError('benchmark plan must name adjustable ballast tanks only')
    source_after = hashes(name)
    row = dict(vessel=name, setup_seconds=setup_seconds, boxes=len(novel.onboard.placements),
               loading_condition='production homogeneous dry departure; one nonempty VGM +1 kg',
               changed_container=box.id, gross_mass_before_kg=before_mass,
               gross_mass_after_kg=before_mass+1., warm_up_seconds=warm_up_seconds,
               fresh_condition_seconds=seconds, cache_before=cache_before,
               cache_after=hydrostatic_cache_info(),
               parts_ran={k: part.ran for k, part in report.parts.items()},
               part_errors={k: part.error for k, part in report.parts.items() if part.error},
               passed=report.passed, versions=component_versions(),
               stability_table_metrics={k: v for k,v in report.parts['stability'].metrics.items()
                                        if 'table' in k},
               displacement_delta_kg=report.parts['stability'].metrics.get('displacement_kg', 0.)
                                     - warm_report.parts['stability'].metrics.get('displacement_kg', 0.),
               source_sha256_before=source_before, source_sha256_after=source_after,
               sources_stable=source_before == source_after,
               rule_ids=sorted({v.rule_id for v in report.violations}))
    rows.append(row)
    target.write_text(json.dumps(rows, indent=2, sort_keys=True)+'\n')
    print(json.dumps(row, sort_keys=True), flush=True)
