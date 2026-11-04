"""Optional controller for the unchanged run.py workers; never start beside serial.

One worker while the leader benchmark sentinel exists, otherwise at most two.
All 117 jobs are queued once; workers retain the existing cache/config identity.
An interrupted controller can be restarted: finished rows are reused by run.py.
"""
from __future__ import annotations

import argparse
from collections import deque
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
from time import monotonic, sleep, time

from timeout_row import CAP_S, declare_policy, record_timeout

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT, LAB = HERE.parents[5], HERE.parents[1]
SENTINEL = LAB / 'c1/leader/BENCH-RUNNING'
ARMS = ('frontier09', 'engine', 'graph')


class CleanupBlocked(RuntimeError):
    pass


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(payload, indent=2) + '\n')
    temporary.replace(path)


def _descendant_snapshot(output, *, worker_pid, launch_epoch, job_argv):
    """Probe only exact native PIDs correlated with this registered Popen job.

    Absence of an unregistered startup process cannot be established by a
    ledger. The native worker logs before numerical work; normal owned-process
    shutdown/session interruption remains necessary in addition to this guard.
    """
    output = Path(output)
    registrations, errors = [], []
    for path in sorted((output / 'pids').glob('native-*.json')):
        try:
            data = path.read_bytes()
            row = json.loads(data)
            if (type(row['pid']) is not int or row['pid'] <= 0
                    or type(row['parent_pid']) is not int or row['parent_pid'] <= 0
                    or type(row['started']) not in (int, float)
                    or not math.isfinite(row['started'])):
                raise ValueError('invalid native PID registration')
            registrations.append(dict(row, record=str(path), record_sha256=hashlib.sha256(data).hexdigest()))
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(dict(record=str(path), error=str(error)))
    owned, selected = {worker_pid}, {}
    while True:
        previous = len(owned)
        for row in registrations:
            if row['parent_pid'] not in owned or row['started'] < launch_epoch:
                continue
            if row.get('argv') != job_argv:
                error = dict(record=row['record'], error='owned parent/time but unexpected job argv')
                if error not in errors:
                    errors.append(error)
                continue
            owned.add(row['pid'])
            selected[row['pid']] = row
        if len(owned) == previous:
            break
    probes = []
    for pid in sorted(selected):
        try:
            os.kill(pid, 0)  # Exact PID from the correlated native registration, never a process scan.
            probes.append(dict(pid=pid, state='alive'))
        except ProcessLookupError:
            probes.append(dict(pid=pid, state='absent'))
        except OSError as error:
            probes.append(dict(pid=pid, state='unknown', error=str(error)))
    audit = dict(worker_pid=worker_pid, launch_epoch=launch_epoch, job_argv=job_argv,
                 checked=time(), registrations=[selected[p] for p in sorted(selected)],
                 probes=probes, registration_errors=errors,
                 all_registered_descendants_absent=not errors and all(p['state'] == 'absent' for p in probes),
                 limitation='unregistered startup races require normal owned-worker/session cleanup')
    return audit


def check_registered_descendants(output, *, worker_pid, launch_epoch, job_argv,
                                 grace_s=360., on_wait=None):
    """Allow registered native processes to finish exit cleanup; never launch work.

    Every snapshot, including initial live/unknown evidence, is retained. Bad
    registrations fail immediately. Only existing worker-deadline servicing may
    run during the grace period; this guard introduces no process signals.
    """
    if not math.isfinite(grace_s) or grace_s < 0:
        raise ValueError('cleanup grace must be finite and nonnegative')
    output, began, iterations = Path(output), monotonic(), []
    target = output / 'cleanup-audits' / f'worker-{worker_pid}.json'
    while True:
        audit = _descendant_snapshot(output, worker_pid=worker_pid,
                                     launch_epoch=launch_epoch, job_argv=job_argv)
        iterations.append(audit)
        elapsed = max(0., monotonic() - began)
        outcome = ('registration_error' if audit['registration_errors'] else
                   'absent' if audit['all_registered_descendants_absent'] else
                   'grace_expired' if elapsed >= grace_s else 'waiting')
        result = dict(audit, grace_s=grace_s, grace_elapsed_s=elapsed, cleanup_outcome=outcome,
                      initial_evidence=iterations[0], iterations=list(iterations))
        _atomic_json(target, result)
        _atomic_json(output / 'cleanup-audit.json', result)
        if outcome == 'absent':
            return result
        if outcome in ('registration_error', 'grace_expired'):
            raise CleanupBlocked(f'Native cleanup not confirmed for worker {worker_pid}; no new jobs. '
                                 f'Interrupt only the owned recorded controller session; inspect {target}')
        if on_wait is not None:
            on_wait()
        sleep(min(1., max(0., grace_s - (monotonic() - began))))


def service_worker_deadlines(active, *, record, signal_child):
    """Service existing direct-worker caps, including while another job drains."""
    for pid in list(active):
        child, row, arm, reason, began = active[pid]
        if reason is None and child.poll() is None and monotonic() - began >= CAP_S:
            active[pid] = (child, row, arm, 'timeout', began)
            record(child, row, arm, deadline_reached=time(), cap_s=CAP_S)
            signal_child(child, row, arm)


def service_draining_limits(active, *, sentinel_exists, record, signal_child):
    """A draining native worker reserves one busy slot while it exits."""
    service_worker_deadlines(active, record=record, signal_child=signal_child)
    if sentinel_exists():
        for pid in list(active):
            child, row, arm, reason, began = active[pid]
            if reason is None and child.poll() is None:
                active[pid] = (child, row, arm, 'sentinel', began)
                record(child, row, arm, interrupted_for_sentinel=True, draining_slot_reserved=True)
                signal_child(child, row, arm)


def read_complete_row(output, entry, arm):
    target = Path(output) / arm / f'{entry["instance_id"]}.json'
    try:
        row = json.loads(target.read_text())
        if (row.get('complete') is not True or type(row.get('passed')) is not bool
                or (row.get('instance_id'), row.get('input'), row.get('input_sha256'), row.get('size'),
                    row.get('ports'), row.get('arm')) != (entry['instance_id'], entry['path'], entry['sha256'],
                                                        entry['size'], entry['ports'], arm)
                or not isinstance(row.get('run_key'), str) or len(row['run_key']) != 64
                or not isinstance(row.get('config'), dict)):
            raise ValueError('incomplete or mismatched worker result')
        int(row['run_key'], 16)
        if (row['config'].get('input'), row['config'].get('size'), row['config'].get('ports')) != (
                entry['path'], entry['size'], entry['ports']):
            raise ValueError('result configuration has a mismatched input stratum')
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError(f'Worker exited without a valid complete target row: {target}: {error}') from error
    return row


def alive(pid):
    try:
        os.kill(pid, 0)  # Probe only the exact PID recorded by our previous driver.
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='results/main')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    output = (HERE / args.out).resolve()
    output.relative_to(HERE)
    maps = json.loads((HERE / 'cohort-manifest.json').read_text())['maps']
    if len(maps) != 39 or len({r['instance_id'] for r in maps}) != 39:
        raise ValueError('requires the complete frozen 39-map cohort')
    queue = deque((row, arm) for row in maps for arm in ARMS)
    if args.dry_run:
        print(json.dumps(dict(jobs=len(queue), workers=1 if SENTINEL.exists() else 2,
                              output=str(output), worker='unchanged run.py --worker')))
        return
    output.mkdir(parents=True, exist_ok=True)
    lock = (output / 'parallel-controller.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for filename in ('process.json', 'active-child.json'):
        path = output / filename
        if path.exists() and alive(json.loads(path.read_text())['pid']):
            raise RuntimeError(f'{filename} records a live PID; stop and reap the serial driver first')
    for path in (output / 'pids').glob('parallel-*.json'):
        previous = json.loads(path.read_text())
        if 'returncode' not in previous and alive(previous['pid']):
            raise RuntimeError(f'previous parallel worker {previous["pid"]} is still alive')
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
        os.environ[key] = '1'
    sys.path.insert(0, str(LAB))
    import score

    declare_policy(output)
    summarize = score.import_plugin(score.plugin_info(f'{HERE / "report.py"}:summarize'))

    active, registrations, stopping, completed, controller_error = {}, {}, False, 0, None
    def interrupt(signum, frame):
        nonlocal stopping
        stopping = True  # Finish registering Popen before signalling registered children.
    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    def record(child, row, arm, **state):
        path = output / 'pids' / f'parallel-{child.pid}.json'
        payload = json.loads(path.read_text()) if path.exists() else {}
        payload.update(pid=child.pid, parent_pid=os.getpid(), arm=arm,
                       input=row['path'], instance_id=row['instance_id'], **state)
        score.write_json(path, payload)
    def signal_child(child, row, arm):
        nonlocal stopping
        if child.poll() is not None:
            return
        try:
            child.send_signal(signal.SIGINT)
        except PermissionError as error:
            stopping = True
            record(child, row, arm, signal_error=str(error), controller_session_interrupt_required=True)
            print(f'SIGINT refused for registered worker {child.pid}: {error}. '
                  'Interrupt this controller\'s own recorded session; no timeout row until worker is reaped.',
                  file=sys.stderr, flush=True)
    def deadlines():
        if not stopping:
            service_worker_deadlines(active, record=record, signal_child=signal_child)
    def during_drain():
        if not stopping:
            service_draining_limits(active, sentinel_exists=SENTINEL.exists,
                                    record=record, signal_child=signal_child)
    def guard(child):
        return check_registered_descendants(output, worker_pid=child.pid,
                                           on_wait=during_drain, **registrations[child.pid])
    score.write_json(output / 'parallel-process.json', dict(pid=os.getpid(), started=time(),
        sentinel=str(SENTINEL), maximum_workers=2, worker_kernel=str(HERE / 'run.py')))
    try:
        while (queue or active) and not stopping:
            deadlines()
            slots = 1 if SENTINEL.exists() else 2
            if len(active) > slots and not any(job[3] for job in active.values()):
                pid = next(reversed(active))
                child, row, arm, _, began = active[pid]
                active[pid] = (child, row, arm, 'sentinel', began)
                signal_child(child, row, arm)
                record(child, row, arm, interrupted_for_sentinel=True)
            for pid in list(active):
                if pid not in active:
                    continue
                child, row, arm, reason, began = active[pid]
                status = child.poll()
                if status is None:
                    continue
                del active[pid]
                record(child, row, arm, finished=time(), returncode=status, reason=reason)
                try:
                    guard(child)
                except CleanupBlocked:
                    queue.appendleft((row, arm))
                    raise
                if reason == 'sentinel':
                    queue.appendleft((row, arm))
                elif reason == 'timeout':
                    record_timeout(row, arm, args.out)
                    read_complete_row(output, row, arm)
                    completed += 1
                elif status:
                    raise RuntimeError(f'registered worker {pid} exited {status}: {arm} {row["path"]}')
                else:
                    read_complete_row(output, row, arm)
                    completed += 1
                if reason != 'sentinel':
                    summarize(output)
            while queue and len(active) < slots and not stopping:
                # Recheck immediately before every launch; the sentinel may change.
                if len(active) >= (1 if SENTINEL.exists() else 2):
                    break
                row, arm = queue.popleft()
                command = [sys.executable, str(HERE / 'run.py'), '--worker', '--arm', arm,
                           '--input', row['path'], '--size', row['size'], '--ports', str(row['ports']),
                           '--out', args.out]
                launch_epoch, began = time(), monotonic()
                child = subprocess.Popen(command, cwd=ROOT,
                    env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
                active[child.pid] = (child, row, arm, None, began)
                registrations[child.pid] = dict(launch_epoch=launch_epoch, job_argv=command[1:])
                record(child, row, arm, started=launch_epoch, command=command, cap_s=CAP_S)
            if active:
                sleep(1.)
    except BaseException as error:
        stopping = True
        controller_error = f'{type(error).__name__}: {error}'
        raise
    finally:
        for child, row, arm, _, _ in active.values():
            signal_child(child, row, arm)
        cleanup_errors = []
        for pid, (child, row, arm, reason, _) in list(active.items()):
            status = child.wait()
            record(child, row, arm, finished=time(), returncode=status, controller_interrupted=True)
            del active[pid]
            try:
                guard(child)
            except CleanupBlocked as error:
                cleanup_errors.append(str(error))
                queue.appendleft((row, arm))
                continue
            if reason == 'timeout':
                record_timeout(row, arm, args.out)
                read_complete_row(output, row, arm)
                completed += 1
                summarize(output)
            else:
                queue.appendleft((row, arm))
        score.write_json(output / 'parallel-state.json', dict(pid=os.getpid(), finished=time(),
            interrupted=stopping, completed_jobs=completed, pending_jobs=len(queue),
            controller_error=controller_error, cleanup_errors=cleanup_errors))
        if cleanup_errors:
            raise CleanupBlocked('; '.join(cleanup_errors))
    if stopping:
        lock.close()
        raise SystemExit(130)
    try:
        summarize(output)
        score.write_json(output / 'parallel-complete.json', dict(finished=time(), jobs=completed, maps=39))
    finally:
        lock.close()


if __name__ == '__main__':
    main()
