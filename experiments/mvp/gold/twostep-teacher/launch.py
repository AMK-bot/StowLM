"""Start the shards detached on d151-manifest.json under a fresh launch id, one thread each, hash seed 0. Receipts are
per launch, so one left by an earlier launch can't satisfy d151.admit. Usage: launch.py [workers=6]"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import argparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('workers', type=int, nargs='?', default=6)
parser.add_argument('--run-dir', type=Path)
args = parser.parse_args()
workers = args.workers
if not 1 <= workers <= 8:
    parser.error('workers must be between 1 and 8')
run_dir = args.run_dir.resolve() if args.run_dir else HERE
run_dir.mkdir(parents=True, exist_ok=True)
if args.run_dir and (run_dir/'pids.txt').exists():
    for recorded in (run_dir/'pids.txt').read_text().split():
        try:
            os.kill(int(recorded), 0)
        except ProcessLookupError:
            continue
        raise SystemExit(f'recorded worker {recorded} is still alive; do not overlap launches')
env = {**os.environ, 'PYTHONHASHSEED': '0', 'PYTHONPATH': str(HERE)}
env.update({k: '1' for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')})
env.pop('STOWAGE_BALLAST_V4', None)
python = str(ROOT/'.venv/bin/python')
launch = dict(launch_id=str(uuid.uuid4()), workers=workers)
if args.run_dir:
    launch.update(run_dir=str(run_dir), manifest=str(run_dir/'manifest.json'))
(run_dir/'launch.json').write_text(json.dumps(launch)+'\n')
pids = []
for shard in range(workers):
    with open(run_dir/f'shard-{shard}.log', 'a') as log:
        pids.append(subprocess.Popen([python, str(HERE/'run_teacher.py'), 'run', str(shard), str(run_dir/'launch.json')], cwd=ROOT, env=env,
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True).pid)
(run_dir/'pids.txt').write_text('\n'.join(map(str, pids))+'\n')
print(launch['launch_id'], pids)
