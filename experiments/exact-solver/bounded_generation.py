"""Bound public generation wall time, including native calibration calls."""
import argparse
import json
from pathlib import Path
import runpy
import sys
import time
from stowage.solver.exact_deadline import run_with_deadline


def generate(arguments):
    sys.argv=[str(Path(__file__).with_name('current_compare.py')),'generate',*arguments]
    runpy.run_path(sys.argv[0],run_name='__main__')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cap',type=float,default=300.)
    args,remaining=p.parse_known_args()
    start=time.monotonic()
    result=run_with_deadline(generate,(remaining,),deadline=start+args.cap)
    print(json.dumps(dict(stage='generation_watchdog',timed_out=result.timed_out,
        cap_s=args.cap,wall_time_s=time.monotonic()-start,worker_pid=result.worker_pid)),flush=True)
    if result.timed_out:raise SystemExit(124)
