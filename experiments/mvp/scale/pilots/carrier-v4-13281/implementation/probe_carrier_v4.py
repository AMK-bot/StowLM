"""One recorded TRAIN f60 planner-only probe; never a shipping certificate."""
from __future__ import annotations
import argparse
import faulthandler
import os
import time
import scale_solver as s


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ship', choices=('feeder_500', 'feeder_800'), default='feeder_500')
    parser.add_argument('--mass', choices=('high', 'low'), default='high')
    args = parser.parse_args()
    folder = s.HERE / 'pilots' / f'carrier-v4-{os.getpid()}'
    folder.mkdir(parents=True)
    s.write(folder / 'owner.json', dict(pid=os.getpid(), at=s.now()))
    s.load_base(s.HERE / 'runtime/base')
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.feasibility import FeasibilityConfig
    from stowage.io import load_instance
    from stowage.solver.twostep import TwoStepPlanner
    import carrier_v4 as carrier
    import reservations
    job = next(j for j in s.manifest()['maps'] if j['stage'] == 'f60'
               and j['ship'] == args.ship and j['mass_level'] == args.mass)
    print(job['map_id'], flush=True)
    faulthandler.dump_traceback_later(60, repeat=True)
    start = time.monotonic()
    with rule_freeze_v1(enabled=False), carrier.install():
        restore = reservations.install()
        try:
            result = TwoStepPlanner(feasibility_config=FeasibilityConfig(extra={'global_screen': 0})).plan(
                load_instance(s.train_path(job['input'])), seed=0, time_limit_s=300)
        finally:
            restore()
    faulthandler.cancel_dump_traceback_later()
    s.write(folder / 'raw.json', result.model_dump(mode='json'))
    row = dict(job=job, status=result.status, message=result.message, metrics=result.metrics,
               elapsed_s=time.monotonic()-start, certificate=False, primary_outcome=False)
    s.write(folder / 'result.json', row)
    print(dict(status=result.status, message=result.message,
               carrier_seconds=result.metrics.get('preplan_time_s'),
               terminal_seconds=result.metrics.get('terminal_time_s'),
               carrier_stages=result.metrics.get('carrier_stages'),
               certificate=False), flush=True)


if __name__ == '__main__':
    main()
