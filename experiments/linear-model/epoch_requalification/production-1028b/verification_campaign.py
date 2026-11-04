"""Run unchanged candidate gates or post-promotion evidence replays.

Candidate residual and individual-row gates precede atomic owner promotion.
Ordinary exact replay and the input manifest follow promotion, because public
builders fail closed while the installed production bank is stale.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import resource
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent


def run(name, arguments):
    logs = HERE / 'verification-logs'
    logs.mkdir(exist_ok=True)
    command = ["/usr/bin/nice", "-n", "10",
               sys.executable, *map(str, arguments)]
    env = os.environ.copy()
    env.update({key: "1" for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
        "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS")})
    began = time.monotonic()
    with (logs / (name + '.log')).open('w') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        (logs / (name + '.pid')).write_text(str(process.pid) + '\n')
        status = process.wait()
    record = dict(command=command, pid=process.pid, exit_code=status,
                  seconds=time.monotonic() - began,
                  max_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    (logs / (name + '.execution.json')).write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(dict(name=name, **record)), flush=True)
    if status:
        raise RuntimeError(name + ' failed; inspect retained log')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('candidate', 'ordinary', 'manifest'), required=True)
    parser.add_argument('--bank', type=Path)
    parser.add_argument('--workers', type=int, default=1, choices=(1, 2, 3),
                        help='admitted single-thread workers for ordinary exact replay only')
    args = parser.parse_args()
    if args.phase == 'candidate':
        if args.bank is None:
            parser.error('candidate phase requires --bank')
        run('candidate-residuals', [HERE / 'claude-prior-holdout-gate.py', '--bank', args.bank,
            '--output', HERE / 'candidate-residual-gate.json'])
        run('candidate-rows', [HERE / 'claude_edge_audit.py', '--bank', args.bank,
            '--output', HERE / 'candidate-row-audit.json'])
    elif args.phase == 'ordinary':
        common = [HERE / 'd49_holdout_audit.py',
            '--input-root', HERE / 'ordinary', '--output', HERE / 'holdout-audit']
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            tasks = [pool.submit(run, 'ordinary-replay-' + ship + '-' + level,
                [*common, '--phase', 'replay', '--ship', ship, '--level', level])
                for ship in ('feeder_500', 'feeder_1000', 'feeder_1700')
                for level in ('light', 'medium', 'full')]
            for task in as_completed(tasks):
                task.result()
        run('ordinary-rows', [*common, '--phase', 'rows'])
        run('ordinary-summary', [*common, '--phase', 'summary'])
    else:
        run('audit-inputs', [HERE / 'claude_audit_inputs.py'])


if __name__ == '__main__':
    main()
