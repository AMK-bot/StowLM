# MVP gold generation

`driver.py` streams atomically accepted corpus shards, runs each case in a fresh
process, and resumes completed case records. `plan.json` is published only after
ballast finalization, a full checker shipping gate, and a complete production
token-mask replay including end tokens. Failures remain structured records but
never become training plans. No repair is used. Rule freeze and C2 stay OFF.

The detached tiny supervisor command is:

```sh
UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/mvp/gold_tools/launch.py --name tiny-v6 -- --rung tiny --workers 5 --adaptive-workers --max-load 16 --policy-file experiments/mvp/gold/tiny-policy.json --exact-s 60 --resume-finished --follow --poll-s 10 --admit-ledger experiments/fleet/ledger.md --ledger-owner mvp-gold/tiny --stop-file experiments/mvp/gold/STOP-tiny
```

Current supervisor PID and exact command are in `gold/tiny-v6.process.json`;
the original supervisor is recorded in `gold/tiny.process.json`. Worker PIDs
are in each case's `process.json`; logs are `gold/tiny-v2.log` and per-case
`worker.log`. The current supervisor log is `gold/tiny-v6.log`. Signal only
recorded owned PIDs. `gold/runtime.json` distinguishes running versus queued
admission; `gold/admission.json` records resource evidence. The supervisor
rechecks admission between worker-sized batches and automatically consumes new
accepted shards. A queued supervisor uses negligible compute. The shared
ledger records the actual admitted gold allocation in its own row. Creating
`gold/STOP-tiny` requests a cooperative stop after the current shard completes.
Remove that file before a future restart. Finished plans remain immutable when
`--resume-finished` permits a new configuration for pending cases.

The scheduler rereads `tiny-policy.json` between shards: training has priority,
tiny validation is capped at 50 passing plans, and the live-load threshold is
16. It waits for unattempted requested training shards before scheduling more
validation. Small uses the same policy with a 56-plan validation cap. These
policy changes do not alter solver identities or existing finished plans. The
GPU milestone in `YIELD.md` counts 300 **train** plans.

`coordinate.py` waits for the per-ship v2 heldout freeze and matching small
shards, starts the small supervisor automatically, and reloads worker ceilings
for both rungs by remaining training effort. It considers pending validation
and inventory exhaustion so an unreachable gold count cannot reserve idle
capacity. See `gold/SMALL-READY.md`; live allocation evidence is in
`gold/coordination.json`. Tiny retains its default finisher; small scopes V4
to finalization only. Stop the coordinator with `gold/STOP-coordinator` before
manually changing shared allocation policies.

APPROXIMATION: tiny exact search is capped at 60 seconds, then scalable search
with objective improvement has 60 seconds only if no exact incumbent exists.
An exact optimum skips scalable entirely; a best-found exact incumbent also
skips the optional best-of-two search to minimise time to 300. Every accepted plan is labelled
best-found or retains the exact solver's scoped optimality label. No exact
certificate covers every physically possible plan. Objective and all move
counts come from the checker. The initial exact-10-second pilot and its finished
plans remain recorded under their original settings.

LNS means removing and replanning part of a candidate to try to improve its
objective; this driver keeps the scalable solver's existing improvement phase.
Repository implementation and sources: `src/stowage/solver/scalable.py`.

After the leader's go, save the **adopted** `ScalableConfig` keyword settings as
`experiments/mvp/gold/locked-scalable.json` (include `constructor_v4: true` there
only if adopted). The lock must include the regenerated corpus and qualified
time budget. This command is ready for a 120-second budget if that budget is
adopted; replace the budget with the measured lock value otherwise:

```sh
UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/mvp/gold_tools/launch.py --name small-medium -- --rung small medium --workers 12 --solver-s 120 --follow --scalable-config experiments/mvp/gold/locked-scalable.json --admit-ledger experiments/fleet/ledger.md
```

This command has **not** been started. The settings file does not yet exist.
Twelve workers require admission and the leader's go. Separate rung invocations
support different budgets; their worker counts must fit a combined 12-core
reservation. Completed cases are immutable and reject input/config changes;
use a fresh output root for a retry campaign. The output manifest can retain
different configurations for tiny and later rungs.

Run the fast contract checks and render measured reports:

```sh
UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync pytest experiments/mvp/gold_tools/test_gold.py -q
UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/mvp/gold_tools/render_status.py
```

`gold/YIELD.md` and `gold/STATUS.md` update after each detached completion. The
first-30 projection remains provisional until 30 measured attempts exist.
Corpus generator acceptance never substitutes for checker/mask acceptance.
