# Native replans and background jobs

`jobs.py` runs submitted operations on one process-wide daemon dispatcher. Every
operation holds a shared reentrant guard because binding installs process-wide
checker, placement and multiprocessing hooks. A daemon thread avoids an executor
exit hook waiting for a long native call. This is responsiveness plumbing, not a
promise that a native replan finishes within the conversation's time budget.

The worker must receive a detached ToolSet/instance/receipt snapshot. Use
`start(operation, snapshot=value)` to deepcopy an explicit snapshot before
submission, or provide a zero-argument closure that already owns detached state.
The manager cannot verify that an arbitrary closure avoids live host objects.
`identity` and `context` are separately copied JSON metadata.

```python
jobs = JobManager()
progress = jobs.start(detached_replan_and_full_check,
                      identity=current_identity,
                      context={'kind': 'native_replan'})
# Return progress immediately. A later user status request makes one poll.
progress = jobs.poll(progress['job_id'], identity=current_identity)
if progress['status'] == 'completed':
    prepared = jobs.result(progress['job_id'], identity=current_identity)
    # Compare the current host generation, instance, plan and confirmed reading
    # again, then install the worker's already checked state. Do no native work
    # here. The worker prepared the full checker and instruction audit result.
```

`poll` returns JSON metadata with measured `elapsed_s`, state, queue/execution
phase and `result_available`. It never serializes native payloads or exposes a
plan digest. `result` is a host-only in-memory accessor, not a model tool.
`wait(timeout_s=...)` is optional host convenience, capped at a quarter second;
normal conversation status tools should use `poll` once. No background poll loop
is required. The manager keeps results until the host calls `forget`.

An identity mismatch or `cancel` marks the job stale and discards publication.
Cancellation cannot safely interrupt native Python/C work or its subprocesses;
a running operation finishes, restores its binding hooks and discards its return
value. A queued stale job is skipped. No process is killed. A long stale job can
therefore delay later native jobs in this process.

Synchronous native tools must also acquire `native_guard(blocking=False)` and
return an unavailable/running result if it is held. `busy()` is advisory and has
a check/use race; it is not sufficient mutual exclusion. Nested acquisition by
the same worker is supported. The guard covers cooperating callers in this
process, not other processes or unrelated services.

The host remains responsible for planner confirmation before submitting a job,
freshness checks before installing it, and treating only a full checked result
that obeys every confirmed instruction as a pass. The manager is intentionally
agnostic about candidate shapes and never manufactures a checker verdict.

## Investigation of the interrupted native run

Source audit only; no new native solve or checker run was started for this
investigation. The evidence is
[`real-smoke-run.log`](real-smoke-run.log), ending in a user/process interrupt.
The saved stack shows:

1. `Copilot.turn` synchronously called `ToolSet._replan`.
2. Binding's `execute_brief` entered `plan_with_rules` and the native planner.
3. Portfolio start finishing called the full checker through binding's observer.
4. Voyage stability entered `_context`, then a cache miss in `_geometry`.
5. `_geometry` called `stability_liquid.metadata`; the interrupt occurred inside
   `json.dumps(inputs, sort_keys=True)`.

The stack proves the conversation was occupied by native planning/checking. It
does **not** prove an infinite loop, deadlock, total time spent serializing, or
repeated metadata serialization. There is no timing or call-count profile in the
log. The periodic waiting lines are not a native progress measurement.

Current source has these relevant boundaries:

- `src/stowage/checker/stability.py:68`: `_geometry(name, payload)` is an LRU
  cache with a bounded vessel/content key. It constructs the geometry and saves
  `_stability_liquid_metadata` on that geometry once per successful cache entry.
- `stability.py:86` caches parsed parameter bytes and canonical JSON.
  `_context` still reconstructs a fresh parameter tree with `json.loads(payload)`
  for each call, then looks up the cached geometry. This repeated tree
  reconstruction is distinct from repeated metadata hashing.
- `stability.py:618` passes the geometry's cached metadata into `attach_tables`.
  `stability_liquid.py:118` recomputes metadata only if that private value is
  absent. The metadata function at `stability_liquid.py:43` filters and hashes
  physical inputs; it is not a checker or geometry wrapper.
- `binding/runtime.py` redirects native cache paths and some keyword defaults
  within a locked scope. It does not clear the geometry LRU or modify its key.
- `binding/hooks.py` wraps full-checker aliases to observe improving checked
  incumbents, and wraps engine placement/score/process entry points for rules.
  It does not wrap or replace `_geometry` or `stability_liquid.metadata`.
  Its observer delegates to the original full checker. Binding also performs a
  separate final original-instance checker audit before release; those are
  intentional checks, not evidence of repeated cold geometry construction.
- `src/llm/instructions/replan.py:68` wraps
  `securing_geometry_violation` placement screens. Despite “geometry” in that
  name, this is not the hydrostatic `StabilityGeometry` cache.
- `stability_budget.py` states that the legacy `budget(seconds)` API validates
  its argument but imposes no elapsed-time cutoff. The native stability setting
  is therefore not a wall-clock guarantee and must not be presented as one.

The demonstrated defect belongs to the copilot's synchronous tool boundary: a
long native operation occupied a conversation turn. Background execution fixes
that boundary. The audited source does not establish a binding/cache correctness
bug or support removing full checks. A cold process, a new vessel/content key,
an evicted LRU entry, an interrupted construction that never cached, and each
fresh spawned process can legitimately compute metadata again.

Recommended follow-up for the binding/checker owners: profile the exact native
configuration with admission, collecting per-process `_geometry.cache_info()`,
metadata call counts, key/content identities and phase timings for cold and warm
checks. If identical successful keys repeatedly miss in one process, investigate
cache identity/lifetime; if metadata repeats with geometry hits, investigate the
private metadata forwarding path. Measure repeated `_context` tree decoding
separately. Any native optimization belongs to its owner and must preserve
content identities, certificate validation and the complete checker gate.

## Verification

`test_jobs.py` uses cheap callbacks and threading events. It verifies prompt
start/poll responses, measured elapsed progress, detached snapshots, one serial
daemon across managers, reentrant/nonblocking guard behaviour, sanitized worker
errors, stale identity/cancellation, and withholding native payloads from poll.
It neither measures native planning speed nor supplies engine feasibility
evidence. No paid calls were made.
