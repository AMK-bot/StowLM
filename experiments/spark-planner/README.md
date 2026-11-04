# Muse Spark baseline and demo explanation (5.5 / 5.6)

No live API calls were made. Tests replace HTTP and exercise the real task 1.8
client, including spend reservations and JSONL settlement. The planner name is
`muse-spark-zero-shot`. It sends the task 4.5 compact observation plus action
grammar, three independent syntax examples and (D76, default) a plain-language
rules-and-limits appendix from `llm.spark.rules_appendix`, parses through the shared action
codec, and stops replay at the first malformed or infeasible action. Zero-based
failure indices and `raw_plan_legal` are recorded. Average that metric over every
attempt, including API/parse failures, to measure complete legal response rate.
This is separate from the full-checker pass rate. API inference is nondeterministic;
`seed` records the experiment identity but is not a provider sampling guarantee.

The compact observation inherits task 4.5's documented information loss. Exact
physics and hard constraints remain in the original instance, feasibility engine,
ballast finalizer and checker. No checker approval is inferred from valid syntax
or a legal prefix. `run.py` uses `stowage.planner.run_planner` with repair disabled
so baseline failures remain visible. It refuses a stubbed task 5.2 harness before
constructing a client. A repaired-plan comparison should be a separately labelled run.

## 6.7 test-split estimate (measured in the w2 pilot, 29 Oct)

From `w2-pilot/` (`summary.json`, `appendix-tokens.json`; label: pilot, pre-freeze epoch, not a result).
Muse Spark reasons before answering, and reasoning counts against `max_output_tokens`:
tiny used 24–28k output tokens, small 42–91k (one small truncated at 65,536). The planner's
default cap is now the measured need per rung (`DEFAULT_MAX_OUTPUT_TOKENS`: tiny 65,536,
small/medium/full 131,072, the API maximum); pass `--max-output-tokens` only to override.
The prompt is the D76 rules-appendix condition (`--compact-only` selects the labelled
ablation; the pilot responses are compact-only). Means include truncated attempts, which
are billed. `estimates.json` holds the numbers; the old 4,096-allowance estimate ($6.06),
which would have truncated every call, is kept there as superseded.

| Rung | Test instances | Cap | Pilot $ / instance (n) | + appendix input | $ / instance | 1 sample | 4 samples |
|---|---:|---:|---:|---:|---:|---:|---:|
| tiny | 75 | 65,536 | 0.1143 (3) | +1,603 tok, $0.0020 | 0.1163 | $8.72 | $34.89 |
| small | 75 | 131,072 | 0.2856 (3) | +1,856 tok, $0.0023 | 0.2879 | $21.59 | $86.36 |
| medium | 75 | 131,072 | not measured | | ≤ 0.57 | ≤ $42.72 | |
| full | 75 | 131,072 | not measured | | ≤ 0.57 | ≤ $42.72 | |
| tiny + small | 150 | | | | | **$30.31** | $121.25 |

**APPROXIMATION:** n = 3 per rung, and reasoning need varies widely per instance. Glimmer
proxies Spark's tokenizer for input. Medium and full are unmeasured; they would likely exceed
the 131,072 single-response limit under this prompt and reasoning setting (an API limit, not
a capability claim; D76 asks for 2–3 real instances each at the cap). Reasoning setting: the
API exposes `reasoning.effort` (minimal/low/medium/high/xhigh; dev.meta.ai/docs/protocols/responses,
checked 29 Oct). `--reasoning-effort` sends it; omitted, nothing is sent and runs record
`reasoning=provider-default`. Each ledger row records the setting; the A/B waits for requalification.
Calls are serialised by the ledger lock (~2 min tiny, 3–5.5 min small). The table is not
a billing guarantee.

Before any request the runner validates **all existing checkpoint identities**,
then tokenizes only pending prepared test instances. It refuses if the pending
estimate exceeds the remaining $60 baseline allocation. Prior measured spend
and pending/unknown holds stay charged during resume. A fully completed resume
needs no client or credit gate override.
Every planner call repeats the allocation guard. Job usage is the job's own
reservations and settlements (unsettled holds stay charged) plus any foreign spend
added after the job started; releasing a hold the job didn't make never adds
headroom (w2-rev-spark High 1). A resumed job seeds its earlier spend with
`allocation_budget(..., prior_job_usd=...)`. The planner locks the
authoritative shared budget and binds its baseline ceiling
across every conservative full-context client reservation and retry, including
injected clients. The runner also requests a $60 client cap;
concurrent reservations can therefore refuse a run that passed the proxy estimate.
All callers must share the default spend ledger. Preflight is an estimate, not a
whole-run reservation; concurrent sessions can consume headroom before completion.

Re-estimate the current prompt (historical measurement: one core, <1 GB, no GPU,
0.45 s):

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false UV_CACHE_DIR=/private/tmp/stow-spark-uv-cache uv run --offline --no-sync python experiments/spark-planner/estimate.py
```

## Exact later run command

Task 6.3 must first prepare a directory of clean Instance JSON/JSON.GZ files for
**all 300 test specs** in `experiments/splits/v1/manifest.json`. The runner matches
by `spec_from_instance`, rejects duplicates/missing specs, and never substitutes
successful generations for failed ones. Generation failures belong in the 6.3
audit; resolve/report them before claiming a complete 300-instance run. The
directory below is an input prerequisite, not produced by this task.

Only after the user confirms the $150 Meta credit has been applied and authorizes
the paid run, with `MODEL_API_KEY` already exported and task 5.2 implemented,
explicitly enable readiness and run from the repository root. Keep the gate
closed while the credit is pending; a key or historical smoke test does not
establish readiness:

```sh
SPARK_CREDIT_READY=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false UV_CACHE_DIR=/private/tmp/stow-spark-uv-cache uv run --offline --no-sync python experiments/spark-planner/run.py --instances experiments/baseline-runs/test-instances --output experiments/spark-planner/run-v1 --run
```

Omit `--run` for local-only preflight; that path does not require a key or
readiness flag. The runner refuses pending paid work unless the exact flag
`SPARK_CREDIT_READY=1` is set, and the client rechecks readiness for every call. Per-spec
JSON checkpoints contain planner result and checker report, and `summary.json`
reports raw legal response and checker pass rates over completed plus newly
attempted specs. `estimated_usd` covers only requests pending at the start of
this invocation; `remaining_usd` is the corresponding preflight headroom.
`completed_instances` and `pending_instances` record that initial resume state. Restart with the same manifest,
implementation and options to resume; use a new output directory after changes.
No plan is shippable unless its full checker report passes. Costs are authoritative
in `experiments/spark-spend/calls.jsonl`; the client automatically synchronizes
the managed `docs/compute.md` spend row before HTTP and after settlement. This
includes measured incomplete responses and conservative pending/unknown holds.
Do not manually copy a second spend row. After an interrupted accounting write,
`SpendLedger().sync_compute()` repairs that row offline from the authoritative
JSONL; do not clear holds without provider reconciliation.

## Explanation

`llm.spark.explain.explain(instance, plan, report)` first verifies the report
belongs to the complete supplied instance and plan with `report_matches_inputs`.
Mismatched reports, including legacy reports without input fingerprints, raise
`ValueError`: recheck the inputs before explaining them.

The result is labelled either `[Muse Spark explanation]` or
`[Deterministic template fallback]`. The checker verdict and every factual
sentence are deterministic. Spark may select only the ordering of six complete
factual sections; generated prose, extra keys, missing/duplicate sections and
other unverified responses select the fallback. This constrains the model so it
cannot claim a failed plan is safe or invent counts, margins or causes.

The sections distinguish proposed lifts from checker replay counts and label
`weighted_nonproductive` as the **total weighted objective including projected
future lifts**. They include the three containers with most weighted rehandle
lifts, checker margins, violations and shut-outs. Lift weights are not money;
no optimality or causal claim is made. Positive checker margins are inside their
limits, and values in different units are never compared.

`PlanExplainer(use_api=False)` forces deterministic offline demo output. The
default API client keeps the readiness gate closed until explicitly enabled.
Missing key, held credit, client/spend/HTTP failure, typed incomplete response or
unverified model output selects the labelled template.

Sources: task 4.5 [format sizing](../format/README.md), task 3.8
[action semantics](../action-space/README.md), and the frozen
[split specification](../splits/v1/README.md). Token count details and pinned
manifest checksum are in [estimates.json](estimates.json).

## Verification commands

All Python commands use Python 3.12 through uv. Set the local-only execution
environment as follows (no model weights, no GPU):

```sh
export UV_CACHE_DIR=/private/tmp/stow-spark-uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false
uv run --offline --no-sync pytest -q tests/llm/test_spark_planner.py tests/llm/test_spark_planner_budget.py tests/llm/test_spark_planner_run.py tests/llm/test_spark_explain.py
uv run --offline --no-sync pytest -q tests/test_frozen_interfaces.py
uv run --offline --no-sync pytest -q
uv run --offline --no-sync python experiments/spark-planner/run.py --help
```

Historical implementation verification: the then-focused suite passed **33 tests**,
entirely offline/mocked. Current fix-wave verification is recorded in
`experiments/review/fixes/fix-t5-spark.md`. Frozen
interfaces pass. Full-suite output is retained in `pytest-full.log`; focused
output is in `pytest-targeted.log`. One transient concurrent feasibility edit
(`Tables.e2_tolerance` missing) interrupted an earlier focused run; rerun passed
without editing feasibility code. There was no API spend. Module byte-compilation
also passes. CPU sizing completed in 0.45 s versus an expected <1 s/<1 GB on
one core. The documented <3 min default-suite expectation was exceeded by the
shared physics tests; process RSS was not measurable (`ps` is sandbox-blocked).
No fleet ledger file was edited because it lies outside this order's scope fence.

Full-suite first pass took about 6 min and exited 1 with 12 setup errors: it had
collected an earlier planner fixture while that file was still being completed.
The final fixture passed focused verification. Required retry began 11:12:16 UTC,
with the final source fixed: estimate one core, <4 GB, no GPU, ~6 min, based on the
first full pass. Admission check: ledger empty, load average 8.67 on 18 cores,
92% memory free; process inventory was blocked by the sandbox. The reservation is
recorded here because the fleet ledger is outside the explicit edit scope.
Retry output is in `pytest-full-retry.log`.

**Final full-suite retry: PASS (exit 0, 100%).** Start 11:12:16 UTC, finished
11:19:19 UTC (7 min 3 s versus ~6 min estimate), one CPU-thread allocation,
no GPU/API use. Existing skips/xfail and a solver NumPy deprecation warning remain;
unmarked slow hull/stability tests explain the wall-time overrun. Peak RSS is
unverified. No open implementation failure remains; live Spark behavior and the
complete generated 6.3 test panel remain unverified prerequisites.
