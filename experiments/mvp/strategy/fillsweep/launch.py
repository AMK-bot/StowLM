"""Detached D192 supervisor: generate, verify, plan and refresh first read.

Only our exact subprocess identities are owned; no signal or process scans.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
             'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'HIGHS_THREADS'):
    os.environ[name] = '1'
os.environ['STOWAGE_IN_PORT_WORKERS'] = '1'
ROW = '| fillsweep/run | D192 TRAIN fill×mass; 1 generation CPU then ≤3 maps+3 owned V4 children; conditional decode window, pause on CPU judging | {cores} | 12 | no | first read ≤11:00Z; full ladder may take 24h+; native1 | {start} |\n'


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    temporary = path.with_name(path.name + f'.tmp-{os.getpid()}')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def ledger(cores, start, finished=None):
    fleet = ROOT / 'experiments/fleet'
    with (fleet / 'ledger.md.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = fleet / 'ledger.md'
        lines = [line for line in path.read_text().splitlines(keepends=True)
                 if not line.startswith('| fillsweep/run |')]
        if finished is None:
            row = ROW.format(cores=cores, start=start)
            index = next(i for i, line in enumerate(lines) if line.startswith('|---|')) + 1
            lines.insert(index, row)
        path.write_text(''.join(lines))
        if finished is not None:
            with (fleet / 'history.md').open('a') as stream:
                stream.write(f'\n- fillsweep/run (D192): {finished}; start {start}, end {now()}; peak reservation6 CPU/12GB, no GPU; estimate24h+ ceiling, actual reservation wall recorded in fillsweep/supervisor-status.json.\n')


def refresh():
    # Report reads durable artifacts only; no planner/checker imports.
    subprocess.run([sys.executable, str(HERE / 'report.py')], cwd=ROOT,
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)


def supervise():
    start = now()
    started = time.time()
    status = dict(pid=os.getpid(), start=start, phase='initializing', owned_children=[])
    write(HERE / 'supervisor-status.json', status)
    # A lock prevents two resumed supervisors from generating concurrently.
    with (HERE / 'supervisor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger(1, start)
        try:
            phases = [
                ('generation', [sys.executable, str(HERE / 'generate.py'), 'generate']),
                ('generation_verification', [sys.executable, str(HERE / 'generate.py'), 'verify']),
                ('planning', [sys.executable, str(HERE / 'run.py'), '--workers', '3', '--load-limit', '18']),
            ]
            for phase, command in phases:
                ledger(6 if phase == 'planning' else 1, start)
                with (HERE / f'{phase}.log').open('a') as stream:
                    child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                             stdout=stream, stderr=subprocess.STDOUT)
                status['owned_children'].append(dict(pid=child.pid, phase=phase, command=command,
                                                    launched_at=now()))
                status.update(phase=phase, active_child_pid=child.pid, updated_at=now())
                write(HERE / 'supervisor-status.json', status)
                write(HERE / f'{phase}-launch.json', status['owned_children'][-1])
                while child.poll() is None:
                    refresh()
                    time.sleep(20)
                status['owned_children'][-1].update(exit_code=child.returncode, ended_at=now())
                if child.returncode:
                    raise RuntimeError(f'{phase} exited {child.returncode}; resume with identical launcher after resolving recorded infrastructure failure')
                refresh()
            status.update(phase='complete', ended_at=now(), elapsed_s=time.time()-started)
            ledger(0, start, finished='complete')
        except Exception as error:
            status.update(phase='error', error=f'{type(error).__name__}: {error}',
                          traceback=traceback.format_exc(), ended_at=now(), elapsed_s=time.time()-started)
            ledger(0, start, finished=f'error, resumable: {error}')
            raise
        finally:
            write(HERE / 'supervisor-status.json', status)
            refresh()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    if args.detach:
        command = [sys.executable, str(Path(__file__).resolve())]
        with (HERE / 'supervisor.log').open('a') as stream:
            child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                     stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        write(HERE / 'supervisor-launch.json', dict(pid=child.pid, command=command, launched_at=now(),
              launcher_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
        print(json.dumps(dict(pid=child.pid, command=command)))
    else:
        supervise()


if __name__ == '__main__':
    main()
