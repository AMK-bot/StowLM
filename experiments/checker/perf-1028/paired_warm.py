"""Interleaved same-process warm wall/CPU timings after the cold campaigns.

This controls the shared-machine load drift between separate reference and
optimized campaigns. The complete report is required to be bitwise equal.
"""
import os
for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'
from pathlib import Path
import json
import statistics
import sys
from time import perf_counter, process_time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import benchmark_full as fixture
from d57_benchmark import declare_voyage
from stowage.checker.full import check, check_reference
from stowage.vessel.fleet import list_fleet

rows = []
for ship in list_fleet():
    fixture.FLEET = (*fixture.FLEET, ship)
    os.environ['STOWAGE_BENCH_LOADING'] = 'homogeneous'
    instance, plan = fixture.condition(ship)
    grouped = {tid for group in instance.vessel.voyage_policy.groups for tid in group.tank_ids}
    instance.onboard.tank_masses_kg.update({tank.id: .5*tank.capacity_m3*tank.content_density_kg_m3
        for tank in instance.vessel.tanks if tank.id in grouped})
    instance = declare_voyage(instance)
    expected = check_reference(instance, plan).model_dump(mode='json')
    assert check(instance, plan).model_dump(mode='json') == expected
    samples = {mode: [] for mode in ('reference', 'optimized')}
    for iteration in range(7):
        modes = list(samples) if iteration % 2 == 0 else list(reversed(samples))
        for mode in modes:
            function = check_reference if mode == 'reference' else check
            wall, cpu = perf_counter(), process_time()
            actual = function(instance, plan)
            elapsed_cpu, elapsed_wall = process_time()-cpu, perf_counter()-wall
            assert actual.model_dump(mode='json') == expected, (ship, mode)
            samples[mode].append(dict(wall_seconds=elapsed_wall, cpu_seconds=elapsed_cpu))
    row = dict(ship=ship, samples=samples, bitwise_equal=True,
        medians={mode: {clock: statistics.median(sample[clock+'_seconds'] for sample in values)
                       for clock in ('wall', 'cpu')} for mode,values in samples.items()})
    rows.append(row)
    Path(os.environ.get('STOWAGE_PERF_PAIRED_OUTPUT', HERE / 'paired-warm.json')).write_text(json.dumps(rows, indent=2)+'\n')
    print(json.dumps({key: value for key,value in row.items() if key != 'samples'}), flush=True)
