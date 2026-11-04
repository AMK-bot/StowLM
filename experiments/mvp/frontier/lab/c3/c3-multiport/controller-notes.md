# External resource policy and optional controller

The uniform cap is **1,800 elapsed seconds for every arm on every map**, including
construction, search, finishing, and the independent full checker. It applies
to all 117 jobs in the fixed 39-map cohort. Earlier complete results are retained
because their measured wall times were already below the same cap. This is an
external experiment policy: `run.py`, frozen engine sources, graph sources,
budgets, run keys, and the 54 completed tiny rows are unchanged.

`parallel_run.py` is optional. Stop and reap the recorded serial controller and
its active worker before launching it. The driver refuses live PIDs from those
records, holds an exclusive controller lock, and also checks its own previous
unfinished worker records. It launches at most one worker while the leader's
`BENCH-RUNNING` sentinel exists, and at most two otherwise. If the sentinel
reappears, it interrupts and requeues one of its registered workers. The
controller never signals a process selected from a process listing or pattern.

The deadline clock starts immediately before `Popen`. Once a worker reaches
1,800 seconds, the controller sends SIGINT to that registered child, waits for
it to exit, then records an `outer_timeout` failure. Polling can add up to one
second before the interrupt request; reaping and recording add cleanup time.
No timeout row is written while the worker is still alive. A signal permission
failure is recorded and printed, and the controller stops launching work. In
that case interrupt the controller's **own recorded execution session** so its
worker/native processes receive the session interrupt. The controller and its
workers share that session; the controller then waits for registered children
to exit. It never silently substitutes an unverified timeout row for a live job.

After reaping a worker, the controller correlates native PID records by exact
job arguments, parent chain, and launch epoch. It probes only those recorded
PIDs and allows up to 360 seconds for exit cleanup, retrying once per second
without launching another job. The audit retains the initial evidence and
every subsequent snapshot. Malformed or inconsistent registrations fail
immediately; persistent live or permission-unknown PIDs fail closed after the
grace period. An in-flight native V4 finishing calculation can still have part
of its existing 300-second allowance left when its parent exits; parent exit
also removes the parent-side watchdog, and atomic numerical work can delay
native exit. The six-minute grace is cleanup overhead outside the censored
1,800-second solve clock, not extra credited search or a later pass opportunity.
This wait introduces no process signals. Existing deadline
checks continue for any other active direct worker, and the scheduler reads
its updated timeout state before handling its exit. Registered absence still
cannot certify a process that has not yet written its startup registration.
The draining native process reserves one busy slot. If the leader sentinel
reappears during grace, every other active worker without an existing interrupt
reason is interrupted for requeue; existing timeout reasons are preserved.

`timeout_row.py` can also record a timeout after the serial controller has been
interrupted and its worker reaped. It loads unchanged `run.py` and temporarily
replaces that module's imported `score.evaluate` with a function that returns
only an honest timeout record. Calling the unchanged `worker(args)` reconstructs
the exact normal configuration, source hashes, input validation, and run key.
No method, solver, replay, finisher, or checker runs. A complete cached result
that won a deadline race is retained. The ordinary worker PID record describes
the helper process writing the measurement; the parallel controller separately
preserves each original `Popen` PID and its exit status.

Timeout rows contain no plan, parts, lift count, or pass. Their wall time is the
1,800-second cap. Stage timings and the complete-replay gate are unknown
(`None`), with `replay_legal_known=False`; interrupted work may have constructed
a legal stow without returning a measurement. Reports should count timeouts and
unknown replay gates separately, rather than treating unknown replay as a
proved construction failure. Completed passes still require the unchanged
independent full checker, and timeout rows never enter lift/restow means.

At launch the controller writes immutable `results/main/measurement-policy.json`, including
the policy, existing completed-row hashes/timings, and controller/helper/kernel/
cohort hashes. It checks that no previously completed row exceeds the cap.
Subsequent declarations verify the same policy core and retain the original
declaration and premeasurement audit unchanged. The standalone helper also
declares this policy before recording its first timeout.
Normal existing run keys are preserved intentionally. Restarting the controller
queues the same jobs and lets unchanged `run.py` reuse complete rows, including
complete timeout failures. Every completed/timeout job refreshes the report through
explicit `score.import_plugin` loading, avoiding module-name collisions.

Nothing was launched or evaluated while preparing these files. Only source
inspection and syntax parsing were performed; no solver/checker tests ran.
