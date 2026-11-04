"""Time the complete departure checker after D30 cache warm-up.

A homogeneous synthetic wet departure is retained for comparability with D28.
This is an honest checker diagnostic, not a feasible/approved voyage. Five warm
calls follow a cold process call with disk caches retained. Instance construction
is excluded. Record all numerical failures and cache-use flags beside latency.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from stowage.checker.full import check
from stowage.model import BallastCondition, MovePlan

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('d30_departures', ROOT.parent/'hull_tanks'/'departures.py')
departures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(departures)


def run(name, output):
    start = perf_counter()
    instance, condition = departures.build_departure(name)
    assembly = perf_counter()-start
    adjustable = {tank.id for tank in instance.vessel.tanks if tank.adjustable}
    plan = MovePlan(id=instance.id+'.d30_diagnostic', instance_id=instance.id,
        call_index=instance.call_index, ballast=BallastCondition(source='planner',
            tank_masses_kg={key: value for key,value in instance.onboard.tank_masses_kg.items()
                           if key in adjustable}))
    result = dict(vessel=name, condition=condition, instance_build_seconds=assembly,
        timing_scope='complete checker; first call in fresh process, disk caches retained; synthetic wet departure',
        checks=[])
    for index in range(6):
        start = perf_counter()
        report = check(instance, plan)
        row = dict(seconds=perf_counter()-start, passed=report.passed,
            parts={key: dict(ran=part.ran,error=part.error) for key,part in report.parts.items()},
            stability_metrics=report.parts['stability'].metrics,
            violation_ids=sorted(set(v.rule_id for v in report.violations)))
        result['checks'].append(row)
        output.write_text(json.dumps(result,indent=2,default=str)+'\n')
    times = [row['seconds'] for row in result['checks'][1:]]
    result.update(cold_seconds=result['checks'][0]['seconds'],
        warm_seconds=dict(min=min(times), median=median(times), max=max(times)))
    output.write_text(json.dumps(result,indent=2,default=str)+'\n')
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vessel',required=True,choices=('feeder_500','feeder_1000','feeder_1700'))
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=run(args.vessel,args.output)
    print(json.dumps({key:result[key] for key in ('vessel','cold_seconds','warm_seconds')},indent=2))
