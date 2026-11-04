"""Sequential outer wall caps, including uninterruptible solver setup.

Launch the current uv-managed Python interpreter directly, record each PID, and
stop only that exact child on timeout. A setup timeout is not solver infeasibility
or an OPTIMAL certificate. Logs and reservations remain in our own directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count',type=int,default=1)
    parser.add_argument('--runner',default='canonical.py')
    parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--wall-cap',type=float,default=60.)
    parser.add_argument('--name',default='canonical-bounded')
    args,forward=parser.parse_known_args()
    root=Path(__file__).resolve().parent
    path=root/f'{args.name}.runs.jsonl'
    for offset in range(args.offset,args.offset+args.count):
        runner=(root/args.runner).resolve()
        assert runner.parent==root and runner.suffix=='.py'
        command=[sys.executable,str(runner),'--offset',str(offset),'--limit','1',*forward]
        started=perf_counter()
        logfile=root/f'{args.name}-{offset:04d}.log'
        with logfile.open('w') as stream:
            child=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT)
            reservation=dict(pid=child.pid,offset=offset,cores=1,ram_gb=2,gpu=False,
                             estimated_wall_s=args.wall_cap,start=datetime.now(timezone.utc).isoformat())
            (root/f'{args.name}-running.json').write_text(json.dumps(reservation,indent=2)+'\n')
            status='completed'
            try:code=child.wait(timeout=args.wall_cap)
            except subprocess.TimeoutExpired:
                status='OUTER_TIMEOUT'
                child.terminate()
                try:code=child.wait(timeout=5.)
                except subprocess.TimeoutExpired:
                    child.kill();code=child.wait()
            reservation.update(status=status,returncode=code,actual_wall_s=perf_counter()-started,log=str(logfile))
            with path.open('a') as output:output.write(json.dumps(reservation,sort_keys=True)+'\n')
            (root/f'{args.name}-running.json').unlink()
            print(json.dumps(reservation),flush=True)


if __name__=='__main__':main()
