"""Post-promotion public availability checks under explicit shared CPU admission.

Prepare only until the leader admits this controller. Shared slots use the same
registry as the numerical campaigns; cache_warm reserves one slot per worker.
No source, saved input, qualification observation or margin bank is modified.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
OWNER = HERE.parent
ROOT = HERE.parents[3]
THREADS = ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
           'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS')


def read(path):
    return json.loads(path.read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def pin(path):
    path = Path(path).resolve()
    return dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def shared_capacity():
    path = HERE / 'compute-capacity.json'
    capacity = read(path)['workers'] if path.exists() else 2
    if type(capacity) is not int or not 1 <= capacity <= 9:
        raise ValueError('shared CPU capacity must be an integer from 1 to 9')
    return capacity


@contextmanager
def reserve(name, cores):
    token = uuid.uuid4().hex
    while True:
        with (HERE / 'compute.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            registry = HERE / 'compute-active.json'
            active = read(registry) if registry.exists() else []
            active = [entry for entry in active if alive(entry['holder_pid'])]
            capacity = shared_capacity()
            if cores > capacity:
                raise ValueError(f'{name} needs {cores} admitted cores, shared capacity is {capacity}')
            if len(active) + cores <= capacity:
                # Existing controllers count registry rows, so one row per core
                # preserves their admission logic without changing their schema.
                active.extend(dict(holder_pid=os.getpid(), name=f'{name}/core-{index}', token=token)
                              for index in range(cores))
                registry.write_text(json.dumps(active, indent=2) + '\n')
                break
            registry.write_text(json.dumps(active, indent=2) + '\n')
        time.sleep(.5)
    try:
        yield
    finally:
        with (HERE / 'compute.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            active = read(HERE / 'compute-active.json')
            (HERE / 'compute-active.json').write_text(json.dumps(
                [entry for entry in active if entry.get('token') != token], indent=2) + '\n')


def junit_counts(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == 'testsuite' else list(root.iter('testsuite'))
    totals = {key: sum(int(suite.get(key, 0)) for suite in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    totals['passed'] = totals['tests'] - sum(totals[key] for key in ('failures', 'errors', 'skipped'))
    totals['seconds'] = sum(float(suite.get('time', 0)) for suite in suites)
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admitted-cores', type=int, choices=range(1, 10), required=True,
                        help='leader-authorized CPU allocation; does not enlarge shared capacity')
    parser.add_argument('--cache-workers', type=int, choices=range(1, 9))
    parser.add_argument('--public-workers', type=int, choices=(1, 2))
    parser.add_argument('--promotion-report', type=Path, default=HERE / 'promotion/report.json')
    parser.add_argument('--baseline-bank', type=Path,
                        default=OWNER / 'production-1028b/promotion/candidate-bank.json')
    parser.add_argument('--attempt', default='final',
                        help='new simple name for a retry; prior logs are never overwritten')
    args = parser.parse_args()
    cache_workers = args.cache_workers or min(3, args.admitted_cores)
    public_workers = args.public_workers or min(2, args.admitted_cores)
    if max(cache_workers, public_workers) > args.admitted_cores:
        parser.error('worker count exceeds the explicitly admitted CPU allocation')
    if args.admitted_cores > shared_capacity():
        parser.error('leader must raise shared compute-capacity.json before using this allocation')
    if Path(args.attempt).name != args.attempt or args.attempt in ('', '.', '..'):
        parser.error('attempt must be a simple directory name')
    output = HERE / 'final-validation' / args.attempt
    output.mkdir(parents=True, exist_ok=True)
    (output / 'controller.pid').write_text(str(os.getpid()) + '\n')
    promotion = read(args.promotion_report)
    bank_path = ROOT / 'src/stowage/linear/margin_policy_data.json'
    bank_pin = pin(bank_path)
    if (promotion.get('passed') is not True or promotion.get('applied') is not True
            or promotion.get('promoted_bank_sha256') != bank_pin['sha256']):
        raise ValueError('fresh policy must already be atomically promoted before validation')
    baseline, bank = read(args.baseline_bank), read(bank_path)
    preserved = {key: dict(canonical_entry_sha256=digest(value),
                          identical=key in bank['entries'] and digest(bank['entries'][key]) == digest(value))
                 for key, value in baseline['entries'].items()}
    if not all(entry['identical'] for entry in preserved.values()):
        raise ValueError('a pre-existing seawater/historical bank entry changed during extension')
    (output / 'baseline-preservation.json').write_text(json.dumps(dict(
        baseline=pin(args.baseline_bank), installed_bank=bank_pin,
        all_baseline_entries_identical=True, entries=preserved), indent=2) + '\n')
    started = time.monotonic()

    def run(name, arguments, *, cores=1, allow_failure=False):
        execution = output / (name + '.execution.json')
        command = ['/usr/bin/nice', '-n', '10', sys.executable, *map(str, arguments)]
        if execution.exists():
            prior = read(execution)
            if prior.get('exit_code') == 0 and prior.get('command') == command and prior.get('bank') == bank_pin:
                print(json.dumps(dict(name=name, reused=True)), flush=True)
                return prior
            raise ValueError('preserve failed/changed attempt; choose --attempt with a new name')
        env = os.environ.copy()
        env.update({key: '1' for key in THREADS})
        with reserve('final-validation/' + name, cores):
            began = time.monotonic()
            with (output / (name + '.log')).open('w') as log:
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                (output / (name + '.pid')).write_text(str(process.pid) + '\n')
                _, status, usage = os.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status)
            result = dict(command=command, pid=process.pid, exit_code=process.returncode,
                wall_s=time.monotonic()-began, peak_rss_bytes=usage.ru_maxrss,
                user_cpu_s=usage.ru_utime, system_cpu_s=usage.ru_stime,
                reserved_cores=cores, native_threads=1, bank=bank_pin)
            execution.write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps(dict(name=name, **result)), flush=True)
        if process.returncode and not allow_failure:
            raise RuntimeError(name + ' failed; inspect preserved log')
        return result

    jobs = [
        ('saved-contexts', [HERE / 'saved_contexts.py', '--phase', 'validate',
                           '--output', output / 'saved-contexts.json']),
        ('freshwater-public', [HERE / 'profile_run.py', OWNER / 'public_validate.py',
                              '--root', HERE / 'ordinary', '--output', output / 'freshwater-public.json',
                              '--latency-variants', '10']),
        ('seawater-public', [OWNER / 'public_validate.py', '--root', OWNER / 'production-1028b/ordinary',
                            '--output', output / 'seawater-public.json', '--latency-variants', '10']),
    ]
    with ThreadPoolExecutor(max_workers=public_workers) as pool:
        pending = [pool.submit(run, name, command) for name, command in jobs]
        for future in as_completed(pending):
            future.result()
    run('cache-warm', ['-m', 'stowage.cache_warm', '--workers', str(cache_workers)], cores=cache_workers)
    xml_path = output / 'linear-feasibility-tests.xml'
    tests_execution = run('linear-feasibility-tests', ['-m', 'pytest', 'tests/linear', 'tests/feasibility', '-q',
        '-o', 'junit_family=legacy', '--junitxml', xml_path], allow_failure=True)
    counts = junit_counts(xml_path)
    # Print counts immediately when pytest finishes, before final report checks.
    print(json.dumps(dict(stage='tests-complete', **counts)), flush=True)
    (output / 'test-counts.json').write_text(json.dumps(counts, indent=2) + '\n')
    if tests_execution['exit_code'] or counts['failures'] or counts['errors']:
        raise ValueError('linear/feasibility verification did not pass')
    saved = read(output / 'saved-contexts.json')
    fresh = read(output / 'freshwater-public.json')
    sea = read(output / 'seawater-public.json')
    if not (saved['passed'] and saved['all_profiles_present']
            and fresh['passed'] and len(fresh['cases']) == 9
            and sea['passed'] and len(sea['cases']) == 9):
        raise ValueError('saved contexts or either nine-case public validation failed')
    if pin(bank_path) != bank_pin:
        raise ValueError('installed margin bank changed during validation')
    result = dict(passed=True, bank=bank_pin, baseline_entries_preserved=True,
        saved_contexts=len(saved['cases']), freshwater_public_cases=9, seawater_public_cases=9,
        cache_workers=cache_workers, public_workers=public_workers, admitted_cores=args.admitted_cores,
        pytest=counts, wall_s=time.monotonic()-started,
        completed_utc=datetime.now(timezone.utc).isoformat())
    (output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
