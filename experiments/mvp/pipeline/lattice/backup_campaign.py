"""Isolated feeder800 campaign; no primary campaign control or catalogue writes."""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from . import batch
from .report import collect, render_table, _atomic_text
from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json

HERE = Path(__file__).resolve().parent
DIRECTORY = HERE/'backup-campaign'


def utc():
    return datetime.now(timezone.utc).isoformat()


def worker_limit(reading):
    """Reduce admissions, never interrupt work already admitted."""
    return 0 if reading is None else 8 if reading['load1'] < 30 else 4


def profile_snapshot():
    from experiments.mvp.pipeline.runner.profiles import get_profile
    profile = get_profile('standard')
    if (profile['budget_s'], profile['finalize_s'], profile['engine_timeout_s']) != (300., 100., 1800.):
        raise ValueError('Backup requires selected standard 300/100 with independent 1800s engine wall cap')
    return {key: asdict(value) if key == 'config' else value for key, value in profile.items()}


def prepare(directory=DIRECTORY):
    directory = Path(directory).resolve()
    selected = json.loads((directory/'instructions.json').read_text())
    base_path = batch.ROOT/selected['base_fixture']
    if sha256_file(base_path) != selected['base_fixture_sha256']:
        raise ValueError('Frozen backup fixture changed')
    base = json.loads(base_path.read_text())
    if sha256_json(base['instance']) != selected['instance_sha256'] or sha256_json(base['plan']) != selected['base_plan_sha256']:
        raise ValueError('Backup instance or base plan identity mismatch')
    from stowage.io import load_instance
    instance_path = batch.ROOT/selected['instance_path']
    if sha256_json(load_instance(instance_path).model_dump(mode='json')) != selected['instance_sha256']:
        raise ValueError('Selected instance content differs from frozen backup')
    from .store import canonical_rule_set, entry_key
    specs = selected['instructions']
    if len(specs) != 12 or len({s['id'] for s in specs}) != 12:
        raise ValueError('Exactly twelve distinct backup instructions required')
    keys = [entry_key(selected['instance_sha256'], canonical_rule_set(s['rules'])) for s in specs]
    if len(set(keys)) != 12 or any(s['purpose'] != 'lattice' for s in specs):
        raise ValueError('Backup instructions must have distinct canonical keys and standard purpose')
    profile = profile_snapshot()
    calibration = Path(profile['calibration_source'])
    if sha256_file(calibration) != selected['profile_selection']['selection_file_sha256']:
        raise ValueError('Profile selection changed since instruction selection')
    manifest = dict(schema_version='stowlm.backup-campaign/1', created_utc=utc(),
        backup_campaign_id='feeder800-12', instance_path=str(instance_path),
        instance_sha256=selected['instance_sha256'], base_plan=base['plan'],
        base_fixture_sha256=selected['base_fixture_sha256'], profile=profile,
        profile_selection_path=str(calibration), profile_selection_sha256=sha256_file(calibration),
        source_sha256=sha256_file(Path(__file__)), instructions=specs, keys=keys,
        run_dir=str(directory/'runs'), store_dir=str(directory/'store'),
        fixtures_dir=str(directory/'fixtures'), state_path=str(batch.STATE),
        concurrency=dict(low_load1_below=30, low_load_workers=8, other_admitted_workers=4),
        eta=dict(measurement='projection', engine_waves_at_eight_workers=2,
                 engine_allowance_minutes=60, note='Two 1800s engine waves at eight workers; admission, native gates and repairs add unbounded time. Not a completion guarantee.'))
    batch.write_json(directory/'fixtures'/'base-source.json', base)
    batch.write_json(directory/'manifest.json', manifest)
    update_report(directory)
    return manifest


def update_report(directory=DIRECTORY):
    from .sequence_provenance import frame_provenance
    directory = Path(directory)
    with (directory/'.report.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = json.loads((directory/'manifest.json').read_text())
        base_path = directory/'fixtures'/'base-source.json'
        base_record = json.loads(base_path.read_text()) if base_path.exists() else dict(plan=manifest.get('base_plan', {}))
        rows = []
        for spec, key in zip(manifest['instructions'], manifest['keys']):
            row = None
            for suffix in ('.json', '.running.json'):
                path = Path(manifest['run_dir'])/(key+suffix)
                if path.exists():
                    row = json.loads(path.read_text())
                    if suffix == '.running.json':
                        row = dict(row, status='running')
                    break
            rows.append(collect(row or dict(spec, key=key, status='pending'), base_record=base_record))
        base_sequence = frame_provenance(base_record)
        data = dict(as_of_utc=utc(), instance_sha256=manifest['instance_sha256'], rows=rows,
                    base_frame_provenance=base_sequence,
                    instructions_stored=sum(r['status'] == 'checked_plan' for r in rows),
                    proved_refusals=sum(r['status'] == 'proved_refusal' for r in rows))
        batch.write_json(directory/'progress.json', data)
        table = render_table(data)
        _atomic_text(directory/'RULES.md', table)
        _atomic_text(directory/'README.md',
            '# Isolated feeder800 backup campaign\n\nSynthetic development voyage: 163 boxes; frozen base 115 extra lifts. '
            f"Baseline: {base_sequence['label']}; {base_sequence['frames_checked']} crane frames walked on this plan; "
            f"all passed: {base_sequence['all_frames_passed']}. "
            'Twelve independently requested instructions use the user-selected standard profile, 300 s budget / 100 s finalize, '
            'with a separate 1800 s engine wall deadline per solver call. Profile source: '
            '`experiments/mvp/pipeline/runner/fixtures/profile-selection.json`; exact hash and Config in manifest.json.\n\n'
            'Run `bash experiments/mvp/pipeline/lattice/backup-campaign/run.sh`. The detached supervisor requires PPID 1 and '
            'tests signals on its own child groups before solving. Admission requires mask popcount >=8, load1 <=45, age <120 s. '
            'It admits eight workers below load1 30 and four otherwise; a load increase stops new admissions without killing active jobs. '
            'Each result, incumbent and frame checkpoint is isolated here. Every accepted plan uses the original full checker, '
            'all requested rules and every native gauge frame. Stored outputs are labelled stored with computation UTC. '
            'The table labels each selected plan separately from its repaired comparison baseline. '
            'A repaired selected plan is labelled crane sequence repaired; native_engine_output is false in its presentation provenance. '
            'Frame counts describe crane frames walked on this plan, not an engine-wide pass rate. '
            'Primary hero, campaign control and catalogue are untouched.\n\n'
            'Presentation-only provenance labels were added after launch; manifest.source_sha256 retains the historical launch source. '
            'Scheduling, profiles, acceptance gates and producer algorithms were unchanged.\n\n'
            'ETA projection: at eight workers, twelve full-cap calls need two engine waves (up to 60 engine minutes), '
            'plus admission, fresh native gates and bounded repairs. This is not a measured completion ETA or a guarantee.\n\n'
            f"As of {data['as_of_utc']}: {data['instructions_stored']} stored instructions; {data['proved_refusals']} proved refusals.\n\n"+table)
        return data


def compute(job):
    directory = Path(job['run_dir']).parent
    manifest = json.loads((directory/'manifest.json').read_text())
    if sha256_file(Path(manifest['profile_selection_path'])) != manifest['profile_selection_sha256'] or profile_snapshot() != manifest['profile']:
        raise ValueError('Pinned backup profile changed before solver admission')
    # Redirect only this process's reporting, not shared source files or main state.
    batch.refresh_progress = lambda run_dir: update_report(Path(run_dir).parent)
    return batch.compute(job)


def signal_preflight(directory):
    rows = []
    for chosen in (signal.SIGTERM, signal.SIGKILL):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        row = dict(pid=child.pid, signal=chosen.name, owned=True)
        try:
            os.killpg(child.pid, chosen)
            row.update(exit=child.wait(timeout=5), passed=True)
        except (PermissionError, ProcessLookupError, subprocess.TimeoutExpired) as error:
            row.update(passed=False, error=type(error).__name__)
        rows.append(row)
        batch.write_json(Path(directory)/'daemon-signal-preflight.json', rows)
        if not row['passed']:
            raise RuntimeError('Detached owned-child signal preflight failed; launch outside sandbox')
    return rows


def supervise(directory=DIRECTORY):
    directory = Path(directory).resolve()
    with (directory/'.supervisor.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = json.loads((directory/'manifest.json').read_text())
        state = dict(pid=os.getpid(), ppid=os.getppid(), session_id=os.getsid(0), started_utc=utc(),
                     status='preflight', active=[], completed=[])
        batch.write_json(directory/'supervisor.json', state)
        if os.getppid() != 1:
            raise RuntimeError('Detached supervisor must be reparented to PID 1')
        signal_preflight(directory)
        queue = list(zip(manifest['instructions'], manifest['keys']))
        pending = {}
        with ProcessPoolExecutor(max_workers=8, mp_context=multiprocessing.get_context('spawn'), max_tasks_per_child=1) as executor:
            while queue or pending:
                reading = batch.capacity_reading(manifest['state_path'])
                slots = worker_limit(reading)
                while queue and len(pending) < slots:
                    spec, key = queue.pop(0)
                    job = {k: manifest[k] for k in ('instance_path', 'base_plan', 'run_dir', 'store_dir', 'fixtures_dir', 'state_path')}
                    job['spec'] = spec
                    pending[executor.submit(compute, job)] = (spec, key)
                state.update(status='running' if pending else 'waiting_admission',
                             active=[dict(id=s['id'], key=k) for s, k in pending.values()],
                             queued=[s['id'] for s, _ in queue], admission=reading, target_workers=slots, updated_utc=utc())
                batch.write_json(directory/'supervisor.json', state)
                done, _ = wait(pending, timeout=2, return_when=FIRST_COMPLETED) if pending else (set(), set())
                if not pending:
                    time.sleep(2)
                for future in done:
                    spec, key = pending.pop(future)
                    try:
                        result = future.result()
                    except Exception as error:
                        result = dict(spec, key=key, status='error', reason=f'{type(error).__name__}: {error}', worker_released=True, release_utc=utc())
                        batch.write_json(Path(manifest['run_dir'])/(key+'.json'), result)
                        (Path(manifest['run_dir'])/(key+'.running.json')).unlink(missing_ok=True)
                    state['completed'].append(result)
                    if result.get('status') == 'rerun_required':
                        queue.append((spec, key))
                    update_report(directory)
        state.update(status='complete', active=[], queued=[], finished_utc=utc())
        batch.write_json(directory/'supervisor.json', state)
        update_report(directory)


def daemonize(directory):
    first = os.fork()
    if first:
        os.waitpid(first, 0)
        return False
    os.setsid()
    second = os.fork()
    if second:
        os._exit(0)
    # Preserve native stdout in an independent durable log, no PTY dependency.
    with open(Path(directory)/'daemon.log', 'a', buffering=1) as log, open(os.devnull) as quiet:
        os.dup2(quiet.fileno(), 0)
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
    for _ in range(100):
        if os.getppid() == 1:
            break
        time.sleep(.05)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=DIRECTORY)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--daemon', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        result = prepare(args.directory)
        print(json.dumps(dict(instructions=len(result['instructions']), instance_sha256=result['instance_sha256'], profile=result['profile'])))
    elif not args.daemon or daemonize(args.directory):
        supervise(args.directory)


if __name__ == '__main__':
    main()
