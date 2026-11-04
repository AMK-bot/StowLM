"""Scoped/E6 construction diagnostic on metadata-first f60 TRAIN maps only."""
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
    parser.add_argument('--ship', choices=('feeder_500', 'feeder_800'), default='feeder_500')
    parser.add_argument('--mass', choices=('high', 'low'), default='high')
    parser.add_argument('--seconds', type=float, default=60.)
    args = parser.parse_args()
    folder = s.HERE / 'pilots' / f'v3-probe-{os.getpid()}'
    folder.mkdir(parents=True)
    s.write(folder / 'owner.json', dict(pid=os.getpid(), at=s.now()))
    def expired(*_):
        raise ProbeDeadline()
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, args.seconds+60)
    faulthandler.dump_traceback_later(30, repeat=True)
    started = time.monotonic()
    row = dict(candidate='packing_v3', primary_outcome=False)
    try:
        s.load_base(s.HERE / 'runtime/base')
        from stowage.rules.catalogue import rule_freeze_v1
        from stowage.io import load_instance
        from stowage.solver import twostep
        import packing_v3
        job = next(j for j in s.manifest()['maps'] if j['stage'] == 'f60'
                   and j['ship'] == args.ship and j['mass_level'] == args.mass)
        row['job'] = job
        print(job['map_id'], flush=True)
        with rule_freeze_v1(enabled=False):
            instance = load_instance(s.train_path(job['input']))
            canonical = twostep._canonical_instance(instance)
            proposal, metrics = packing_v3.joint_preplan(canonical,
                deadline=time.monotonic()+args.seconds, seed=0)
        row.update(metrics=metrics, proposal_status=proposal.status if proposal else None,
                   actions=[dict(box=a.box, slot=a.slot) for a in proposal.actions] if proposal else [])
    except ProbeDeadline:
        row.update(status='diagnostic_watchdog')
    except Exception as error:
        row.update(status='exception', error=f'{type(error).__name__}: {error}',
                   traceback=traceback.format_exc())
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        faulthandler.cancel_dump_traceback_later()
        row.update(elapsed_s=time.monotonic()-started, pid=os.getpid())
        s.write(folder / 'result.json', row)
        print({k: v for k, v in row.items() if k not in ('job', 'actions')}, flush=True)


if __name__ == '__main__':
    main()
