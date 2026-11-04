"""Bounded, identical per-arm watchdogs for exact recorded owned groups only."""
import os
import signal
import subprocess
import time

POLICY = dict(planner_s=3600., audit_s=600., terminate_grace_s=5., kill_grace_s=5.)


def run_owned(command, *, cwd, env, stream, mode, policy, record):
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stream,
                               stderr=subprocess.STDOUT, start_new_session=True)
    # start_new_session makes this PID the exact owned process-group ID.
    pgid = process.pid
    record('owned_pid', pid=process.pid, pgid=pgid, mode=mode)
    began = time.monotonic()
    limit = policy['planner_s' if mode == 'plan' else 'audit_s']
    timed_out = False
    while True:
        try:
            code = process.wait(timeout=min(1., max(.01, limit-(time.monotonic()-began))))
            return dict(returncode=code, timed_out=False, pid=process.pid, pgid=pgid,
                        elapsed_s=round(time.monotonic()-began, 3))
        except subprocess.TimeoutExpired:
            if time.monotonic()-began < limit:
                continue
            timed_out = True
            break
    record('watchdog_timeout', pid=process.pid, pgid=pgid, mode=mode, limit_s=limit)
    termination_error = None
    for sig, grace in ((signal.SIGTERM, policy['terminate_grace_s']),
                       (signal.SIGKILL, policy['kill_grace_s'])):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass
        except OSError as error:
            termination_error = f'{type(error).__name__}: {error}'
            record('watchdog_termination_denied', pid=process.pid, pgid=pgid, error=termination_error)
        try:
            code = process.wait(timeout=grace)
            return dict(returncode=code, timed_out=timed_out, pid=process.pid, pgid=pgid,
                        elapsed_s=round(time.monotonic()-began, 3), termination_error=termination_error)
        except subprocess.TimeoutExpired:
            continue
    # Caller must stop the queue; this lane cannot be replaced while its group
    # is unconfirmed. There is no unbounded process.wait() fallback.
    return dict(returncode=process.poll(), timed_out=True, undrained=True, pid=process.pid, pgid=pgid,
                elapsed_s=round(time.monotonic()-began, 3), termination_error=termination_error)
