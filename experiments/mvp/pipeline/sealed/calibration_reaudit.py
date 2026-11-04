"""Fresh-process public-plan audits after the diagnostic E9-mask correction.

All retained development baselines are accounted for. Plans are never searched,
replaced or selected by their result; no-plan rows remain no-plan rows.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .common import HERE, atomic_json, object_hash, read_json, sha256


def child(request_path):
    from .worker import _load, _audit, _calibration_rechecks
    from .driver import public_path
    request = read_json(public_path(request_path))
    source = public_path(request['source'])
    if sha256(source) != request['source_sha256'] or sha256(HERE/'worker.py') != request['worker_sha256']:
        raise ValueError('Development audit source changed')
    original = read_json(source)[request['index']]
    item = request['map']
    instance = _load(dict(item, arm='pipeline', mode='dry-run'))
    measured = _audit(instance, original['plan'], None)
    measured.update(calibration_rechecks=_calibration_rechecks(instance, original['plan'], item['calibration_rules']))
    result = dict(original, **measured)
    result.update(status='checked_plan' if measured['passed'] else 'no_checked_plan',
        audit_verified=True, audit_fresh_process=True, audit_pid=os.getpid(),
        audit_source_sha256=request['worker_sha256'], original_audit_status=original['status'],
        original_source_sha256=request['source_sha256'], original_source=str(source),
        original_source_index=request['index'], diagnostic_mask_correction=True,
        planner_retried=False, numerical_search_performed=False,
        audit_semantics='full checker L0 plus physical replay completeness; construction mask diagnostic only')
    atomic_json(Path(request['output']), result)


def run(study, out):
    from .driver import public_path
    from .real_dry_run import admission, cap_receipt
    study, out = public_path(study), public_path(out)
    if not out.is_relative_to(HERE):
        raise ValueError('Audit output outside sealed folder')
    out.mkdir(parents=True, exist_ok=True)
    config = read_json(study/'study-config.json')
    maps = {m['map_id']: m for m in config['maps'] if m['cohort'] == 'envelope24'}
    entries = []
    for source in (study/'run/rows.json', study/'supplement-rows.json'):
        digest = sha256(source)
        for index, row in enumerate(read_json(source)):
            if row['cohort'] == 'envelope24' and row['arm'] == 'pipeline':
                entries.append((source, digest, index, row))
    if len({row['map_id'] for _, _, _, row in entries}) != len(entries):
        raise ValueError('Duplicate baseline; no result-based selection')
    pinned_worker = sha256(HERE/'worker.py')
    def execute(entry):
        source, digest, index, original = entry
        key = object_hash(original['map_id'])[:20]
        directory = out/'fresh-audits'/key
        directory.mkdir(parents=True, exist_ok=False)
        target = directory/'result.json'
        if not isinstance(original.get('plan'), dict):
            row = dict(original, numerical_search_performed=False, planner_retried=False,
                       audit_not_repeated_reason='No committed native plan; original failure retained')
            atomic_json(target, row)
            return row
        reading = admission()
        request = dict(source=str(source), source_sha256=digest, index=index,
                       map=maps[original['map_id']], worker_sha256=pinned_worker, output=str(target))
        atomic_json(directory/'request.json', request)
        log = (directory/'audit.log').open('w')
        process = subprocess.Popen([sys.executable, '-m', 'experiments.mvp.pipeline.sealed.calibration_reaudit',
                                    '--child', str(directory/'request.json')],
                                   start_new_session=True, stdout=log, stderr=subprocess.STDOUT)
        atomic_json(directory/'DISPATCHED.json', dict(pid=process.pid, pgid=process.pid,
                    admission=reading, deadline_s=300, started=time.time()))
        try:
            code = process.wait(timeout=300)
            if code != 0:
                raise RuntimeError(f'Fresh audit exited {code}')
            return read_json(target)
        except (subprocess.TimeoutExpired, RuntimeError) as error:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (PermissionError, ProcessLookupError):
                    pass
                process.wait(timeout=5)
            row = dict(original, passed=False, audit_verified=False, status='error',
                       audit_error=str(error), planner_retried=False, numerical_search_performed=False)
            atomic_json(target, row)
            return row
        finally:
            log.close()
    began = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(execute, entries))
    atomic_json(out/'rows.json', rows)
    verified = cap_receipt(out, rows, config)
    from .calibration_prepare import summarize
    binding_summary = summarize(config, rows, read_json(study/'STUDY-STOPPED.json'))
    atomic_json(out/'rule-binding-summary.json', binding_summary)
    summary = dict(schema_version='sealed-fresh-development-audit/1', original_study=str(study),
        completed_baselines=len(rows), committed_plans=sum(isinstance(r.get('plan'), dict) for r in rows),
        workers=4, audit_deadline_s=300, elapsed_s=time.monotonic()-began,
        worker_sha256=pinned_worker, planners_retried=0, numerical_search_performed=False,
        unknown_maps=32-len(rows), cap_calibration=verified, sealed_maps_opened=0)
    atomic_json(out/'summary.json', summary)
    print(json.dumps({key: value for key, value in summary.items() if key != 'cap_calibration'} |
                     {'calibration_binding': verified.get('binding'),
                      'calibration_maps': verified.get('maps'),
                      'calibration_blocker': verified.get('reason')}, sort_keys=True))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('study', nargs='?', type=Path)
    parser.add_argument('--out', type=Path, default=HERE/'measurements/calibration-reaudit-D276')
    parser.add_argument('--child', type=Path)
    args = parser.parse_args()
    if args.child:
        child(args.child)
    else:
        run(args.study, args.out)


if __name__ == '__main__':
    main()
