"""One TRAIN f60 V5 planner diagnostic; optional departure securing, no certificate."""
from __future__ import annotations
import argparse
import faulthandler
import os
import time
import scale_solver as s


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ship', choices=('feeder_500', 'feeder_800'), default='feeder_800')
    parser.add_argument('--mass', choices=('high', 'low'), default='high')
    parser.add_argument('--securing', action='store_true')
    args = parser.parse_args()
    folder = s.HERE / 'pilots' / f'v5-{os.getpid()}'
    s.write(folder / 'owner.json', dict(pid=os.getpid(), at=s.now()))
    s.load_base(s.HERE / 'runtime/base')
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.feasibility import FeasibilityConfig
    from stowage.io import load_instance
    from stowage.moves import apply_plan
    from stowage.checker.lashing import check_lashing
    from stowage.solver.twostep import TwoStepPlanner
    import carrier_v5
    import reservations
    import greedy_v5
    import terminal_v4
    import oracle_v5
    job = next(j for j in s.manifest()['maps'] if j['stage'] == 'f60'
               and j['ship'] == args.ship and j['mass_level'] == args.mass)
    instance = load_instance(s.train_path(job['input']))
    assert s.sha(s.train_path(job['input'])) == job['input_sha256']
    implementation = s.code_hashes()
    s.write(folder / 'declaration.json', dict(job=job, implementation=implementation,
            planner_seconds=300, certificate=False, primary_outcome=False,
            diagnostic_part='departure lashing at inherited ballast' if args.securing else None))
    # Preserve each actual source version before a future experiment can edit it.
    for name in implementation:
        target = folder / 'implementation' / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes((s.HERE / name).read_bytes())
    started = time.monotonic()
    faulthandler.dump_traceback_later(60, repeat=True)
    with rule_freeze_v1(enabled=False), oracle_v5.install(), carrier_v5.install(), greedy_v5.install(), terminal_v4.install():
        restore = reservations.install()
        try:
            result = TwoStepPlanner(feasibility_config=FeasibilityConfig(extra={'global_screen': 0})).plan(
                instance, seed=0, time_limit_s=300)
        finally:
            restore()
    s.write(folder / 'raw.json', result.model_dump(mode='json'))
    row = dict(job=job, status=result.status, message=result.message, metrics=result.metrics,
               elapsed_s=time.monotonic()-started, certificate=False, primary_outcome=False)
    if args.securing and result.status == 'ok':
        replay = apply_plan(instance, result.plan)
        row['native_replay_legal'] = replay.legal
        before = time.monotonic()
        part = check_lashing(instance, replay.final_state.ship, call_index=instance.call_index)
        row['securing_seconds'] = time.monotonic()-before
        row['securing_margins'] = part.margins
        row['securing_hard_violations'] = sum(v.severity == 'hard' for v in part.violations)
        s.write(folder / 'departure-securing.json', part.model_dump(mode='json'))
    faulthandler.cancel_dump_traceback_later()
    row['total_elapsed_s'] = time.monotonic()-started
    s.write(folder / 'result.json', row)
    print(dict(status=row['status'], planner_seconds=row['elapsed_s'],
               carrier_stages=result.metrics.get('carrier_stages'),
               securing_margins=row.get('securing_margins'),
               securing_hard_violations=row.get('securing_hard_violations'), certificate=False), flush=True)


if __name__ == '__main__':
    main()
