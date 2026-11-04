# D175 headroom

All code, evidence and state live here. No source edits, GPU, training, held-out inputs or git operations are needed.

`train_maps.json` fixes the forty small maps and eight archived medium maps, with corpus-manifest hashes and exact instance hashes. `source_snapshot.json` records the pre-study source state; each run also locks all source Python, source/config data, parameters, wrapper code, run budgets and input hashes. Changed sources or inputs require a new run directory.

The detached supervisor is recorded in `launch.json` and `supervisor.json`. It runs a checked pilot, then resumes all sixty-four random layouts per map plus local search. `HEADROOM.md` refreshes every thirty seconds. `status.json`, `supervisor.log`, `runs/study/controller.log` and per-map `progress.jsonl` show progress. At most three map workers and their owned V4 children compute concurrently; each numerical library has one thread. An initial two-map admission phase waits for the recorded calibration pilot to exit before expanding the pool.

For an interrupted run, after its recorded controller has exited, resume without changing code, manifests or budgets:

```sh
.venv/bin/python experiments/mvp/strategy/headroom/run.py \
  --manifest experiments/mvp/strategy/headroom/train_maps.json \
  --out experiments/mvp/strategy/headroom/runs/study \
  --workers 3 --stage full --detach
```

Refresh the report manually:

```sh
.venv/bin/python experiments/mvp/strategy/headroom/report.py \
  --out experiments/mvp/strategy/headroom/runs/study
```

`evals/` contains measured two-step outcomes; `plans/` are uncertified planner results. `checks/`, `reports/` and `finished/` preserve certification evidence. Reported moves require full-checker acceptance and independent layout auditing. Identical physical plans can share a checker certificate, with a separate layout audit and explicit certificate reference; copied certificates do not count as additional compute calls.

Calibration folders preserve implementation smoke evidence and are excluded from the main study's table. Discarded warmup solves avoid cold-cache bias and are excluded from measured layout timing; setup and generation costs are reported separately. The archive medium maps retain historical drift lineage. See `ASSUMPTIONS.md` for limits.

The live resource row is in `LEDGER.md`. Never stop other jobs or discover PIDs by process patterns. Owned historical map PIDs are recorded in `workers.json`; launch/controller files identify the current owner. Prefer allowing atomic checkpoints and natural completion. A source/config mismatch deliberately blocks resume rather than mixing epochs.

The first checked read is frozen in `FIRST_READ.md` and `first_read.json`; `HEADROOM.md` continues refreshing. Initial paired results use sixteen random layouts plus local search; the supervisor automatically queues the full sixty-four-layout continuation. `final_verification.json` records the source/config hash and certification checks at handoff.
