"""Launch one real diagnostic stage worker against the current numerical inventory.

This is transport/cache/worker plumbing validation, never an admitted evidence
campaign. The explicit input is synthetic and no physical approval is inferred.
"""
from __future__ import annotations

from eval.snapshot_io import read_snapshot

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from freeze import ROOT, atomic_json, sha256_file, snapshot_numerical_caches
from pin_transport import PIN_PATH, PIN_SHA256, pin_environment

HERE = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout-s', type=float, default=180.)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if not output.is_relative_to(HERE) or output == HERE:
        parser.error('output must be a dedicated campaign-g1 directory')
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    source = args.input.resolve()
    input_hash = sha256_file(source)
    pin_started = time.perf_counter()
    pin = snapshot_numerical_caches(ROOT)
    pin_wall_s = time.perf_counter() - pin_started
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        environment[name] = '1'
    environment = pin_environment(pin, output, environment)
    attempt = uuid.uuid4().hex
    environment.update(CAMPAIGN_ATTEMPT_ID=attempt,
                       CAMPAIGN_CACHE_ROOT=str(output/'cache-work'))
    command = [sys.executable, str(HERE/'stage_worker.py'), '--stage', '5.1',
        '--input', str(source), '--output', str(output/'worker'), '--rung', 'tiny',
        '--search-s', '0.2', '--oracle-s', '0.2', '--ballast-s', '0.2', '--check-s', '0.2',
        '--diagnostic']
    worker_started = time.perf_counter()
    with (output/'worker.log').open('w') as log:
        child = subprocess.Popen(command, cwd=ROOT, env=environment,
            stdout=log, stderr=subprocess.STDOUT)
        atomic_json(output/'pid.json', dict(pid=child.pid, owner_pid=os.getpid(),
            attempt_id=attempt, diagnostic=True, command=command))
        timed_out = False
        try:
            code = child.wait(timeout=args.timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            child.terminate()
            try:
                code = child.wait(timeout=10.)
            except subprocess.TimeoutExpired:
                child.kill()
                code = child.wait()
    result_path = output/'worker/result.json'
    worker = read_snapshot(result_path).json() if result_path.exists() else None
    valid = bool(code == 0 and not timed_out and worker
        and worker.get('adapter_completed') is True and worker.get('attempt_id') == attempt)
    report = dict(label='diagnostic, not evidence', diagnostic=True, evidence_eligible=False,
        admitted=False, admission='transport/cache/worker plumbing only; no epoch or physical approval',
        worker_completed=valid, worker_returncode=code, timed_out=timed_out,
        pin_files=len(pin['files']), pin_file=environment[PIN_PATH],
        pin_file_sha256=environment[PIN_SHA256], pin_file_bytes=Path(environment[PIN_PATH]).stat().st_size,
        environment_bytes=sum(len(k.encode())+len(v.encode())+2 for k,v in environment.items()),
        arg_max=os.sysconf('SC_ARG_MAX'), pin_wall_s=pin_wall_s,
        worker_wall_s=time.perf_counter()-worker_started, total_wall_s=time.perf_counter()-started,
        input=str(source), input_sha256=input_hash, input_unchanged=sha256_file(source)==input_hash,
        attempt_id=attempt, worker_result_sha256=sha256_file(result_path) if result_path.exists() else None,
        worker=worker, cores=1, gpu=False)
    atomic_json(output/'diagnostic-result.json', report)
    print(json.dumps({k:v for k,v in report.items() if k != 'worker'}), flush=True)
    return 0 if valid else 1


if __name__ == '__main__':
    raise SystemExit(main())
