"""Single-core warm stability A/B; the production full benchmark is separate."""
import os
for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'

from contextlib import nullcontext
from functools import lru_cache
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_full import homogeneous_departure
from stowage.checker.perf import without_reuse
from stowage.checker.stability import check_stability
from stowage.checker import stability_reuse
from stowage.checker import stability_loading
from stowage.vessel.fleet import list_fleet
from stowage.vessel.tanks import TankGeometry


@lru_cache(maxsize=96)
def serialized_tank_candidate(payload):
    """Rejected candidate: exact, but JSON serialization erases its speedup."""
    return TankGeometry.from_params(json.loads(payload))


def tank_geometry_candidate(record, ship_params):
    geometry = record.get('geometry', record)
    return serialized_tank_candidate(json.dumps(geometry, sort_keys=True))


def main():
    results = []
    for name in list_fleet():
        instance = homogeneous_departure(name)
        grouped = {tid for group in instance.vessel.voyage_policy.groups for tid in group.tank_ids}
        instance.onboard.tank_masses_kg.update({tank.id: .5*tank.capacity_m3*tank.content_density_kg_m3
            for tank in instance.vessel.tanks if tank.id in grouped})
        samples = {'reference': [], 'axes_only': [], 'reuse': []}
        expected = None
        for iteration in range(6):
            for mode in samples:
                original = stability_loading.TankGeometry
                class Provider:
                    @staticmethod
                    def from_params(record, *, ship_params):
                        return tank_geometry_candidate(record, ship_params)
                stability_loading.TankGeometry = Provider if mode == 'reuse' else TankGeometry
                with without_reuse() if mode == 'reference' else nullcontext():
                    started = perf_counter()
                    report = check_stability(instance, instance.onboard, call_index=instance.call_index)
                    elapsed = perf_counter()-started
                stability_loading.TankGeometry = original
                if not report.ran:
                    raise AssertionError(report.error)
                dumped = report.model_dump(mode='json')
                if expected is None:
                    expected = dumped
                assert expected == dumped, (name, mode)
                if iteration:
                    samples[mode].append(elapsed)
        row = dict(vessel=name, samples=samples,
            medians={mode:median(values) for mode,values in samples.items()},
            bitwise_reports_equal=True,
            axis_cache=stability_reuse._axis_query.cache_info()._asdict(),
            solid_cache=serialized_tank_candidate.cache_info()._asdict())
        print(json.dumps(row), flush=True)
        results.append(row)
    Path(__file__).with_suffix('.json').write_text(json.dumps(results, indent=2)+'\n')


if __name__ == '__main__':
    main()
