"""Measure full-check part costs without changing any numerical calculation."""
import argparse
import importlib.util
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from stowage.checker import full
from stowage.model import BallastCondition, MovePlan

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('d30_profile_departures', ROOT/'benchmark.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def run(name, output):
    instance, condition = benchmark.departures.build_departure(name)
    adjustable = {tank.id for tank in instance.vessel.tanks if tank.adjustable}
    plan = MovePlan(id='d30_part_profile', instance_id=instance.id, call_index=instance.call_index,
        ballast=BallastCondition(source='planner', tank_masses_kg={
            key:value for key,value in instance.onboard.tank_masses_kg.items() if key in adjustable}))
    full.check(instance, plan)
    original = full._run_part
    parts = {}
    def measured(instance, ship, name, function):
        start = perf_counter()
        result = original(instance, ship, name, function)
        parts.setdefault(name, []).append(perf_counter()-start)
        return result
    full._run_part = measured
    times = []
    try:
        for _ in range(5):
            start = perf_counter()
            report = full.check(instance, plan)
            times.append(perf_counter()-start)
            assert all(part.ran for part in report.parts.values())
            metrics = report.parts['stability'].metrics
            assert metrics['geometry_table_used'] == metrics['liquid_table_used'] == 1.
            assert metrics['liquid_direct_queries'] == 0.
    finally:
        full._run_part = original
    result = dict(ship=name, condition=condition, total_seconds=times,
        total_median_seconds=median(times),
        part_median_seconds={name:median(values) for name,values in parts.items()},
        scope='five warm complete checks; wrapper includes PartReport isolation/copy; synthetic wet departure')
    output.write_text(json.dumps(result,indent=2,default=str)+'\n')
    print(json.dumps({key:result[key] for key in ('ship','total_median_seconds','part_median_seconds')},indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vessel', required=True)
    parser.add_argument('--output', required=True, type=Path)
    options = parser.parse_args()
    run(options.vessel, options.output)
