"""Fixed-list fresh-process queue, at most two single-threaded busy workers."""
from pathlib import Path
import argparse
import datetime
import json
import os
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
PYTHON = ROOT/'.venv/bin/python'
MARKER = ROOT/'experiments/mvp/frontier/lab/c1/leader/BENCH-RUNNING'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--list', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=(1, 2), default=2)
    parser.add_argument('--paired', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    pending = [line.strip() for line in args.list.read_text().splitlines()
               if line.strip() and not line.lstrip().startswith('#')]
    args.out.mkdir(parents=True, exist_ok=True)
    logs = HERE/'assignment-logs'/args.out.name
    logs.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1',
        MKL_NUM_THREADS='1', STOWAGE_IN_PORT_WORKERS='1', PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=':'.join(str(ROOT/path) for path in ('src', 'experiments/mvp/frontier', 'experiments/mvp/frontier/lab')))
    running, history = [], []
    status = HERE/(args.out.name+'-jobs.json')
    def save():
        status.write_text(json.dumps({'coordinator_pid': os.getpid(),
            'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'list': str(args.list), 'pending': pending, 'jobs': history}, indent=2)+'\n')
    while pending or running:
        cap = 1 if MARKER.exists() else args.workers
        alive = sorted(running, key=lambda row: row['pid'])
        for index, item in enumerate(alive):
            should_pause = index >= cap
            if should_pause != item['paused'] and item['process'].poll() is None:
                # Only exact child PIDs started and recorded by this coordinator.
                os.kill(item['pid'], signal.SIGSTOP if should_pause else signal.SIGCONT)
                item['paused'] = should_pause
                item['record']['paused_for_bench'] = should_pause
                save()
        for item in list(running):
            code = item['process'].poll()
            if code is not None:
                item['stream'].close()
                item['record'].update(exit_code=code, elapsed_s=time.monotonic()-item['started'])
                running.remove(item)
                print('FINISHED', item['record']['input'], code, flush=True)
                save()
        while pending and len(running) < cap:
            path = pending.pop(0)
            number = len(history)+1
            stream = (logs/f'{number:02}.log').open('w')
            command = ['nice', '-n', '5', str(PYTHON), str(HERE/'assignment_run.py')]
            if args.verify:
                command.extend(['--verify', path, '--out', str(args.out/f'{number:02}.json')])
            else:
                command.extend(['--input', path, '--out', str(args.out)])
            if args.paired:
                command.append('--paired')
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
            record = dict(input=path, pid=process.pid, log=str(logs/f'{number:02}.log'))
            history.append(record)
            running.append(dict(process=process, pid=process.pid, stream=stream,
                started=time.monotonic(), record=record, paused=False))
            save()
            print('STARTED', process.pid, path, flush=True)
        time.sleep(.5)
    save()

if __name__ == '__main__':
    main()
