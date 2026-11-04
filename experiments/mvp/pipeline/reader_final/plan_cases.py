"""CPU step: twelve of D211's replans on Engine 0.13.0 through the real runner, then stop.

run_packet at the standard profile does the whole path: assemble, envelope, read,
confirm, plan, recheck, receipt. Stored masked readings replay; nothing is read here.
confirm() stands in for a planner comparing the reading with the registered meaning, so
a wrong reading is never confirmed. Finished runs are kept, so a rerun resumes.
Usage: python -m experiments.mvp.pipeline.reader_final.plan_cases [--job ID]
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys

from experiments.mvp.reader.evaluate import normalized
from .read_all import HERE, d211_cases, instance_of, load_rows, packet
from .reader import ROOT, StoredReadings

RUNS = HERE / 'runs'
WORKERS = 4
WALL_S = 1800.
ATTEMPTS = 2
SOURCE = 'batch:d211-rerun:reading-compared-with-registered-meaning'


def same_meaning(answer, expected, context):
    try:
        return answer is not None and normalized(answer, context) == normalized(expected, context)
    except (ValueError, TypeError):
        return False


def jobs():
    return [{'id': case['case_id'], 'input': case['input'], 'sentence': case['sentence'],
             'expected': case['expected']} for case in d211_cases()]


def finished(job, runs=RUNS):
    return sorted((runs / job['id']).glob('attempt-*/outcome.json'))


def run_job(job, runner=None, runs=RUNS):
    from experiments.mvp.pipeline.runner.core import run_packet
    from experiments.mvp.pipeline.runner.profiles import measured_profile  # noqa: F401  fails early if unmeasured
    runner = runner or run_packet
    context = instance_of(job['input'], {})

    def confirm(row):
        return same_meaning(row.get('parsed'), job['expected'], context)

    attempt = len(list((runs / job['id']).glob('attempt-*'))) + 1
    return runner(packet(job['input'], job['id'], [job['sentence']]), parser=StoredReadings(load_rows().values()),
                  out_dir=runs / job['id'] / f'attempt-{attempt}', confirm=confirm,
                  confirmation_source=SOURCE, engine_timeout_s=WALL_S)


def launch(job):
    for _ in range(ATTEMPTS):
        if finished(job):
            return job['id'], 0
        code = subprocess.run([sys.executable, '-m', 'experiments.mvp.pipeline.reader_final.plan_cases',
                               '--job', job['id']], cwd=ROOT).returncode
    return job['id'], code


def main():
    with ThreadPoolExecutor(WORKERS) as pool:
        for name, code in pool.map(launch, [job for job in jobs() if not finished(job)]):
            print(json.dumps({'job': name, 'exit_code': code}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job')
    arguments = parser.parse_args()
    if arguments.job:
        run_job(next(job for job in jobs() if job['id'] == arguments.job))
    else:
        main()
