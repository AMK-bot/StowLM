"""Bounded diagnostic on one explicitly allowlisted f60 TRAIN map."""
from __future__ import annotations
import argparse
import faulthandler
import os
import signal
import time
import traceback
import scale_solver as s


class ProbeDeadline(BaseException):
    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ship', choices=('feeder_500', 'feeder_800'), default='feeder_800')
    parser.add_argument('--seconds', type=float, default=60.)
    args = parser.parse_args()
    folder = s.HERE / 'pilots' / f'probe-{os.getpid()}'
    folder.mkdir(parents=True)
    s.write(folder / 'owner.json', dict(pid=os.getpid(), at=s.now()))
    def expired(*_):
        raise ProbeDeadline()
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, args.seconds+60)
    faulthandler.dump_traceback_later(20, repeat=True)
    started = time.monotonic()
    stage = 'import'
    row = {}
    try:
        print('loading frozen runtime', flush=True)
        s.load_base(s.HERE / 'runtime/base')
        from stowage.rules.catalogue import rule_freeze_v1
        from stowage.io import load_instance
        from stowage.solver import twostep
        import packing
        print('loading first f60 low TRAIN map', flush=True)
        job = next(j for j in s.manifest()['maps'] if j['stage'] == 'f60'
                   and j['ship'] == args.ship and j['mass_level'] == 'low')
        row['job'] = job
        with rule_freeze_v1(enabled=False):
            stage = 'instance'
            instance = load_instance(s.train_path(job['input']))
            canonical = twostep._canonical_instance(instance)
            stage = 'joint_preplan'
            print('joint packing', flush=True)
            proposal, metrics = packing.joint_preplan(canonical, deadline=time.monotonic()+args.seconds, seed=0)
        row.update(metrics=metrics, proposal_status=proposal.status if proposal else None)
    except ProbeDeadline:
        row.update(status='diagnostic_watchdog', stage=stage)
    except Exception as error:
        row.update(status='exception', stage=stage, error=f'{type(error).__name__}: {error}',
                   traceback=traceback.format_exc())
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        faulthandler.cancel_dump_traceback_later()
        row.update(elapsed_s=time.monotonic()-started, pid=os.getpid())
        s.write(folder / 'result.json', row)
        print(row, flush=True)


if __name__ == '__main__':
    main()
