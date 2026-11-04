"""Resumable bounded orchestration: every case runs in a fresh interpreter."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import sys
import time
import os
import signal
from threading import Condition, Event, Lock

from stowage.linear.cache import source_fingerprint

_HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--directory', type=Path, default=_HERE/'narrow-current')
    parser.add_argument('--samples', type=int, default=128)
    parser.add_argument('--signed-metrics',action='store_true')
    parser.add_argument('--require-liquid-tables',action='store_true')
    parser.add_argument('--calibration-samples',type=int,
                        help='Private small pilot calibration; omit for production 192.')
    parser.add_argument('--case', action='append', dest='selected_cases',
                        help='Only run a named ship/level; repeat to select multiple cases.')
    parser.add_argument('--worker-budget-file', type=Path,
                        help='Dynamic JSON {"workers": n}; missing/invalid means zero new leases.')
    args = parser.parse_args()
    assert 1 <= args.workers <= 6
    args.directory.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    stopping = Event()
    def interrupt(signum, frame):
        # The terminal also interrupts our own child process group. Block fresh
        # leases first so completed interrupted children cannot start more work.
        stopping.set()
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, interrupt)
    # Earlier pilots identified the low-GM 1000 full case as the long tail.
    cases = [('feeder_1000','full'),('feeder_1700','full'),('feeder_500','full'),
             ('feeder_1700','medium'),('feeder_1000','medium'),('feeder_500','medium'),
             ('feeder_1700','light'),('feeder_1000','light'),('feeder_500','light')]
    if args.selected_cases:
        wanted=set(args.selected_cases)
        valid={'/'.join(case) for case in cases}
        if not wanted<=valid: parser.error('Unknown case: '+', '.join(sorted(wanted-valid)))
        cases=[case for case in cases if '/'.join(case) in wanted]
    import runpy
    experiment = runpy.run_path(str(_HERE/'narrow.py'))
    epoch_audit = runpy.run_path(str(_HERE/'audit_loader_epoch.py'))
    schema_audit = runpy.run_path(str(_HERE/'audit_schema13_epoch.py'))
    def current_checkpoint(data, ship, level):
        path=args.directory/f'narrow-{ship}-{level}.json'
        current=source_fingerprint()
        epoch_ok=(data.get('source_clean') and data.get('source_fingerprint_end') == current)
        if not epoch_ok and path.exists():
            epoch_ok=epoch_audit['qualified'](path,data,current) or schema_audit['qualified'](path,data,current)
        return (epoch_ok
                and (data.get('signed_metrics_schema_version')==1 if args.signed_metrics else True)
                and (data.get('requires_liquid_tables') and data.get('reference_table_inputs_clean')
                     and data.get('liquid_table_inputs_end')==experiment['liquid_table_snapshot'](ship) if args.require_liquid_tables else True)
                and data.get('stability_free_surface_method') == os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1')
                and data.get('reference_compatible',True)
                and data.get('input_hashes_end') == experiment['input_snapshot'](ship)
                and data.get('departure_manifest_identity') == experiment['current_manifest_identity'](ship,level)
                and data.get('diagnostics',{}).get('samples') == args.samples
                and data.get('calibration',{}).get('samples') == (192 if args.calibration_samples in (None,64) else args.calibration_samples)
                and (data.get('public_checker_comparison') is None or data['public_checker_comparison'].get('passed', False)))
    pid_records = {}
    pid_lock = Lock()
    def record_pid(case, pid, status):
        with pid_lock:
            pid_records['/'.join(case)] = {'pid':pid,'parent_pid':os.getpid(),
                                           'status':status,'elapsed_seconds':time.perf_counter()-started}
            target = args.directory/'child-pids.json'
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(pid_records,indent=2)+'\n')
            temporary.replace(target)
    def run_case(case):
        ship,level = case
        if stopping.is_set():
            return {'ship':ship,'level':level,'status':'cancelled_before_start'}
        target = args.directory/f'narrow-{ship}-{level}.json'
        if target.exists():
            prior = json.loads(target.read_text())
            if current_checkpoint(prior, ship, level):
                return {'ship':ship,'level':level,'status':'current_checkpoint' if prior.get('source_clean') else 'qualified_loader_epoch_checkpoint'}
            archive = target.with_name(target.stem+'.prior-'+prior.get('source_fingerprint_end','unknown')[:12]+'.json')
            target.replace(archive)
        begin = time.perf_counter()
        log = target.with_suffix('.log')
        print(f'START {ship}/{level}', flush=True)
        with log.open('w') as stream:
            command=[sys.executable,str(_HERE/'narrow.py'),'--ship',ship,'--level',level,
                '--samples',str(args.samples),'--seed','3712','--output',str(target)]
            if args.calibration_samples is not None: command.extend(['--calibration-samples',str(args.calibration_samples)])
            if args.signed_metrics:command.append('--signed-metrics')
            if args.require_liquid_tables:command.append('--require-liquid-tables')
            completed = subprocess.Popen(command,
                stdout=stream,stderr=subprocess.STDOUT)
            record_pid(case,completed.pid,'running')
            completed.wait()
            record_pid(case,completed.pid,'completed')
        clean = target.exists() and json.loads(target.read_text()).get('source_clean',False)
        record = {'ship':ship,'level':level,'returncode':completed.returncode,
                  'status':'clean' if completed.returncode == 0 and clean else 'dirty_or_failed',
                  'wall_seconds':time.perf_counter()-begin,'log':str(log)}
        print(json.dumps(record),flush=True)
        return record
    condition = Condition()
    active = set()
    def budget():
        if args.worker_budget_file is None: return args.workers
        try:
            value = json.loads(args.worker_budget_file.read_text())['workers']
            return value if isinstance(value,int) and not isinstance(value,bool) and 0 <= value <= args.workers else 0
        except (OSError, ValueError, KeyError, TypeError): return 0
    def lease_status():
        status = {'active_count':len(active),'active_cases':sorted(active),
                  'worker_budget':budget(),'elapsed_seconds':time.perf_counter()-started}
        target = args.directory/'worker-leases.json'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(status,indent=2)+'\n')
        temporary.replace(target)
        print(json.dumps({'lease_change':status}),flush=True)
    def run(case):
        key = '/'.join(case)
        with condition:
            while not stopping.is_set() and len(active) >= budget(): condition.wait(timeout=1.)
            if stopping.is_set():
                return {'ship':case[0],'level':case[1],'status':'cancelled_before_lease'}
            active.add(key)
            lease_status()
        try:
            return run_case(case)
        finally:
            with condition:
                active.remove(key)
                lease_status()
                condition.notify_all()
    results = []
    with condition:
        lease_status()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        jobs = [executor.submit(run,case) for case in cases]
        for job in as_completed(jobs):
            results.append(job.result())
            (args.directory/'batch-status.json').write_text(json.dumps(
                {'cases':results,'elapsed_seconds':time.perf_counter()-started},indent=2)+'\n')
    current = source_fingerprint()
    stale = []
    for ship,level in cases:
        target = args.directory/f'narrow-{ship}-{level}.json'
        if not target.exists() or not current_checkpoint(json.loads(target.read_text()), ship, level):
            stale.append(f'{ship}/{level}')
    final = {'cases':results,'elapsed_seconds':time.perf_counter()-started,
             'source_fingerprint_end':current,'stale_or_missing_cases':stale}
    (args.directory/'batch-status.json').write_text(json.dumps(final,indent=2)+'\n')
    print(json.dumps(final),flush=True)
    if stale or any(row['status']=='dirty_or_failed' for row in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
