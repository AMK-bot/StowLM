"""EXPERIMENT ONLY: reuse exact repeated elastic end-frame responses.

No production source modifications. Numerical libraries use one CPU thread.
"""
import os
for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'

from functools import lru_cache
import json
from pathlib import Path
from statistics import median
import sys
import struct
from time import perf_counter

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_full import homogeneous_departure
import stowage.checker.lashing as lashing
from stowage.vessel.fleet import list_fleet

ORIGINAL = lashing._end_response


def pack(values):
    array = np.asarray(values)
    return array.dtype.str, array.shape, array.tobytes()


@lru_cache(maxsize=2048)
def response(forces, winds, heights, stiffness, cg_heights):
    def unpack(value):
        dtype, shape, payload = value
        return np.frombuffer(payload, dtype=np.dtype(dtype)).reshape(shape).tolist()
    rack, rod, co, si, anchor = ORIGINAL(unpack(forces), unpack(winds), unpack(heights),
        unpack(stiffness), cg_heights=None if cg_heights is None else unpack(cg_heights))
    rack.flags.writeable = False
    return rack, rod, co, si, anchor


def candidate(forces, winds, heights, stiffness, *, cg_heights=None):
    # The production caller provides Python float lists. Other/custom numeric
    # types retain the original provider and its exact arithmetic/validation.
    if (type(stiffness) is not float or any(type(vector) not in (list, tuple)
            or any(type(value) is not float for value in vector)
            for vector in (forces, winds, heights, *(() if cg_heights is None else (cg_heights,))))):
        return ORIGINAL(forces, winds, heights, stiffness, cg_heights=cg_heights)
    rack, *rest = response(pack(forces), pack(winds), pack(heights), pack(stiffness),
        None if cg_heights is None else pack(cg_heights))
    return rack.copy(), *rest


@lru_cache(maxsize=2048)
def packed_response(forces, winds, heights, stiffness, cg_heights):
    def unpack(payload):
        return struct.unpack(f'>{len(payload)//8}d', payload)
    rack, rod, co, si, anchor = ORIGINAL(unpack(forces), unpack(winds), unpack(heights),
        unpack(stiffness)[0], cg_heights=None if cg_heights is None else unpack(cg_heights))
    rack.flags.writeable = False
    return rack, rod, co, si, anchor


def candidate_packed(forces, winds, heights, stiffness, *, cg_heights=None):
    vectors = (forces, winds, heights, *(() if cg_heights is None else (cg_heights,)))
    if (type(stiffness) is not float or any(type(vector) not in (list, tuple)
            or any(type(value) is not float for value in vector) for vector in vectors)):
        return ORIGINAL(forces, winds, heights, stiffness, cg_heights=cg_heights)
    def pack_vector(values):
        return struct.pack(f'>{len(values)}d', *values)
    rack, *rest = packed_response(pack_vector(forces), pack_vector(winds), pack_vector(heights),
        struct.pack('>d', stiffness), None if cg_heights is None else pack_vector(cg_heights))
    return rack.copy(), *rest


def main():
    rows = []
    for name in (['feeder_1700'] if '--full' in sys.argv else list_fleet()):
        instance = homogeneous_departure(name)
        grouped = {tid for group in instance.vessel.voyage_policy.groups for tid in group.tank_ids}
        instance.onboard.tank_masses_kg.update({tank.id: .5*tank.capacity_m3*tank.content_density_kg_m3
            for tank in instance.vessel.tanks if tank.id in grouped})
        if '--full' in sys.argv:
            from d57_benchmark import declare_voyage
            from stowage.checker.full import check
            from stowage.model import MovePlan, BallastCondition
            instance = declare_voyage(instance)
            plan = MovePlan(id='lashing-response-benchmark', instance_id=instance.id,
                call_index=instance.call_index, ballast=BallastCondition(source='planner',
                    tank_masses_kg={tank.id:instance.onboard.tank_masses_kg.get(tank.id,0.)
                        for tank in instance.vessel.tanks if tank.adjustable}))
            evaluate = lambda: check(instance, plan)
            samples = {'reference': [], 'packed': []}
        else:
            evaluate = lambda: lashing.check_lashing(instance, instance.onboard, call_index=instance.call_index)
            samples = {'reference': [], 'candidate': [], 'packed': []}
        expected = None
        for iteration in range(7):
            for mode in samples:
                lashing._end_response = {'reference': ORIGINAL, 'candidate': candidate, 'packed': candidate_packed}[mode]
                started = perf_counter()
                report = evaluate()
                elapsed = perf_counter()-started
                if hasattr(report, 'ran'):
                    assert report.ran, report.error
                dumped = report.model_dump(mode='json')
                if expected is None:
                    expected = dumped
                assert dumped == expected, (name, mode)
                if iteration:
                    samples[mode].append(elapsed)
        lashing._end_response = ORIGINAL
        row = dict(vessel=name, samples=samples,
                   medians={mode:median(values) for mode,values in samples.items()},
                   bitwise_reports_equal=True, cache=response.cache_info()._asdict(), packed_cache=packed_response.cache_info()._asdict())
        rows.append(row)
        print(json.dumps(row), flush=True)
    target = Path(__file__).with_name('lashing-response-full.json') if '--full' in sys.argv else Path(__file__).with_suffix('.json')
    target.write_text(json.dumps(rows, indent=2)+'\n')


def self_test():
    rng = np.random.default_rng(1028)
    samples = 0
    for tiers in range(1, 9):
        for _ in range(20):
            forces = rng.uniform(0., 200000., tiers).tolist()
            winds = rng.uniform(0., 20000., tiers).tolist()
            heights = rng.uniform(2., 3.5, tiers).tolist()
            for cg in (None, (rng.uniform(.1, .9, tiers)*heights).tolist()):
                for stiffness in (15.7e6, 3.7e6):
                    args = (forces, winds, heights, stiffness)
                    expected = ORIGINAL(*args, cg_heights=cg)
                    for actual in (candidate(*args, cg_heights=cg), candidate(*args, cg_heights=cg), candidate_packed(*args, cg_heights=cg), candidate_packed(*args, cg_heights=cg)):
                        for a, b in zip(actual, expected, strict=True):
                            aa, bb = np.asarray(a), np.asarray(b)
                            assert aa.dtype == bb.dtype and aa.shape == bb.shape and aa.tobytes() == bb.tobytes()
                    actual[0][:] = -123456.
                    assert candidate(*args, cg_heights=cg)[0].tobytes() == expected[0].tobytes()
                    samples += 1
    print(json.dumps(dict(randomized_response_cases=samples, bitwise_equal=True)))


if __name__ == '__main__':
    self_test() if '--self-test' in sys.argv else main()
