"""D196 K1c against chance: K1c's exact recipe (port rule in the mask, same seeds, compaction, judge, fallback, 55 maps) with uniform random actions
(CPU) and with untrained Glimmer (GPU), plus greedy alone inside the rule. Each arm's fallback is its own unconstrained run: K1r for random, the null arm
for Glimmer, K1b for 8.2. K1C-CHANCE.md is rewritten after each arm. Detached, resumable. Usage: k1c_chance.py random [workers=4] | null [shards=2] [workers=4]
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import k1r  # sets K1b's 600 s finisher before compact and judge are imported
import k1c_chance_report as report
import judge
import kcurve

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PYTHON = str(ROOT/'.venv/bin/python')
K1B = json.loads((HERE/'manifest.json').read_text())
ARMS = report.ARMS
ENV = {**os.environ, 'STOWLM_MLX_CACHE_LIMIT_GB': '2', 'UV_CACHE_DIR': str(ROOT/'.uv-cache'), 'PYTHONPATH': f'{HERE}:{HERE.parent}'}


def decode_random(job):
    k1r.OUT = ARMS['random']
    return k1r.decode_map(dict(job, port_rule=True))


def judge_arm(pair):
    arm, job = pair
    judge.HERE = ARMS[arm]
    return judge.judge_model(job)


def greedy(pair):
    """Sample 0 alone inside the rule: the k = 1 point of the k-curve, reusing a verdict the judge reached."""
    arm, job = pair
    return kcurve.first_sample(arm, ARMS[arm], job, HERE/'k1c-greedy')


def decode_null(files, shards):
    out = ARMS['null']
    groups = [sorted(files)[i::shards] for i in range(shards)]

    def one(i):
        with open(out/f'decode/s{i}.log', 'a') as stream:
            return subprocess.run([PYTHON, 'experiments/eval-c1/run_c1.py', '--run-dir', str(out/f'decode/s{i}'), '--loose', *groups[i], *K1B['flags'], '--port-rule',
                                   '--store-candidates', str(out/'candidates'), '--decode-only'], cwd=ROOT, env=ENV, stdout=stream, stderr=subprocess.STDOUT).returncode
    (out/'decode').mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(shards) as pool:
        codes = list(pool.map(one, range(shards)))
    if any(codes):
        raise SystemExit(f'decode failed: {codes}')


def finish(arm, jobs, workers):
    with ProcessPoolExecutor(workers) as pool:
        list(pool.map(judge_arm, [(arm, job) for job in jobs]))
        list(pool.map(greedy, [(a, job) for a in report.ready_arms() for job in jobs]))
    report.write()
    (ARMS[arm]/'ARM.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')


def main():
    mode = sys.argv[1]
    files = sorted(K1B['inputs'])
    jobs = judge.jobs(files)
    if mode == 'random':
        workers = int(sys.argv[2]) if len(sys.argv) > 2 else 4
        ARMS['random'].mkdir(exist_ok=True)
        if not (ARMS['random']/'decode.DONE').exists():
            with ProcessPoolExecutor(workers) as pool:
                list(pool.map(decode_random, jobs))
            (ARMS['random']/'decode.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')
        finish('random', jobs, workers)
    else:
        shards, workers = int(sys.argv[2]) if len(sys.argv) > 2 else 2, int(sys.argv[3]) if len(sys.argv) > 3 else 4
        ARMS['null'].mkdir(exist_ok=True)
        while not (HERE/'k2/decode.DONE').exists():  # the ladder has a per-map time limit, so sharing its GPU would cost it samples
            time.sleep(60)
        if not (ARMS['null']/'decode.DONE').exists():
            decode_null(files, shards)
            (ARMS['null']/'decode.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')
        finish('null', jobs, workers)
    ledger = ROOT/'experiments/fleet/ledger.md'
    ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True) if not line.startswith(f'| k1c-chance/{mode} ')))
    with open(ROOT/'experiments/fleet/history.md', 'a') as history:
        history.write(f'\n- k1c-chance/{mode} (fix-t8-sft, 31 Oct): D196; finished {datetime.now().astimezone().isoformat(timespec="minutes")}.\n')


if __name__ == '__main__':
    main()
