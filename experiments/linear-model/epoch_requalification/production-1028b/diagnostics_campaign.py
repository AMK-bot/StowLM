"""Replay the unchanged saved N1 regression and prescribed diagnostics, one CPU.

Every numerical observation is freshly recomputed; the old point and complete
instance are immutable physical inputs, never relabelled numerical evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
HISTORICAL = HERE.parent.parent / 'fix-t3-linear'


def pin(path):
    return dict(path=str(path.resolve().relative_to(ROOT)),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def run(name, arguments, output):
    logs = HERE / 'diagnostic-logs'
    logs.mkdir(exist_ok=True)
    execution = logs / (name + '.execution.json')
    command = ["/usr/bin/nice", "-n", "10",
               sys.executable, *map(str, arguments)]
    env = os.environ.copy()
    env.update({key: "1" for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
        "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS")})
    script_pin = pin(Path(arguments[0]))
    if execution.exists():
        prior = json.loads(execution.read_text())
        if prior['exit_code'] == 0 and prior['command'] == command and prior['script'] == script_pin and output.exists():
            return
        raise ValueError('diagnostic checkpoint is failed or has changed: ' + name)
    if output.exists():
        raise ValueError('preserve incomplete diagnostic output; inspect before restarting: ' + str(output))
    started = time.monotonic()
    with (logs / (name + '.log')).open('w') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        (logs / (name + '.pid')).write_text(str(process.pid) + '\n')
        status = process.wait()
    record = dict(command=command, script=script_pin, pid=process.pid,
                  exit_code=status, seconds=time.monotonic() - started,
                  max_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    execution.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(dict(name=name, **record)), flush=True)
    if status:
        raise RuntimeError(name + ' failed; inspect retained log')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('review', 'pilots', 'all'), default='all')
    args = parser.parse_args()
    if args.phase in ('review', 'all'):
        run('review', [HERE / 'claude-freeze-review-v2.py',
            '--source-state', HISTORICAL / 'claude-edge-seed51/state-011.json',
            '--frozen-instance', HISTORICAL / 'claude-review/feeder_500/frozen-instance.json',
            '--prepared', HERE / 'ordinary', '--output', HERE / 'claude-review-v2'],
            HERE / 'claude-review-v2/feeder_500/necessary-review.json')
        source = HERE / 'claude-review-v2/feeder_500/necessary-review.json'
        target = HERE / 'edges-v3/feeder_500/necessary-review.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise ValueError('preserve conflicting final review observation')
        if not target.exists():
            shutil.copyfile(source, target)
    if args.phase in ('pilots', 'all'):
        run('controlled', [HERE / 'claude-edge-cause.py'], HERE / 'claude-edge-cause.json')
        for name in ('claude-face-pilot', 'claude-face-budget-pilot'):
            run(name, [HERE / (name + '.py')], HERE / (name + '-artifacts/summary.json'))


if __name__ == '__main__':
    main()
