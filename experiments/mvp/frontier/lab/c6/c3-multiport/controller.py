"""Owned manifest-only workers, uniform cap, exact registered-descendant cleanup.

The cleanup guard is copied locally from the C3 controller; no old lab runtime
is imported. Cleanup is outside the censored whole-map work clock.
"""
from __future__ import annotations
import argparse
from collections import deque
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
from time import monotonic, sleep, time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
SENTINEL = ROOT/'experiments/mvp/frontier/lab/c1/leader/BENCH-RUNNING'
CAP_S = 600.

def local_report():
    name = '_c6_multiport_report'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE/'report.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]

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
    if target.exists():
        previous = target.read_bytes()
        archive = target.parent/'history'/f'worker-{worker_pid}-{hashlib.sha256(previous).hexdigest()}.json'
        archive.parent.mkdir(parents=True,exist_ok=True)
        if not archive.exists():
            archive.write_bytes(previous)
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


def service_limits(active, *, record, signal_child, sentinel_exists, draining=False):
    """Service caps during ordinary polling and native cleanup, without launch."""
    for pid in list(active):
        job = active[pid]
        if job['reason'] is None and job['child'].poll() is None and monotonic()-job['began'] >= CAP_S:
            job['reason'] = 'timeout'
            record(job, deadline_reached=time(), cap_s=CAP_S)
            signal_child(job)
    slots = 0 if draining and sentinel_exists() else 1 if sentinel_exists() else 2
    running = [j for j in active.values() if j['child'].poll() is None]
    # Interrupted children still reserve slots until reaped and drained.
    unmarked = [j for j in running if j['reason'] is None]
    reserved = len(running)-len(unmarked)
    excess = max(0,len(unmarked)-max(0,slots-reserved))
    for job in list(reversed(unmarked))[:excess]:
        job['reason'] = 'sentinel'
        record(job, interrupted_for_sentinel=True, draining_slot_reserved=draining)
        signal_child(job)


def cached_row(output, entry, arm, config):
    report = local_report()
    try:
        row = report.read_row(output, entry, arm, config, optional=True)
        if row and row.get('outer_timeout') and row.get('resource_policy') != report.kernel().POLICY:
            return None  # A worker-only SIGINT is not proof that the outer deadline ran.
        return row
    except (OSError, ValueError, TypeError, KeyError):
        return None


def archive_job(output, entry, arm, reason):
    """Unmeasured interrupts/stale source identities must not become cache hits."""
    destination = output/'interrupted'/f"{entry['instance_id']}-{arm}-{time():.6f}"
    for source in (output/arm/f"{entry['instance_id']}.json",
                   output/'checkpoints'/arm/f"{entry['instance_id']}.json"):
        if source.exists():
            destination.mkdir(parents=True,exist_ok=True)
            source.replace(destination/(('checkpoint-' if source.parent.parent.name == 'checkpoints' else '')+source.name))
    if destination.exists():
        _atomic_json(destination/'reason.json', dict(reason=reason, archived=time()))


def finalize_timeout(output, entry, arm, config, *, worker_pid, launched, cleanup):
    """Retain valid concrete checked evidence after reap; perform no new check."""
    report, candidates, rejected = local_report(), [], []
    for path in (output/arm/f"{entry['instance_id']}.json",
                 output/'checkpoints'/arm/f"{entry['instance_id']}.json"):
        if not path.exists():
            continue
        try:
            row = report.validate_row(json.loads(path.read_text()), entry, arm, config, require_complete=False)
            if row.get('completed'):
                observed = row['completed'].get('checked_epoch',path.stat().st_mtime)
                if not launched <= observed <= launched+CAP_S:
                    raise ValueError('No proof incumbent was checked during this launch before work cap')
            candidates.append(row)
        except (OSError, ValueError, KeyError, TypeError) as error:
            rejected.append(dict(path=str(path), reason=str(error)))
    def rank(row):
        from frozen_reporting.tiers import rank_analysis
        c = row.get('completed')
        return (c['level'], rank_analysis(c['analysis'])) if c else (99, ())
    if candidates:
        row = min(candidates, key=rank)
        row = dict(row, complete=True)
    else:
        row = dict(instance_id=entry['instance_id'], input=entry['path'], input_sha256=entry['sha256'],
            arm=arm, size=entry['size'], ports=entry['ports'], vessel=entry['vessel'], fill_pct=entry['fill_pct'],
            actual_fill=entry['fill'], development=entry['development'], n_boxes=entry['n_boxes'], config=config,
            run_key=report.kernel().run_key(config,entry['sha256']), cold=True, complete=True, passed=False,
            completed=None, best_safe_partial=None, observed_full_checks=0, checked_candidates=[],
            plan=None, report=None, assignment=None, lifts=None, parts=None, replay_legal_complete=False)
    row.update(outer_timeout=True, interrupted=True, returned_normally=False,
        status='passed' if row['passed'] else 'completed_relaxed' if row.get('completed') else 'outer_timeout',
        resource_policy=report.kernel().POLICY, wall_s=CAP_S,
        failure_reason='Declared uniform 600-second whole-arm/map cap; retained only pre-cap checked evidence',
        timeout=dict(worker_pid=worker_pid, launched=launched, deadline=launched+CAP_S,
                     recorded=time(), cleanup_s=cleanup.get('grace_elapsed_s'),
                     concrete_checkpoint_retained=bool(row.get('completed')),
                     rejected_checkpoints=rejected, checker_reexecuted=False,
                     replay_result='saved native report' if row.get('report') else 'unknown; no concrete saved result'))
    report.validate_row(row,entry,arm,config)
    _atomic_json(output/arm/f"{entry['instance_id']}.json",row)
    return row


def confirm_previous_output(output):
    """Probe only exact jobs from this output's controller registration ledger."""
    for path in (output/'pids').glob('worker-*.json'):
        previous = json.loads(path.read_text())
        if 'returncode' not in previous:
            try:
                os.kill(previous['pid'],0)
            except ProcessLookupError:
                pass
            else:
                raise CleanupBlocked(f'Previous exact registered PID {previous["pid"]} not confirmed absent')
        check_registered_descendants(output,worker_pid=previous['pid'],launch_epoch=previous['started'],
                                     job_argv=previous['argv'],grace_s=0.)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('dev','frozen'), default='dev')
    parser.add_argument('--out')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--arms', nargs='+', choices=('engine12','port_lp'))
    parser.add_argument('--max-workers', type=int, choices=(1,2), default=2)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    report, run = local_report(), local_report().kernel()
    if run.CAP_S != CAP_S:
        raise ValueError('Controller and worker cap differ')
    entries = report.load_manifest(phase=args.phase, limit=args.limit)
    arms = tuple(dict.fromkeys(args.arms or run.ARMS))
    if args.phase == 'frozen' and set(arms) != set(run.ARMS):
        raise ValueError('Frozen phase measures both arms on all32 fixed maps')
    configs = report.configurations(entries, arms)
    output_arg = args.out or ('results/dev' if args.phase == 'dev' else 'results/main')
    output = (HERE/output_arg).resolve()
    output.relative_to(HERE)
    if args.dry_run:
        print(json.dumps(dict(maps=len(entries), jobs=len(entries)*len(arms), phase=args.phase,
            workers=min(args.max_workers,1 if SENTINEL.exists() else 2), output=str(output))))
        return
    # One lock across all outputs and phases, rather than a lock per sweep.
    lock = (HERE/'controller.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    output.mkdir(parents=True,exist_ok=True)
    global_ledger = HERE/'controller-last.json'
    if global_ledger.exists():
        previous_output = Path(json.loads(global_ledger.read_text())['output']).resolve()
        previous_output.relative_to(HERE)
        if previous_output != output:
            confirm_previous_output(previous_output)
    confirm_previous_output(output)
    _atomic_json(global_ledger,dict(pid=os.getpid(),output=str(output),started=time(),argv=sys.argv))
    policy_path = output/'measurement-policy.json'
    policy = dict(policy=run.POLICY, phase=args.phase, cleanup_grace_s=360.,
                  cleanup_outside_censored_work_clock=True)
    selection = [dict(instance_id=e['instance_id'],arm=a,run_key=run.run_key(configs[e['instance_id'],a],e['sha256']))
                 for e in entries for a in arms]
    if policy_path.exists() and json.loads(policy_path.read_text()) != policy:
        raise ValueError('Output belongs to a different measurement selection/policy')
    _atomic_json(policy_path,policy)
    active, queue, completed = {}, deque(), 0
    for entry in entries:
        for arm in arms:
            if cached_row(output,entry,arm,configs[entry['instance_id'],arm]):
                completed += 1
            else:
                archive_job(output,entry,arm,'Invalid/missing current complete cache row; cold rerun queued')
                queue.append((entry,arm))
    stopping, error = False, None
    def interrupt(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT,interrupt)
    signal.signal(signal.SIGTERM,interrupt)
    def record(job, **state):
        path = output/'pids'/f"worker-{job['child'].pid}.json"
        row = json.loads(path.read_text()) if path.exists() else {}
        row.update(pid=job['child'].pid,parent_pid=os.getpid(),instance_id=job['entry']['instance_id'],
            arm=job['arm'],input=job['entry']['path'],started=job['launched'],argv=job['argv'],
            run_key=run.run_key(job['config'],job['entry']['sha256']),**state)
        _atomic_json(path,row)
    def signal_child(job):
        nonlocal stopping
        if job['child'].poll() is not None:
            return
        try:
            job['child'].send_signal(signal.SIGINT)
        except OSError as issue:
            stopping = True
            record(job,signal_error=str(issue),controller_session_interrupt_required=True)
            raise CleanupBlocked(f'Signal refused for exact owned PID {job["child"].pid}; interrupt its recorded controller session') from issue
    def service(draining=False):
        service_limits(active,record=record,signal_child=signal_child,sentinel_exists=SENTINEL.exists,draining=draining)
    def guard(job):
        return check_registered_descendants(output,worker_pid=job['child'].pid,
            launch_epoch=job['launched'],job_argv=job['argv'],on_wait=lambda:service(True))
    def summarize():
        return report.summarize(output,entries=entries,arms=arms,configs=configs,phase=args.phase)
    _atomic_json(output/'controller-process.json',dict(pid=os.getpid(),started=time(),argv=sys.argv,
        controller_sha256=run.digest(HERE/'controller.py'),report_sha256=run.digest(HERE/'report.py'),
        maximum_workers=args.max_workers,sentinel=str(SENTINEL),policy=policy,selection=selection))
    _atomic_json(output/'pids'/f'controller-{os.getpid()}.json',dict(pid=os.getpid(),started=time(),argv=sys.argv,
        selection=selection,policy=policy))
    def reap(job, status):
        nonlocal completed
        record(job,returncode=status,reaped=time(),reason=job['reason'])
        cleanup = guard(job)
        entry, arm, config = job['entry'],job['arm'],job['config']
        if job['reason'] == 'timeout':
            finalize_timeout(output,entry,arm,config,worker_pid=job['child'].pid,launched=job['launched'],cleanup=cleanup)
            completed += 1
        elif job['reason'] in ('sentinel','controller_interrupt'):
            archive_job(output,entry,arm,job['reason'])
            queue.appendleft((entry,arm))
        elif status != 0:
            archive_job(output,entry,arm,f'unexpected exit {status}')
            raise RuntimeError(f'Worker {job["child"].pid} exited {status}; no completed row credited')
        else:
            result = report.read_row(output,entry,arm,config)
            if result.get('completed') and not job['launched'] <= result['completed'].get('checked_epoch',float('inf')) <= job['launched']+CAP_S:
                archive_job(output,entry,arm,'Completed incumbent has no pre-cap observation in this launch')
                raise RuntimeError('Completed incumbent lacks pre-cap absolute timestamp')
            if result.get('interrupted') or result.get('outer_timeout'):
                archive_job(output,entry,arm,'Unowned interruption has no controller deadline evidence')
                raise RuntimeError('Normal exit carried unowned interruption; not a measured completion')
            completed += 1
        summarize()
    try:
        summarize()
        while (queue or active) and not stopping:
            service()
            for pid in list(active):
                if pid not in active:
                    continue
                job = active[pid]  # Refresh after callbacks may have changed other reasons.
                status = job['child'].poll()
                if status is None:
                    continue
                if job['reason'] is None and monotonic()-job['began'] >= CAP_S:
                    job['reason'] = 'timeout'
                    record(job,deadline_reached=time(),cap_s=CAP_S,observed_exit_at_or_after_deadline=True)
                del active[pid]
                reap(job,status)
            slots = min(args.max_workers,1 if SENTINEL.exists() else 2)
            while queue and len(active) < slots and not stopping:
                if len(active) >= min(args.max_workers,1 if SENTINEL.exists() else 2):
                    break
                entry,arm = queue.popleft()
                config = configs[entry['instance_id'],arm]
                # Source/data must remain unchanged throughout the sweep.
                if run.config_for(entry,arm) != config or run.digest(ROOT/entry['path']) != entry['sha256']:
                    raise ValueError('Measured source or input changed during controller sweep')
                archive_job(output,entry,arm,'fresh cold attempt; prior checkpoint not reused')
                argv = [str(HERE/'run.py'),'--worker','--phase',args.phase,'--arm',arm,'--input',entry['path'],
                        '--size',entry['size'],'--ports',str(entry['ports']),'--out',str(output.relative_to(HERE))]
                command = ['/usr/bin/nice','-n','5',sys.executable,*argv]
                logpath = output/'logs'/f"{arm}-{entry['instance_id']}.log"
                logpath.parent.mkdir(parents=True,exist_ok=True)
                logfile = logpath.open('a')
                env = {**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
                for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','STOWAGE_IN_PORT_WORKERS'):
                    env[key] = '1'
                launched,began = time(),monotonic()
                env['C6_WORKER_DEADLINE'] = str(launched+CAP_S)
                env['C6_WORKER_STARTED'] = str(launched)
                try:
                    child = subprocess.Popen(command,cwd=ROOT,env=env,stdout=logfile,stderr=subprocess.STDOUT)
                finally:
                    logfile.close()
                job = dict(child=child,entry=entry,arm=arm,config=config,reason=None,began=began,launched=launched,argv=argv)
                active[child.pid] = job
                record(job,command=command,cap_s=CAP_S,log=str(logpath))
                print(json.dumps(dict(started=entry['instance_id'],arm=arm,pid=child.pid,deadline=launched+CAP_S)),flush=True)
            if active:
                sleep(1.)
    except BaseException as issue:
        stopping,error = True,f'{type(issue).__name__}: {issue}'
        raise
    finally:
        cleanup_errors = []
        for job in active.values():
            if job['reason'] is None:
                job['reason'] = 'controller_interrupt'
            try:
                signal_child(job)
            except CleanupBlocked as issue:
                cleanup_errors.append(str(issue))
        while active and not cleanup_errors:
            service()
            for pid in list(active):
                job = active[pid]
                status = job['child'].poll()
                if status is not None:
                    del active[pid]
                    try:
                        reap(job,status)
                    except Exception as issue:
                        cleanup_errors.append(str(issue))
            if active:
                sleep(1.)
        _atomic_json(output/'controller-state.json',dict(pid=os.getpid(),finished=time(),interrupted=stopping,
            completed_jobs=completed,pending_jobs=len(queue)+len(active),error=error,cleanup_errors=cleanup_errors))
        if cleanup_errors:
            raise CleanupBlocked('; '.join(cleanup_errors))
        lock.close()
    if stopping:
        raise SystemExit(130)
    summarize()
    _atomic_json(output/'controller-complete.json',dict(finished=time(),jobs=completed,maps=len(entries),phase=args.phase))


if __name__ == '__main__':
    main()
