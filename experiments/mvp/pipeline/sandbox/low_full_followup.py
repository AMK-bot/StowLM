"""Finite, cancellable low-load observations of three canonical full checks.

This recorder never skips checker parts and never reuses a base verdict for a
changed plan. The requests are development performance inputs, not a holdout.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time

from .benchmark import HERE, ROOT, read_fixture
from .profile_full import admitted, reading


def admission_status(row, *, now, deadline, cancelled=False, released=True):
    """Pure finite admission policy; used for both initial and subsequent calls."""
    if cancelled:
        return 'cancelled'
    if now >= deadline:
        return 'wait_expired'
    if released and admitted(row, 20.):
        return 'ready'
    return 'waiting'


def record(source, output, *, wait_s=1200., release_file=None, cancel_file=None):
    from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
    from stowage.checker import check, report_matches_inputs
    from stowage.model import Instance, MovePlan
    from .moves import prepare_move
    source, output = Path(source), Path(output)
    fixture = read_fixture(source)
    instance, base = Instance.model_validate(fixture['instance']), MovePlan.model_validate(fixture['plan'])
    selection_file = HERE / 'fixtures/clear190-final.json'
    selection = read_fixture(selection_file)
    requests = [next(row for row in selection['selected'] if row['kind'] == kind)
                for kind in ('swap', 'empty')]
    deadline = time.monotonic() + wait_s
    data = {'synthetic': True, 'mode': 'recorded', 'complete': False, 'status': 'waiting',
            'created_utc': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid(), 'workers': 1,
            'wait_deadline_s': wait_s, 'max_admission_load1': 20.,
            'release_file': str(release_file) if release_file else None,
            'cancel_file': str(cancel_file) if cancel_file else None,
            'source_fixture': str(source), 'source_fixture_sha256': sha256_file(source),
            'instance_sha256': sha256_json(instance.model_dump(mode='json')),
            'source_plan_sha256': sha256_json(base.model_dump(mode='json')),
            'n_boxes': len(instance.load_list), 'source_sha256': {
                name: sha256_file(ROOT / name) for name in (
                    'src/stowage/checker/full.py', 'src/stowage/checker/perf.py',
                    'experiments/mvp/pipeline/sandbox/low_full_followup.py',
                    'experiments/mvp/pipeline/sandbox/moves.py')},
            'performance_selection_source': selection_file.name,
            'performance_selection_sha256': sha256_file(selection_file),
            'performance_requests': [{key: row[key] for key in ('kind', 'box_id', 'target_slot')}
                                     for row in requests],
            'native_thread_caps': {name: os.environ.get(name) for name in (
                'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')},
            'method': 'One process, native thread caps 1; fresh canonical check(instance, plan) with default arguments '
                      'for the base and two genuine changed development requests (swap and empty). No candidate '
                      'report is reused. Model loading, preparation and admission waits are outside reported '
                      'full-check wall time. A low-load row requires all sampled fresh fleet readings <=20; '
                      'admission alone is not a sustained-low-load claim. Physics performance has priority '
                      'through the explicit release control; admission waiting has a finite deadline.',
            'admission_observations': [], 'runs': []}

    def save():
        output.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')

    def wait():
        while True:
            row = reading()
            data['admission_observations'].append(row)
            status = admission_status(row, now=time.monotonic(), deadline=deadline,
                cancelled=bool(cancel_file and Path(cancel_file).exists()),
                released=not release_file or Path(release_file).exists())
            data['status'] = status
            save()
            if status != 'waiting':
                return status
            time.sleep(5)

    print(json.dumps({'pid': os.getpid(), 'output': str(output), 'wait_s': wait_s,
                      'release_file': data['release_file'], 'cancel_file': data['cancel_file']}), flush=True)
    save()
    for index, request in enumerate([None, *requests]):
        if wait() != 'ready':
            break
        if request:
            prepared = prepare_move(instance, base, request['box_id'], request['target_slot'])
            plan = prepared.plan
            detail = {key: request[key] for key in ('kind', 'box_id', 'target_slot')}
            detail['replay_legal'] = prepared.replay.legal
        else:
            plan, detail = base, {'kind': 'base'}
        if wait() != 'ready':
            break
        samples, stop = [reading()], threading.Event()

        def sample():
            while not stop.wait(2):
                samples.append(reading())

        sampler = threading.Thread(target=sample, name='low-full-fleet-sampler', daemon=True)
        sampler.start()
        data['status'] = 'running'
        save()
        started = time.perf_counter()
        try:
            report = check(instance, plan)
        finally:
            wall_s = time.perf_counter() - started
            stop.set()
            sampler.join()
        samples.append(reading())
        row = {**detail, 'case': index, 'wall_s': wall_s, 'passed': report.passed,
               'input_binding_matches': report_matches_inputs(report, instance, plan),
               'plan_sha256': sha256_json(plan.model_dump(mode='json')),
               'all_parts_ran': all(part.ran and not part.error for part in report.parts.values()),
               'parts': sorted(report.parts), 'load_samples': samples,
               'sustained_low_load': all(admitted(r, 20.) for r in samples),
               'report': report.model_dump(mode='json')}
        data['runs'].append(row)
        data['status'] = 'case_complete'
        save()
        print(json.dumps({key: row[key] for key in ('case', 'kind', 'wall_s', 'passed', 'sustained_low_load')}), flush=True)
        if index == 0 and not report.passed:
            data['status'] = 'base_failed'
            break
    else:
        data['status'], data['complete'] = 'complete', True
    data['finished_utc'] = datetime.now(timezone.utc).isoformat()
    save()
    print(json.dumps({'status': data['status'], 'complete': data['complete'], 'runs': len(data['runs'])}), flush=True)
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=HERE / 'fixtures/envelope190.json')
    parser.add_argument('--output', type=Path, default=HERE / 'fixtures/profile190_lowload_followup.json')
    parser.add_argument('--wait-s', type=float, default=1200.)
    parser.add_argument('--release-file', type=Path)
    parser.add_argument('--cancel-file', type=Path, default=HERE / 'fixtures/profile190_lowload_followup.cancel')
    args = parser.parse_args()
    record(args.source, args.output, wait_s=args.wait_s, release_file=args.release_file, cancel_file=args.cancel_file)
