"""D199 TRAIN-only scale experiment; two map workers, at most two V4 children.

Run with UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python ...
All acceptance uses the pinned, unmodified full checker. No repair is called.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import ctypes
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = ROOT / 'experiments/mvp/strategy/fillsweep/runs/main/runtime'
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
           'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'HIGHS_THREADS',
           'BLIS_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS')
for name in THREADS:
    os.environ[name] = '1'
os.environ['STOWAGE_BALLAST_V4'] = '1'
ADMISSION_POLICY = dict(policy='D206', source='experiments/fleet/core-state.json',
                        min_recommended_cores=8, max_load1=45, max_age_s=120,
                        max_compute_workers=4, reserved_workers_per_map=2)


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.tmp-{os.getpid()}')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + '\n')
    tmp.replace(path)


def train_path(value):
    path = (ROOT / value).resolve()
    rel = path.relative_to(ROOT)
    if any(p in {'val', 'validation', 'test', 'test-v3', 'showcase', 'archive'} for p in rel.parts):
        raise ValueError(f'Forbidden partition: {rel}')
    allowed = [ROOT / 'experiments/mvp/strategy' / study / 'maps/train'
               for study in ('fillsweep', 'blockrule')]
    if not any(path.is_relative_to(base) for base in allowed) or path.suffix != '.json':
        raise ValueError(f'Not an allowlisted TRAIN input: {rel}')
    return path


def manifest():
    jobs = []
    sweep = read(ROOT / 'experiments/mvp/strategy/fillsweep/cohort.json')
    medium = read(ROOT / 'experiments/mvp/strategy/blockrule/cohort-medium.json')
    for source, ladder in ((sweep, True), (medium, False)):
        if source.get('split') != 'train':
            raise ValueError('Explicit TRAIN authority required')
        for original in source['maps']:
            row = dict(original)
            if ladder and (row['ship'] not in ('feeder_500', 'feeder_800')
                           or row['fill_target'] not in (60, 70, 80)):
                continue
            path = train_path(row.get('input', row.get('path')))
            expected = row.get('input_sha256', row.get('sha256'))
            if sha(path) != expected:
                raise ValueError(f'TRAIN hash mismatch: {path}')
            row.update(input=str(path.relative_to(ROOT)), input_sha256=expected,
                       stage=f"f{row['fill_target']}" if ladder else 'D185')
            if not row.get('map_id') or Path(row['map_id']).name != row['map_id']:
                raise ValueError('Invalid map ID')
            jobs.append(row)
    stages = ('f60', 'f70', 'f80', 'D185')
    def schedule(row):
        if row['stage'] == 'D185':
            return stages.index(row['stage']), row['seed'], '', row['ship'], row['map_id']
        base = 192_200_000 + 100 * ('feeder_500', 'feeder_800').index(row['ship'])
        replicate = row['seed'] - base
        if replicate not in (0, 1, 2):
            raise ValueError('Unexpected TRAIN ladder replicate')
        return stages.index(row['stage']), replicate, row['mass_level'], row['ship'], row['map_id']
    jobs.sort(key=schedule)
    if len(jobs) != 48 or len({r['map_id'] for r in jobs}) != 48:
        raise ValueError('Expected exactly 36 ladder + 12 D185 TRAIN maps')
    return dict(split='train', maps=jobs, stages=list(stages))


def freeze_base():
    target = HERE / 'runtime/base'
    if not (target / 'COMPLETE.json').exists():
        # Copy only the registered runtime files and recorded hull dependencies.
        # No dataset directory is copied or traversed.
        snap = read(BASE / 'snapshot.json')
        data = read(BASE / 'data-snapshot.json')
        sources = {**snap['sources'], **data['sources']}
        for relative, expected in sources.items():
            source = BASE / relative
            if sha(source) != expected:
                raise ValueError(f'Corrupt D192 runtime: {relative}')
            dest = target / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        for name in ('snapshot.json', 'data-snapshot.json'):
            shutil.copyfile(BASE / name, target / name)
        write(target / 'COMPLETE.json', dict(sources=sources, copied_at=now(),
                    origin=str(BASE.relative_to(ROOT)), source_snapshot_sha256=sha(BASE / 'snapshot.json')))
    record = read(target / 'COMPLETE.json')
    for relative, expected in record['sources'].items():
        if sha(target / relative) != expected:
            raise ValueError(f'Frozen base changed: {relative}')
    return target, record


def load_base(runtime):
    runtime = Path(runtime)
    helpers = runtime / 'experiments/mvp/strategy'
    sys.path[:0] = [str(helpers / 'blockrule'), str(helpers / 'headroom'),
                   str(helpers), str(runtime / 'src'), str(runtime)]
    study = importlib.import_module('study')
    sys.path[:0] = [str(HERE), str(runtime / 'src'), str(runtime)]
    study.ROOT = ROOT
    study.headroom.ROOT = ROOT
    import stowage.checker
    if not Path(stowage.checker.__file__).resolve().is_relative_to(runtime):
        raise ValueError('Checker did not load from frozen runtime')
    return study


def cpu_sample(interval=.5):
    """Darwin live CPU ticks: ps is denied in this workspace sandbox."""
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
    lib.mach_host_self.restype = ctypes.c_uint
    def ticks():
        a = (ctypes.c_uint * 4)()
        count = ctypes.c_uint(4)
        rc = lib.host_statistics(lib.mach_host_self(), 3, ctypes.byref(a), ctypes.byref(count))
        if rc:
            raise RuntimeError(f'host_statistics returned {rc}')
        return list(a)
    a = ticks()
    time.sleep(interval)
    b = ticks()
    delta = [(y-x) % 2**32 for x, y in zip(a, b)]
    cores = os.cpu_count() or 1
    return dict(at=now(), hardware_cores=cores, ticks=delta,
                busy_cores=cores*(sum(delta)-delta[2])/max(1, sum(delta)),
                loadavg=os.getloadavg(), ps='sandbox denied; no process scan')


def ledger(cores, status):
    fleet = ROOT / 'experiments/fleet'
    with (fleet / 'ledger.md.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = fleet / 'ledger.md'
        lines = [line for line in path.read_text().splitlines(keepends=True)
                 if not line.startswith('| scale-solver/run |')]
        row = (f'| scale-solver/run | D199 TRAIN scale; {status}; two map workers + at most two owned V4 children; native1 | '
               f'{cores} | {8 if cores else 0} | no | 48-map ceiling ~12h; live CPU admission; owner {os.getpid()} | {now()} |\n')
        index = next(i for i, line in enumerate(lines) if line.startswith('|---|')) + 1
        lines.insert(index, row)
        path.write_text(''.join(lines))
    write(HERE / 'ledger.json', dict(at=now(), cores=cores, status=status,
           pid=os.getpid(), native_threads=1, max_compute_workers=4,
           estimate='48 maps, <=300s solve + <=1200s finisher each; ~12h ceiling at2 maps plus checks'))


def code_hashes():
    return {p.name: sha(p) for p in sorted(HERE.glob('*.py')) if not p.name.startswith('test_')}


def check_code(expected):
    for name, value in expected.items():
        if sha(HERE / name) != value:
            raise RuntimeError(f'Experiment implementation changed during frozen run: {name}')


def diagnostic_workers(control=None):
    control = Path(control) if control else HERE / 'control.json'
    return len(read(control).get('owned_diagnostic_pids', [])) if control.exists() else 0


def _finite_number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


def _core_errors(state, observed_at):
    if not isinstance(state, dict):
        return ['core-state must be a JSON object']
    errors = []
    try:
        json.dumps(state, allow_nan=False)
    except (ValueError, TypeError):
        errors.append('core-state contains nonfinite or invalid JSON values')
    mask, load, timestamp = (state.get(key) for key in ('mask', 'load1', 't'))
    if type(mask) is not int or mask < 0:
        errors.append('mask must be a nonnegative integer')
    elif mask.bit_count() < ADMISSION_POLICY['min_recommended_cores']:
        errors.append('mask has fewer than eight recommended cores')
    if not _finite_number(load) or not 0 <= load <= ADMISSION_POLICY['max_load1']:
        errors.append('load1 must be finite, nonnegative and at most 45')
    if not _finite_number(timestamp) or timestamp < 0 or not _finite_number(observed_at):
        errors.append('timestamps must be finite nonnegative UNIX times')
    elif observed_at < 0 or not 0 <= observed_at-timestamp < ADMISSION_POLICY['max_age_s']:
        errors.append('core-state must be nonfuture and younger than 120 seconds')
    return errors


def core_admission():
    """Read the leader file once, retaining the exact bytes used for D206."""
    path = (ROOT / ADMISSION_POLICY['source']).resolve()
    row = dict(policy=dict(ADMISSION_POLICY), source_path=str(path), source_text=None,
               source_sha256=None, raw=None, allowed=False, errors=[])
    try:
        raw = path.read_bytes()
        row.update(source_text=raw.decode('utf-8'), source_sha256=hashlib.sha256(raw).hexdigest())
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f'duplicate core-state key: {key}')
                result[key] = value
            return result
        def reject_constant(value):
            raise ValueError(f'invalid core-state JSON constant: {value}')
        row['raw'] = json.loads(row['source_text'], object_pairs_hook=unique,
                                parse_constant=reject_constant)
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        row['errors'].append(f'{type(error).__name__}: {error}')
    observed = time.time()
    row.update(observed_at=observed, observed_utc=datetime.fromtimestamp(observed, timezone.utc).isoformat())
    row['errors'].extend(_core_errors(row['raw'], observed))
    state = row['raw'] if isinstance(row['raw'], dict) else {}
    mask, timestamp = state.get('mask'), state.get('t')
    row.update(core_count=mask.bit_count() if type(mask) is int and mask >= 0 else None,
               age_s=observed-timestamp if _finite_number(timestamp) else None)
    row['allowed'] = not row['errors']
    return row


def map_admission(*, active_maps, max_map_workers, control=None):
    """A new leader-file reading and worker reservation for EACH submission."""
    row = core_admission()
    workers = dict(active_maps=active_maps, max_map_workers=max_map_workers,
                   diagnostic_workers=None, active_map_limit=max_map_workers)
    row['workers'] = workers
    try:
        controls = read(control) if control is not None and Path(control).exists() else {}
        if not isinstance(controls, dict):
            raise ValueError('map control must be an object')
        pids = controls.get('owned_diagnostic_pids', [])
        limit = controls.get('active_map_limit', max_map_workers)
        if (not isinstance(pids, list) or any(type(pid) is not int or pid <= 0 for pid in pids)
                or type(limit) is not int or limit < 0):
            raise ValueError('invalid diagnostic PID list or active map limit')
        workers.update(diagnostic_workers=len(pids), active_map_limit=limit,
                       owned_diagnostic_pids=pids)
        if controls.get('stop_after_active') is True:
            row['errors'].append('stop_after_active requested')
        if (type(active_maps) is not int or active_maps < 0 or type(max_map_workers) is not int
                or not 1 <= max_map_workers <= 2):
            row['errors'].append('invalid active map count or declared map worker cap')
        elif active_maps >= min(max_map_workers, limit):
            row['errors'].append('map worker cap reached')
        elif 2*(active_maps+1)+len(pids) > ADMISSION_POLICY['max_compute_workers']:
            row['errors'].append('four-worker cap reached including owned diagnostics and map children')
    except (OSError, ValueError, TypeError) as error:
        row['errors'].append(f'map control read failed: {type(error).__name__}: {error}')
    row['allowed'] = not row['errors']
    return row


def submit_admitted_map(pool, job, out, runtime, config, implementation, *, active_maps, control=None):
    """Persist a source-bound admission before the executor can start its worker."""
    evidence = map_admission(active_maps=active_maps, max_map_workers=config['max_map_workers'],
                             control=control)
    if not evidence['allowed']:
        return None, evidence
    map_id = job['map_id']
    if Path(map_id).name != map_id or map_id in ('', '.', '..'):
        raise ValueError('Invalid map identity for admission artifact')
    evidence.update(map_id=map_id, admitted_at=evidence['observed_at'],
                    admitted_utc=evidence['observed_utc'], controller_pid=os.getpid())
    path = Path(out) / 'maps' / map_id / 'admissions' / f'{time.time_ns()}-{os.getpid()}.json'
    write(path, evidence)
    binding = dict(path=str(path.relative_to(Path(out))), sha256=sha(path))
    future = pool.submit(run_map, job, str(out), str(runtime), config, implementation, binding)
    return future, evidence


def worker_admission(job, out, binding):
    """Validate the original dispatch observation and report its current age.

    Fresh phase admission after setup authorizes each timed call separately;
    an aged dispatch record never authorizes planning or finishing itself.
    """
    if not isinstance(binding, dict):
        raise ValueError('Timed map lacks D206 admission evidence')
    folder = (Path(out) / 'maps' / job['map_id'] / 'admissions').resolve()
    path = (Path(out) / binding['path']).resolve()
    if path.parent != folder or sha(path) != binding['sha256']:
        raise ValueError('D206 admission artifact path or SHA256 differs')
    evidence = read(path)
    observed = time.time()
    if (evidence.get('allowed') is not True or evidence.get('errors')
            or evidence.get('map_id') != job['map_id'] or evidence.get('policy') != ADMISSION_POLICY
            or evidence.get('source_path') != str((ROOT / ADMISSION_POLICY['source']).resolve())
            or evidence.get('admitted_at') != evidence.get('observed_at')
            or evidence.get('admitted_utc') != evidence.get('observed_utc')
            or hashlib.sha256(evidence['source_text'].encode('utf-8')).hexdigest() != evidence['source_sha256']
            or json.loads(evidence['source_text']) != evidence['raw']
            or _core_errors(evidence['raw'], evidence['observed_at'])):
        raise ValueError('D206 admission evidence is invalid')
    return dict(artifact=binding, evidence=evidence, worker_checked_at=observed,
                worker_checked_utc=datetime.fromtimestamp(observed, timezone.utc).isoformat(),
                source_age_at_worker_start_s=observed-evidence['raw']['t'])


def run_admitted_stage(job, out, stage, reservation, call, *, record=None, poll_s=10):
    """Wait outside stage budgets; freshly authorize each time-limited call."""
    if stage not in ('planner', 'finisher'):
        raise ValueError('Unknown time-limited stage')
    folder = Path(out) / 'maps' / job['map_id']
    waiting_since = time.monotonic()
    attempts = 0
    while True:
        evidence = core_admission()
        attempts += 1
        wait_seconds = time.monotonic()-waiting_since
        telemetry = dict(stage=stage, attempts=attempts, wait_seconds=wait_seconds,
                         latest_evidence=evidence, reservation=reservation['artifact'])
        write(folder / 'admission-waits' / f'{stage}.json', telemetry)
        if evidence['allowed']:
            evidence.update(map_id=job['map_id'], stage=stage,
                            admitted_at=evidence['observed_at'], admitted_utc=evidence['observed_utc'],
                            map_reservation_artifact=reservation['artifact'],
                            reserved_workers=reservation['evidence']['workers'])
            path = folder / 'admissions' / f'{stage}-{time.time_ns()}-{os.getpid()}.json'
            write(path, evidence)
            admission = dict(artifact=dict(path=str(path.relative_to(Path(out))), sha256=sha(path)),
                             evidence=evidence, wait_seconds=time.monotonic()-waiting_since,
                             attempts=attempts)
            if record is not None:
                record(admission)
            # Persistence/summary writes or scheduling can themselves consume
            # the final freshness margin. Re-read rather than launch on expiry.
            admission['start_checked_at'] = time.time()
            if _core_errors(evidence['raw'], admission['start_checked_at']):
                telemetry['start_recheck_errors'] = ['admission expired before timed call']
                write(folder / 'admission-waits' / f'{stage}.json', telemetry)
                time.sleep(poll_s)
                continue
            before = time.monotonic()
            result = call()
            return result, time.monotonic()-before, admission
        time.sleep(poll_s)


def run_map(job, out, runtime, config, implementation, admission_binding=None):
    folder = Path(out) / 'maps' / job['map_id']
    target = folder / 'summary.json'
    if target.exists() and read(target).get('complete'):
        return read(target)
    admission = worker_admission(job, out, admission_binding)
    check_code(implementation)
    study = load_base(runtime)
    packing = importlib.import_module(config.get('packing_module', 'packing'))
    finisher = importlib.import_module(config.get('finisher_module', 'finisher'))
    import reservations
    from stowage.checker import check
    from stowage.feasibility import FeasibilityConfig
    from stowage.io import load_instance
    from stowage.planner import finalize
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.solver.ballast_deadline import shutdown_worker
    from stowage.solver.twostep import TwoStepPlanner
    folder.mkdir(parents=True, exist_ok=True)
    if target.exists():
        archive = folder / 'interruptions' / str(time.time_ns())
        archive.mkdir(parents=True)
        for prior in folder.glob('*.json'):
            shutil.copyfile(prior, archive / prior.name)
        write(archive / 'lineage.json', dict(resumed_at=now(),
              policy='Interrupted uncommitted map attempt retained; committed complete maps never rerun'))
    write(folder / 'worker.json', dict(pid=os.getpid(), started_at=now()))
    row = dict(job=job, complete=False, passed=False, timings={}, pid=os.getpid(), admission=admission)
    def state(stage):
        row.update(stage=stage, updated_at=now())
        write(target, row)
    stop = threading.Event()
    def inventory():
        while not stop.wait(.5):
            for child in mp.active_children():
                if child.pid:
                    write(folder / 'owned' / f'{child.pid}.json', dict(pid=child.pid,
                          parent_pid=os.getpid(), role='owned V4 child', observed_at=now()))
    monitor = threading.Thread(target=inventory, daemon=True)
    monitor.start()
    started = time.monotonic()
    packing_handle = None
    restore_reservations = None
    restore_finisher = None
    restore_terminal = None
    restore_greedy = None
    restore_oracle = None
    restore_allocation = None
    try:
        path = train_path(job['input'])
        if sha(path) != job['input_sha256']:
            raise ValueError('TRAIN input changed after freeze')
        instance = load_instance(path)
        with rule_freeze_v1(enabled=False):
            if config.get('allocation_module'):
                restore_allocation = importlib.import_module(config['allocation_module']).install()
            if config.get('oracle_module'):
                restore_oracle = importlib.import_module(config['oracle_module']).install()
            packing_handle = packing.install()
            restore_reservations = reservations.install()
            restore_finisher = finisher.install()
            if config.get('terminal_module'):
                restore_terminal = importlib.import_module(config['terminal_module']).install()
            if config.get('greedy_module'):
                restore_greedy = importlib.import_module(config['greedy_module']).install()
            # All plugins steer search or reuse identical computations only.
            # Model/native initialization can age the dispatch snapshot. Neither
            # setup nor a later finisher may reuse it as timed-stage admission.
            planner = TwoStepPlanner(feasibility_config=FeasibilityConfig(extra={'global_screen': 0}))
            def stage_admitted(stage, admission):
                row.setdefault('stage_admissions', {})[stage] = admission
                row.setdefault('admission_wait_seconds', {})[stage] = admission['wait_seconds']
                state('construction' if stage == 'planner' else 'finisher')
            state('planner_admission_wait')
            result, planner_seconds, _ = run_admitted_stage(job, out, 'planner', admission,
                lambda: planner.plan(instance, seed=0, time_limit_s=config['planner_seconds']),
                record=lambda evidence: stage_admitted('planner', evidence))
            # Restore planner allocation and search oracle before finishing/checking.
            if callable(restore_allocation):
                restore_allocation()
                restore_allocation = None
            row['allocation_restored_before_finisher'] = True
            if callable(restore_oracle):
                restore_oracle()
                restore_oracle = None
            row['oracle_restored_before_finisher'] = True
            row['full_checker_unmodified'] = True
            row['timings']['planner_seconds'] = planner_seconds
            row.update(solver_status=result.status, message=result.message, metrics=result.metrics)
            write(folder / 'raw.json', result.model_dump(mode='json'))
            if result.status == 'ok' and result.plan is not None:
                state('finisher_admission_wait')
                os.environ['SCALE_FINISHER_PROGRESS'] = str(folder / 'finisher-progress.json')
                # Capture complete diagnostics which planner.finalize omits.
                import stowage.solver.ballast as ballast
                original = ballast.finish_ballast
                def capture(*args, **kwargs):
                    outcome = original(*args, **kwargs)
                    write(folder / 'ballast-outcome.json', outcome.model_dump(mode='json'))
                    return outcome
                ballast.finish_ballast = capture
                try:
                    result, finisher_seconds, _ = run_admitted_stage(job, out, 'finisher', admission,
                        lambda: finalize(instance, result, time_limit_s=config['finisher_seconds']),
                        record=lambda evidence: stage_admitted('finisher', evidence))
                finally:
                    ballast.finish_ballast = original
                row['timings']['finisher_seconds'] = finisher_seconds
                write(folder / 'finished.json', result.model_dump(mode='json'))
                row.update(finalize_status=result.status, message=result.message,
                           final_metrics=result.metrics)
                # finisher's exact caches are worker-scoped. This independent
                # full checker call is run in the map process after it returns.
                if result.status == 'ok' or config.get('check_failed_finishers', False):
                    state('full_checker')
                    before = time.monotonic()
                    report = check(instance, result.plan)
                    row['timings']['checker_seconds'] = time.monotonic()-before
                    write(folder / 'report.json', report.model_dump(mode='json'))
                    row.update(checker_attempted=True, checker_passed=report.passed is True,
                               rules=sorted({v.rule_id for v in report.violations}),
                               passed=result.status == 'ok' and report.passed is True,
                               finished_sha256=sha(folder / 'finished.json'),
                               report_sha256=sha(folder / 'report.json'))
                    if row['passed']:
                        row.update(moves=report.count.weighted_nonproductive,
                                   count=report.count.model_dump(mode='json'))
                else:
                    row.update(checker_attempted=False, checker_not_reached='finisher did not return a successful plan')
            row.update(complete=True)
    except Exception as error:
        row.update(error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
    finally:
        if callable(restore_allocation):
            restore_allocation()
            restore_allocation = None
        if callable(restore_oracle):
            restore_oracle()
            restore_oracle = None
        shutdown_worker()
        if packing_handle is not None:
            packing_handle.restore()
        if restore_reservations is not None:
            restore_reservations()
        if callable(restore_finisher):
            restore_finisher()
        if callable(restore_terminal):
            restore_terminal()
        if callable(restore_greedy):
            restore_greedy()
        stop.set()
        monitor.join(timeout=2)
        row['timings']['total_seconds'] = time.monotonic()-started
        state('complete' if row['complete'] else 'harness_error')
    return row


def refresh(out):
    # Report generation performs JSON reads only, without planner imports.
    subprocess.run([sys.executable, str(HERE / 'report.py'), '--out', str(out)],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=HERE / 'runs/main')
    parser.add_argument('--workers', type=int, choices=(1, 2), default=2)
    parser.add_argument('--planner-seconds', type=float, default=300)
    parser.add_argument('--finisher-seconds', type=float, default=1200)
    parser.add_argument('--stage', choices=('all', 'f60', 'f70', 'f80', 'D185'), default='all')
    parser.add_argument('--packing-module', choices=('packing', 'packing_v3', 'carrier', 'carrier_v4', 'carrier_v5', 'carrier_v6'), default='packing')
    parser.add_argument('--finisher-module', choices=('finisher', 'finisher_v2', 'finisher_v3'), default='finisher')
    parser.add_argument('--admission-file', type=Path, default=HERE / 'control.json')
    parser.add_argument('--check-failed-finishers', action='store_true',
                        help='Optional costly diagnostic full check after a failed finisher; cannot create a pass')
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args(argv)
    if args.planner_seconds <= 0 or args.finisher_seconds <= 0:
        parser.error('Stage budgets must be positive')
    out = args.out.resolve()
    if not out.is_relative_to(HERE / 'runs'):
        parser.error('Output must be under scale/runs')
    out.mkdir(parents=True, exist_ok=True)
    if args.detach:
        command = [sys.executable, str(Path(__file__).resolve()),
                   *[x for x in sys.argv[1:] if x != '--detach']]
        with (out / 'controller.log').open('a') as log:
            child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        write(out / 'launch.json', dict(pid=child.pid, command=command, at=now()))
        print(json.dumps(dict(pid=child.pid, out=str(out))))
        return
    runtime, base = freeze_base()
    cohort = manifest()
    if args.prepare_only:
        write(HERE / 'cohort.json', cohort)
        print(json.dumps(dict(maps=len(cohort['maps']), runtime=str(runtime))))
        return
    config = dict(planner_seconds=args.planner_seconds, finisher_seconds=args.finisher_seconds,
                  seed=0, feasibility_extra={'global_screen': 0}, rule_freeze_v1=False,
                  no_repair=True, max_map_workers=args.workers, native_threads={k: '1' for k in THREADS},
                  packing_module=args.packing_module, finisher_module='finisher_v3' if args.packing_module == 'carrier_v6' else args.finisher_module,
                  terminal_module='terminal_v4' if args.packing_module in ('carrier_v4', 'carrier_v5', 'carrier_v6') else None,
                  greedy_module='greedy_v5' if args.packing_module in ('carrier_v5', 'carrier_v6') else None,
                  oracle_module='oracle_v5' if args.packing_module in ('carrier_v5', 'carrier_v6') else None,
                  allocation_module='allocation_v6' if args.packing_module == 'carrier_v6' else None,
                  full_checker_unmodified=True,
                  admission_policy=dict(ADMISSION_POLICY),
                  check_failed_finishers=args.check_failed_finishers)
    implementation = code_hashes()
    record = dict(config=config, cohort=cohort, implementation=implementation,
                  base_snapshot_sha256=sha(runtime / 'COMPLETE.json'), split='train')
    lock = (out / 'controller.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if (out / 'run.json').exists() and read(out / 'run.json') != record:
        raise RuntimeError('Run configuration or implementation changed; use new output')
    write(out / 'run.json', record)
    write(out / 'controller.json', dict(pid=os.getpid(), started_at=now(), command=sys.argv))
    ledger(diagnostic_workers(args.admission_file), 'queued; tests passed; live admission pending; recorded diagnostics retained')
    errors = []
    begin = time.monotonic()
    stopping = False
    try:
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context('spawn')) as pool:
            for stage in cohort['stages']:
                if args.stage != 'all' and stage != args.stage:
                    continue
                pending = [j for j in cohort['maps'] if j['stage'] == stage
                           and not ((out / 'maps' / j['map_id'] / 'summary.json').exists()
                           and read(out / 'maps' / j['map_id'] / 'summary.json').get('complete'))]
                futures = {}
                while pending or futures:
                    try:
                        sample = cpu_sample()
                    except Exception as error:
                        sample = dict(error=f'{type(error).__name__}: {error}')
                    # Hardware-idle arithmetic is telemetry only. D206 plus
                    # the map/owned-child cap authorize each individual submit.
                    extra = diagnostic_workers(args.admission_file)
                    admit = min(args.workers, max(0, (4-extra)//2))
                    control = args.admission_file
                    if control.exists():
                        controls = read(control)
                        admit = min(admit, int(controls.get('active_map_limit', 0)))
                        if controls.get('stop_after_active') is True:
                            stopping = True
                            pending = []
                            admit = 0
                    ledger(extra + 2*max(len(futures), admit if pending else 0),
                           'active including recorded diagnostic workers' if futures else 'queued; awaiting fresh D206 admission')
                    admission = None
                    while pending and len(futures) < admit:
                        job = pending[0]
                        future, admission = submit_admitted_map(pool, job, out, runtime, config,
                            implementation, active_maps=len(futures), control=args.admission_file)
                        if future is None:
                            break
                        pending.pop(0)
                        futures[future] = job
                    write(out / 'status.json', dict(stage=stage, pending=len(pending), active=len(futures),
                          admission=admission, cpu_telemetry=sample,
                          active_limit=admit if admission is None or admission['allowed'] else len(futures),
                          elapsed_s=time.monotonic()-begin,
                          max_compute_workers=4, owned_diagnostic_workers=extra, updated_at=now()))
                    refresh(out)
                    if not futures:
                        time.sleep(10)
                        continue
                    done, _ = wait(futures, timeout=15, return_when=FIRST_COMPLETED)
                    for future in done:
                        job = futures.pop(future)
                        try:
                            result = future.result()
                            if not result.get('complete'):
                                errors.append(dict(map_id=job['map_id'], error=result.get('error', 'incomplete')))
                            print(json.dumps(dict(map_id=job['map_id'], passed=result.get('passed'),
                                                  error=result.get('error'))), flush=True)
                        except Exception as error:
                            errors.append(dict(map_id=job['map_id'], error=f'{type(error).__name__}: {error}'))
                    if errors:
                        raise RuntimeError('Harness errors retained; refusing to advance ladder')
                if stopping:
                    break
                write(out / f'{stage}-COMPLETE.json', dict(stage=stage, ended_at=now()))
        write(out / ('stopped.json' if stopping else 'completion.json'),
              dict(errors=errors, ended_at=now(), elapsed_s=time.monotonic()-begin,
                   status='stopped_after_active; incomplete maps remain pending' if stopping else 'complete'))
    finally:
        ledger(diagnostic_workers(args.admission_file), 'primary idle; recorded diagnostic workers retained until exit')
        refresh(out)


if __name__ == '__main__':
    main()
