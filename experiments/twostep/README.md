# Industry two-step baseline (4.8)

## Fix-wave evidence (checker 2.0 / linear 3.5 / schema 1.7)

The frozen planner ID is `baseline.twostep`. Wall-clock-bounded search is
declared nondeterministic. These runs keep strict feasibility and full physics;
no hull, securing, ballast, or expert-rule limit was relaxed.

Fresh scoped, synthetic train/validation inputs are selected by the frozen 5.3
split policy before generation, never from test shards. Each input records its
SHA256, split specification, generator audit and source epoch. Measurement rows
record component versions, all local `src/stowage`/policy source hashes at start
and end, planner source hashes, budgets, child PIDs and process reaping. Rows
with changed generation/runtime sources are quarantined. This is a two-case
smoke diagnostic, not a representative population estimate.
These inputs are exploratory/noncanonical: split assignment does not establish
membership in the frozen released inventory, and no release-eligibility claim
is made. Every scoped manifest declares `canonical: false`.

| Rung / seed | Raw outcome | Checker-approved / current-qualified requests / raw requests | Terminal failures / reached | Checker passes / after-ballast submissions |
|---|---|---:|---:|---:|
| tiny / 530000 (val) | pre-stow dead end | 0/0/1 | 0/0 (unknown) | 0/0 (unknown) |
| small / 540001 (train) | pre-stow timeout | 0/0/1 | 0/0 (unknown) | 0/0 (unknown) |

Final scoped rows and current-source audit are retained in
[fix-current/results.json](fix-current/results.json) and
[summary.json](fix-current/summary.json). Both rows were stable from generation
through measurement, but later changes to `feasibility/_engine.py` and
`feasibility/capacity.py` invalidate them for the final current epoch. They
remain raw observations, not current qualification. Tiny cannot place a box
without violating a placement/completion check; small exhausts its 10 s
pre-stow share of the 20 s planner budget. Neither reaches the terminal, ballast,
or full checker. Objectives, lifts and exact gaps are unmeasured, and terminal
failure frequency is unknown because the terminal denominator is zero.
The first pilot reproduced the concurrent missing-`direction` feasibility call;
the retry gets past that API after its owner migrated to D49. An intervening
small run was quarantined when feasibility/generator source changed. The early
retry without a completed manifest is a harness error, excluded entirely.

The separate [seed-12 diagnostic](fix-current/diagnostic-seed12/summary.json)
exercises current full-hull generation → pre-stow → terminal → ballast attempt
→ full checker. Its raw cargo plan succeeds with fixed categories and no
fixed-slot failure (0/1 reached terminal). The 15 s ballast attempt returns
`not_found`, with final status `error`. The retained cargo-only plan fails the
full checker with 100 background cell-guide securing violations and one
containership second-check violation. Its checker report is diagnostic only,
**not** a successful post-ballast submission or an approved quality result.
The raw/final plans, instance and checker JSON are saved alongside the summary;
source hashes agree before/after its own run; later mutable source changes mean
this diagnostic is not final-epoch qualification either. This seed was not manifest-selected and is
excluded from the rung table.

No current certified tiny optimum is available: the concurrent
[exact pinned summary](../exact-solver/fix-current-epoch/pinned-summary.json)
records zero independent physical optima and zero engine optima across three
tiny/six small attempts. The older paired-quiet run is explicitly pinned
to a prior rule epoch and its inputs lack current background/voyage contracts;
it cannot certify a current comparison. No numerical exact gap is claimed.

To reproduce a bounded scoped probe, complete generation before measurement:

```sh
export UV_CACHE_DIR=/private/tmp/twostep-fix-uv
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
uv run --offline --no-sync python experiments/twostep/fresh_instances.py \
  --rungs tiny small --count 1 --seed 530000 --generation-seconds 45 \
  --output experiments/twostep/reproduction/cohort
uv run --offline --no-sync python experiments/twostep/benchmark.py \
  --instance-root experiments/twostep/reproduction/cohort --rungs tiny small \
  --count 1 --plan-seconds 20 --ballast-seconds 8 --oracle-seconds 0 \
  --process-seconds 50 --output experiments/twostep/reproduction/results.json
uv run --offline --no-sync python experiments/twostep/summarize.py \
  --json experiments/twostep/reproduction/results.json
```

No cache warm, GPU, cloud call, test-shard opening or regulatory relaxation was
used. Scoped resource estimates/measurements are in
[compute.md](fix-current/compute.md); leader must mirror them into the shared
fleet history because that file is outside the write fence. Four evidence
regressions pass; the first two failed before the summary fix. Root owns the
current complete focused/dependent/full-suite results.

Task 4.8 evidence remains partial: freeze mutable producer/feasibility/checker
sources, generate and measure in that same epoch, obtain checker-approved ballast
departures, then collect terminal failure frequency
and a certified tiny comparison. Medium/full rungs were not attempted under
this order's explicitly bounded tiny/small campaign.

## Historical interim evidence (superseded; not current reproduction)

All sections below describe pre-fix experiments under older mutable epochs.
In particular, the former “canonical v3” tiny seed100000000 and medium
seed100200000 were rejected by the final manifest, and their original accepted
shard bytes/manifest were not retained here. They cannot be reproduced against
the final manifest. The preserved result JSON is historical metadata, not
eligible final-cohort evidence. The old commands, cache-warm request, runtime
claims, missing-epoch errors and test outcomes below apply only to that history.

The carrier builds a cargo-only pre-stow by category, filling compatible hold
cells with class-H cargo before using deck. The terminal assigns actual yard
boxes and searches their sequence while keeping those slots. The independent
checker after `planner.finalize` remains mandatory. No C2 screen is disabled.

### Historical final-ship measurement

The prescribed cache warm ran first (root measured 161.53 s, exit 2). Stability certificates were reused, but
all 15 production linear geometry hooks failed with **production linear policy
epoch is stale; requalify**. [Qualification metadata](../instances-v3/c2-qualification.json)
is recorded in measurement rows. This blocks a usable strict C2 completion model.

The interim v3 cohort then used the first training shard in manifest order
for each measured rung. Frozen 5.3 membership is checked before reading shard
bytes and manifest SHA256 is verified. No test bytes were opened. One case per
rung is a smoke cohort, not a population estimate; failed cases are retained.

| Rung / canonical train seed | Legal share | Checker pass / submitted | D11 weighted objective | Lifts by type | LB | Gap to LB | Planner s | Wall s |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| tiny / 100000000 | 0/1 | 0/0 | — | — | 0 | — | 12.408 | 25.599 |
| small / 100100000 | 0/1 | 0/0 | — | — | 0 | — | 13.840 | 18.372 |
| medium / 100200000 | 0/1 | 0/0 | — | — | 0 | — | 14.102 | 17.595 |

[Historical interim tiny raw row](v23-pilot/results.json),
[historical interim small/medium raw rows](v23-canonical/results.json).
Tiny exhausted its nominal 10 s cargo-only pre-stow search (12.408 s measured).
Small and medium returned a model-unavailable error with
`checker.missing_vessel_data` and the stale-policy message above.
No plan reached final checker or mask replay. Thus legal share is zero and
conditional checker pass rate is unmeasured (0 submissions), while end-to-end
checker-approved share is 0/1 per rung. Missing quality values are not zero-cost
solutions. Weighted D11 objective, all-loaded verdict, stability/strength tie
margins, lifts, and gap are unmeasured. Expected lift columns are load/discharge,
yard relocation, current restow/hatch, and projected restow/hatch. Reported gap
is `(objective-LB)/objective` on legal plans only. No terminal fixed-slot failure
share can be inferred because step 2 was never reached.

An earlier scoped diagnostic generated small/medium afresh while those v3
groups were unpublished. Its frozen splits were assigned before generation;
no test specifications were generated. Both were train (410000 /420000), with
0/1 legal and the same stale-policy error; times 15.262 /12.071 s, LB 6 /23.
[Scoped manifest](fresh-v3/manifest.json) and [rows](v23-fresh/results.json) are
retained for provenance; the canonical table above supersedes that cohort.

## Tiny independent optimum

The runner uses `checker_brute_force_certificate`: an independent physical
brute-force bound plus a full-checker-approved witness. Equality certifies the
weighted-lift optimum of the checker problem; it does not certify tertiary
margin optimality. This replaces the previous engine-restricted oracle.
Both the initial 10 s oracle and the 60 s independent follow-up returned LIMIT
with no retained completed search proof (reported nodes 0) and no witness. The
longer run took 60.860 s including startup, below its 75 s cap. Neither provides
a baseline-optimum difference or evidence for a joint advantage. The longer
probe is recorded under [v23-oracle](v23-oracle/results.json).

## Reproduce

```sh
export UV_CACHE_DIR="$PWD/.uv-cache"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 MKL_NUM_THREADS=1
uv run python -m stowage.cache_warm --workers 1
uv run python experiments/twostep/benchmark.py --instance-root experiments/instances-v3 \
  --rungs tiny --plan-seconds 10 --oracle-seconds 10 --process-seconds 40 \
  --output experiments/twostep/v23-pilot/results.json
uv run python experiments/twostep/benchmark.py --instance-root experiments/instances-v3 \
  --warm-status experiments/instances-v3/c2-qualification.json \
  --rungs small medium --plan-seconds 30 --oracle-seconds 0 --process-seconds 45 \
  --output experiments/twostep/v23-canonical/results.json
uv run python experiments/twostep/fresh_instances.py --rungs small medium --count 1 \
  --output experiments/twostep/fresh-v3
uv run python experiments/twostep/benchmark.py --instance-root experiments/twostep/fresh-v3 \
  --warm-status experiments/instances-v3/c2-qualification.json \
  --rungs small medium --plan-seconds 30 --ballast-seconds 5 --oracle-seconds 0 \
  --process-seconds 45 --output experiments/twostep/v23-fresh/results.json
uv run python experiments/twostep/benchmark.py --instance-root experiments/instances-v3 \
  --warm-status experiments/instances-v3/c2-qualification.json --rungs tiny --oracle-only \
  --oracle-seconds 60 --oracle-nodes 100000 --process-seconds 75 \
  --output experiments/twostep/v23-oracle/results.json
uv run python experiments/twostep/summarize.py experiments/twostep/v23-fresh/results.json
```

`--count N` selects the first N train then val shards in manifest order, checks
SHA256, and records instance identity, component versions, planner source hashes,
budgets, PIDs and checkpoints. Default root is `experiments/instances-v3`.
Every benchmark worker has an independent alarm; the parent records and reaps
its exact child PID on completion, timeout or interruption. Oracle/ballast public
watchdogs own and reap their recorded numerical children. No process-name
matching or broad process termination is used.

## Compute and verification

All numerical jobs use one core/CP worker, no GPU. The root holds the fleet
ledger umbrella reservation; benchmark and tests together remain within three
cores. Tiny pilot estimate <=40 s /0.5 GB; actual 25.599 s /474.97 MB. Fresh
small/medium estimate <=90 s /1 GB combined serial; actual 27.333 s, peak
477.70 MB. Canonical small/medium used the same <=90 s /1 GB estimate; actual
35.967 s total, peak 482.69 MB. Generation took 4.170 s small and 1.809 s medium. The independent
oracle follow-up has a 60 s numerical deadline and 75 s process cap.

Syntax compilation and runner `--help` passed; table generation was exercised
against historical and new rows. Root owns focused/full test verification.
Historical pre-KCS and pre-v2 JSON/archive rows are superseded by this section.
Task 4.8 remains partial pending a qualified production linear policy,
checker-approved fresh plans and an independent tiny optimality comparison.

Root validation: `UV_CACHE_DIR=$PWD/.uv-cache uv run pytest -q tests/solver/test_twostep.py tests/test_frozen_interfaces.py` passed after cache warm (25 baseline tests plus frozen-interface checks). Immutable own-run warm metadata: [v23-cache-warm-summary.json](v23-cache-warm-summary.json). Full-suite result will be appended after completion.

Timing columns distinguish planner time from total process wall time. Wall includes imports, loading, post-processing and (for the tiny pilot) its initial independent oracle probe. All current-run numerical children have recorded PIDs and completed/reaped normally; the full test session remains managed until completion.

## Final test and compute outcome

`UV_CACHE_DIR=$PWD/.uv-cache uv run pytest -q tests/solver/test_twostep.py tests/test_frozen_interfaces.py` passed, including all **25 baseline tests**. The default full suite
`uv run pytest -q` completed in **1173.96 s**, exit 1, with two failures outside
task 4.8:

- `tests/linear/test_epoch_promote.py::test_complete_epoch_keeps_station_reserves_and_holdout_evidence`
- `tests/linear/test_epoch_promote.py::test_retained_failure_enriches_only_calibration_and_new_seed_qualifies`

Both reported a boundary LIST builder-identity mismatch. The explicit rerun
`uv run pytest -q tests/linear/test_epoch_promote.py::test_complete_epoch_keeps_station_reserves_and_holdout_evidence tests/linear/test_epoch_promote.py::test_retained_failure_enriches_only_calibration_and_new_seed_qualifies`
passed both tests, exit 0. Full output: [pytest-v23-full.log](pytest-v23-full.log);
retry output: [pytest-v23-retry.log](pytest-v23-retry.log). The passing retry
does not constitute a clean new full-suite pass. Known import/fork deprecation
warnings originate outside the carrier change.

The umbrella estimate was25min, warm-up<=6min, and at most3CPU/4GB/noGPU.
Warm-up used one worker and took161.53s. Full-suite time exceeded the old989s
reference but fit the umbrella with overlapping single-core measurement. Actual
measurement peak RSS was482.69MB; full-suite/warm peak RAM was not measured.
All current-run children were recorded, completed/reaped; the reservation was
released and own history appended. No commits, paid calls, or test-shard tuning.

Proposed leader status: carrier E6/index implementation is complete; task4.8
evidence remains partial until production linear epoch requalification permits
strict C2, followed by a measured legal cohort and certified tiny optimum gap.
