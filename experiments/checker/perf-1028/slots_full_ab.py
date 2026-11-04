"""Single-core alternating full-check ablation of the two final candidates."""
from __future__ import annotations
import os
for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '1'
os.environ['STOWAGE_BENCH_LOADING'] = 'homogeneous'

from pathlib import Path
import json
from statistics import median
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_full as fixture
from d57_benchmark import declare_voyage
from stowage.checker import full, slots, strength, strength_assessment


def main():
    ship = sys.argv[1] if len(sys.argv) > 1 else 'feeder_1700'
    setup = perf_counter()
    instance, plan = fixture.condition(ship)
    grouped = {tid for group in instance.vessel.voyage_policy.groups for tid in group.tank_ids}
    instance.onboard.tank_masses_kg.update({tank.id: .5*tank.capacity_m3*tank.content_density_kg_m3
        for tank in instance.vessel.tanks if tank.id in grouped})
    instance = declare_voyage(instance)
    setup = perf_counter()-setup
    original_current_cache = slots.current_cache
    original_assessment = strength.assessment_cuts

    def mode(label):
        slots.current_cache = original_current_cache if label == 'new' else lambda: None
        strength.assessment_cuts = (original_assessment if label == 'new'
                                    else strength_assessment.assessment_cuts_reference)

    reference = None
    priming = {}
    for label in ('old', 'new'):
        mode(label)
        started = perf_counter()
        report = full.check(instance, plan).model_dump(mode='json')
        priming[label] = perf_counter()-started
        if reference is None:
            reference = report
        else:
            assert report == reference, 'new candidates changed the full report'
    timings = {'old': [], 'new': []}
    for repeat in range(5):
        for label in (('old', 'new') if repeat % 2 == 0 else ('new', 'old')):
            mode(label)
            started = perf_counter()
            report = full.check(instance, plan)
            timings[label].append(perf_counter()-started)
            assert report.model_dump(mode='json') == reference
    slots.current_cache = original_current_cache
    strength.assessment_cuts = original_assessment
    print(json.dumps(dict(ship=ship, setup_seconds=setup, priming_seconds=priming,
        samples=timings, medians={key: median(value) for key,value in timings.items()},
        bitwise_equal_reports=True, assessment_cache_info=strength_assessment.assessment_cache_info()), indent=2))


if __name__ == '__main__':
    main()
