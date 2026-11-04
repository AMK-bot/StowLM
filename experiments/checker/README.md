# Full checker throughput

Run from the repository root, with Python 3.12 through uv:

```sh
UV_CACHE_DIR=/private/tmp/stow-checker-uv-cache uv run --offline --no-sync python experiments/checker/benchmark_full.py --repeats 1 --profile
UV_CACHE_DIR=/private/tmp/stow-checker-uv-cache uv run --offline --no-sync python experiments/checker/benchmark_full.py --sizes feeder_500 feeder_1000 feeder_1700 --repeats 1 --output experiments/checker/timings-fleet.json
```

The script pins BLAS/OpenMP to one thread and runs each size sequentially in a
fresh process. Cold means the first `check` call after constructing the inputs;
warm means a subsequent call in that process. Imports, input validation, station
construction and tank-table setup are excluded and recorded separately. Each
full report must compare exactly equal across repeated calls, including the
additional profiled call. Raw JSON records versions, part completion and rules.
These are one-sample smoke measurements on a shared machine, not statistical
latency guarantees. Profiling adds substantial instrumentation overhead.

APPROXIMATION: every twenty-foot cell contains one synthetic empty dry20 box
of 2,400 kg, already aboard, with an empty move list. This deliberately measures
physics and full-condition validation rather than crane replay throughput. It
does not represent a checker-approved operational plan; failing rule IDs remain
visible. The four decision-region presets retain task 3.5's archived synthetic hull,
tanks, fixed loads and consumables. Archived stations were empty, so the harness
derives current synthetic S11A permissible stations with the production helper.
No vessel hull construction is charged to the checker timing.

APPROXIMATION: the three whole-fleet conditions use the complete current fleet
cell geometry, lightship, bridge, tank geometry and synthetic strength limits.
All tanks are present and empty. A synthetic fixed background mass at z=3 m
balances longitudinal moment at design displacement, accounting for the loaded
boxes. Hydrostatic/cross-curve tables are empty: these exact checker parts
integrate the declared hull offsets directly and do not read those tables.
This avoids timing expensive table construction without replacing any checker
calculation. The background mass is an explicit artificial loading condition,
not a distribution calibrated to a real cargo ship.

On 2026-10-27, the archived feeder_500 presets measured:

| Decision region | Boxes | Cold s | Warm s | All parts ran |
| --- | ---: | ---: | ---: | --- |
| tiny | 6 | 6.517 | 6.056 | No: free-trim solve at 89.99° |
| small | 84 | 6.544 | 5.337 | No: free-trim solve at 89.99° |
| medium | 252 | 8.330 | 7.658 | Yes |
| full | 504 | 9.764 | 9.251 | Yes |

Setup ranged from 0.090 to 0.203 s. The full profile took 10.98 s instrumented:
`visibility._loading_condition` ran twice, totaling 8.68 s (79%); visibility
and lashing took 4.38/4.46 s, stability 1.82 s. Hull polygon clipping and
waterline intersection dominated the repeated equilibrium solves. Strength's
warm geometric evaluations already benefit from its bounded submerged cache.
The <1 s target is blocked inside numerical parts, even before replaying lifts.

The balanced whole-fleet conditions (all seven parts ran, exact repeat equality)
measured:

| Vessel | Boxes | Cold s | Warm s | Setup s |
| --- | ---: | ---: | ---: | ---: |
| feeder_500 | 504 | 4.085 | 3.415 | 0.236 |
| feeder_1000 | 1,008 | 3.927 | 3.193 | 0.311 |
| feeder_1700 | 1,720 | 5.911 | 4.095 | 0.597 |

Every condition failed safety rules, as expected for this artificial cargo
layout/background; `passed=False` is preserved in raw results. The full archived
condition's slack consumables account for substantial extra numerical work
compared with the empty-tank balanced conditions.
In particular, `checker.missing_vessel_data` hard violations remain even in
rows where every part ran: part completion does not imply every required rule
parameter is available. These timings are not feasibility certificates.

Owners should share a condition-scoped immutable equilibrium result between
visibility and lashing, and optimize/vectorize polygon clipping without changing
quadrature or tolerances. Context/cache keys must include vessel identity,
component versions, actual geometry/parameters and all condition inputs; same
IDs can refer to edited vessels. Full verdict caching or monkeypatching a shared
helper would risk stale results and is not used. Stability also rereads/reparses
fleet TOML on each call, a smaller opportunity than the duplicate equilibrium.

Compute: numerical jobs used one thread, no GPU, estimated below 1 GB and each
bounded below a few minutes. RAM peak was not measured. `ps` is denied by the
sandbox; no processes were killed. The four-preset run took roughly two minutes
including instrumented profiles; the three-fleet run takes under one minute.

## Post-memo follow-up

After the task 2.1 exact-input equilibrium/KN memo landed, the command below
produced `timings-fleet-memo.json` and the `profile-feeder_*.{prof,txt}` files:

```sh
UV_CACHE_DIR=/private/tmp/stow-checker-uv-cache uv run --offline --no-sync python experiments/checker/benchmark_full.py --sizes feeder_500 feeder_1000 feeder_1700 --repeats 1 --profile --output experiments/checker/timings-fleet-memo.json
```

| Vessel | Boxes | Cold s | Warm s |
| --- | ---: | ---: | ---: |
| feeder_500 | 504 | 1.613 | 1.296 |
| feeder_1000 | 1,008 | 1.600 | 1.266 |
| feeder_1700 | 1,720 | 1.983 | 1.558 |

These are shared-machine smoke timings, with the same synthetic loading
conditions described above. All seven parts ran; reports were exactly equal
across cold, warm and profiled calls, and retained `passed=False`. Each cold call
recorded three solver misses and three hits; each subsequent full check added
six hits and zero misses. KN cache counters stayed zero because this checker
uses the separate `StabilityGeometry.free_trim_curve` path. The harness now
explicitly clears numerical result caches after setup and records source hashes
for hydrostatics and all four numerical checker parts. The raw run records
checker version 1.0, before the coordinated 1.1 version bump; the source hashes
identify the implementations independently of that bump. Hydrostatics,
stability and visibility changed during this follow-up; strength and lashing
review fixes were still pending at measurement time. Remeasure after those land
before treating this as the final reviewed-physics performance result.

The warm feeder_500 profile took 1.707 s with instrumentation. Stability used
1.285 s (75%); 14 `free_trim_curve` calls used 0.975 s and 782 batched submerged
evaluations used 0.903 s. `stability.py:183` computes the main curve and
`stability.py:195` recomputes single-angle curves for criterion roots, both via
`stability_geometry.py:73`. These custom nonlinear solves bypass the base
HullGeometry solve/KN caches and are the remaining blocker to the <1 s target.
Parameter-file reparsing in `stability._context` took another 0.110 s.

Visibility and lashing still call `visibility._loading_condition` separately
(`visibility.py:193` and `lashing.py:131`), but their equilibrium calls at
`visibility.py:146` now hit the shared memo. The two helper calls together used
only 0.016 s. All six base `_solve` calls together used 0.00038 s; there were
zero `_solve_uncached` calls in the warm profile. Visibility/lashing whole-part
times were 0.141/0.063 s. Their previous duplicate numerical solve bottleneck is
resolved; a shared condition object is no longer necessary for this target.

Two sequential three-fleet runs used one numerical thread, no GPU, estimated
under 1 GB and under two minutes each; each completed below one minute. Peak
RAM was not measured. No heavy-job reservation or process termination occurred.

### Current partial review snapshot

A subsequent run after lashing hash `138a243acf0260ae...` landed is preserved
separately as `timings-fleet-review-partial.json`; the earlier memo JSON remains
unchanged. Same command as above, with `--output
experiments/checker/timings-fleet-review-partial.json`. The `profile-feeder_*`
files now describe this latest run. Strength hash `b4d53732cdabb35c...` remained
unchanged; this is still a partial review snapshot, recording checker 1.0.

| Vessel | Cold s | Warm s |
| --- | ---: | ---: |
| feeder_500 | 1.537 | 1.294 |
| feeder_1000 | 1.961 | 1.673 |
| feeder_1700 | 2.333 | 2.030 |

All seven parts ran without part errors, reports matched exactly across all
three checks per vessel, and every report remained `passed=False`. The updated
lashing also rejects the feeder_500 condition under the IRS securing rule.
Cache counts remain three misses total and six additional hits per warm check,
with no KN calls. This is not an isolated lashing speed comparison: hydrostatics
and stability also changed hashes, and shared-machine timings vary.

The new feeder_500 profile took 1.988 s. Stability was 1.060 s (53%), including
14 free-trim calls at 0.800 s; lashing was 0.471 s (24%). Lashing's
`_check_lashing` (`lashing.py:279`) constructs 2,046 violation objects via
`problem` (`lashing.py:282`), consuming 0.256 s, including identifier validation.
Visibility was 0.116 s, and its two shared loading-condition helper calls still
totalled only 0.016 s. No uncached base equilibrium solve occurred. Stability
custom free-trim solves remain the main numerical target; the artificial failing
condition's extensive lashing diagnostics also affect throughput. No physics or
diagnostic result was removed for speed.

This final sequential run also completed below one minute, one numerical thread,
no GPU, estimated below 1 GB; peak RAM unmeasured.

## Reviewed KCS baseline (checker 1.1)

The 2026-10-27 reviewed-physics baseline uses the current checksum-pinned KCS
parameters, including the pruned hold/tank geometry, downflooding openings,
D21 containership stability criteria and D22 strength limits. It does not reuse
the archived task 3.5 analytic hull. These measurements therefore supersede the
old fleet timings above; changes in hull detail and loading geometry prevent a
controlled speed comparison.

```sh
UV_CACHE_DIR=/private/tmp/stow-checker-uv-cache uv run --offline --no-sync python experiments/checker/benchmark_full.py --sizes feeder_500 feeder_1000 feeder_1700 --repeats 1 --profile --output experiments/checker/timings-kcs-reviewed-baseline.json
```

| Vessel | Actual 20-foot cells/boxes | Cold s | Warm s | Setup excluded s |
| --- | ---: | ---: | ---: | ---: |
| feeder_500 | 440 | 17.527 | 12.027 | 0.634 |
| feeder_1000 | 840 | 21.375 | 19.008 | 0.977 |
| feeder_1700 | 1,432 | 27.848 | 19.222 | 1.348 |

All seven parts ran without errors. Full reports were exactly equal between
cold, warm and profiled calls, with `passed=False` retained. The raw JSON records
source hashes, rule IDs, component versions and cache counters. All three report
`geometry_table_used=0`: qualified stability acceleration was still being built.
Three initial equilibrium misses became six additional hits per warm check;
visibility and lashing reuse the memo rather than repeating the numerical solve.

The feeder_500 warm profile took 14.136 s with instrumentation, of which stability
used 13.295 s. Its 18 `StabilityGeometry.free_trim_curve` calls took 12.851 s;
488 batched polygon integrations took 12.713 s. Lashing took 0.342 s and visibility
0.204 s. The detailed KCS direct free-trim calculation remains the main blocker
for the <1 s target pending independent qualification of the table fast path.
Baseline-specific profile filenames are linked in the JSON.

APPROXIMATION: the benchmark still uses synthetic 2,400 kg dry boxes in every
20-foot cell, empty tanks and an artificial design-displacement background mass
at z=3 m. It is a throughput fixture, not a feasible operational plan. The baseline
projects current production hull fields and computes current production strength
stations but omits model-only hydrostatic/cross-curve/Bonjean tables; checker
parts do not read those tables. `STOWAGE_BENCH_PRODUCTION_MODEL=1` instead loads
the complete production `load_vessel(name, with_hull=True)` model. Its expensive
assembly remains excluded and separately recorded. Production model cache
construction was already running in the checker/section-5 validation processes,
so the baseline avoided redundant table builds.

Compute: one numerical thread, no GPU, estimated below 1 GB and <3 minutes;
the nine baseline checks plus profiles consumed about 180 seconds, with setup
and import overhead additional. Peak RAM was not measured. The root executive's
aggregate checker reservation covered the run. Shared-machine contention means
these are single-sample smoke timings, not latency guarantees.

### Complete production-model direct run

Once all current-version whole-vessel caches were available, the complete
production model was checked with the same artificial loading condition:

```sh
STOWAGE_BENCH_PRODUCTION_MODEL=1 UV_CACHE_DIR=/private/tmp/stow-checker-uv-cache uv run --offline --no-sync python experiments/checker/benchmark_full.py --sizes feeder_500 feeder_1000 feeder_1700 --repeats 1 --profile --output experiments/checker/timings-kcs-reviewed-production.json
```

| Vessel | Cold s | Warm s | Setup excluded s |
| --- | ---: | ---: | ---: |
| feeder_500 | 11.356 | 11.139 | 0.413 |
| feeder_1000 | 17.500 | 13.398 | 0.875 |
| feeder_1700 | 11.322 | 10.869 | 0.395 |

See raw JSON for exact setup values. All seven parts ran without errors; cold,
warm and profiled reports matched exactly. A separate untimed full check also
confirmed exact equality of the complete production model and the earlier
parameter projection for every vessel. No checker part depended on the omitted
model-only tables. All three still report `geometry_table_used=0` and
`passed=False`. Source hashes were stable throughout each vessel's timed calls,
profile and additional equality validation; the raw records include all checker
parts and their geometry/criteria/loading helpers. Stability and table source
hashes changed since the baseline, so the difference between runs cannot be
attributed solely to contention or model assembly.

The production feeder_500 profile spent 11.503 s in stability; lashing used
0.317 s, visibility 0.192 s and strength 0.046 s. Visibility's shared
`_loading_condition` helper was called twice (from `visibility.py:193` and
`lashing.py:296`) for 0.024 s total. Its equilibrium call at `visibility.py:146`
hit the memo: six base `_solve` calls took 0.001 s, with no uncached solve.
The remaining performance block is stability's direct KCS free-trim curve,
not repeated visibility/lashing equilibrium. Full production and parameter-only
models produce identical reports in this condition.

Compute: this sequential run used one numerical thread, no GPU, estimated below
1 GB and roughly three minutes, under the root's aggregate checker reservation.
It additionally ran one equality check per vessel outside all latency figures.
The summed setup, timed checks, profiles and extra validation took 159.3 s;
fresh-process import overhead was additional.
Peak RAM was not measured. Single-sample shared-machine timing remains noisy;
no throughput claim should use differences of a few seconds as causal evidence.

### Final qualified-table measurements (current reviewed source)

These final runs supersede the earlier direct/initial-dispatch snapshots. All
production vessels load through `load_vessel(name, with_hull=True)`. Checker
version is 1.1; all six sea/fresh-water qualification certificates match the
current component versions (`table-qualification-snapshot.json`). Stability
source `c84717771d1f7bef...` refines a near-limit floating condition directly
while retaining the qualified finite-angle curve. Earlier source rechecked
whole curves near a draft limit: those historical fallback timings remain
preserved in `timings-kcs-reviewed-qualified.json`.

| Condition | Vessel | Boxes | Cold s | Warm median s | Table used |
| --- | --- | ---: | ---: | ---: | --- |
| All cells, balanced background | feeder_500 | 440 | 4.246 | 0.609 | Yes |
| All cells, balanced background | feeder_1000 | 840 | 2.046 | 0.497 | Yes |
| All cells, balanced background | feeder_1700 | 1432 | 4.399 | 0.617 | Yes |
| Homogeneous, no balancing | feeder_500 | 224 | 1.182 | 0.146 | Yes |
| Homogeneous, no balancing | feeder_1000 | 392 | 1.313 | 0.141 | Yes |
| Homogeneous, no balancing | feeder_1700 | 744 | 1.261 | 0.224 | Yes |

Cold excludes separately recorded input assembly and clears numerical result
caches after setup. Warm means the same condition checked again. The all-cell
run has one warm sample; homogeneous has two. All seven parts ran without
errors; reports matched exactly across cold/warm/profile calls and all
source hashes remained stable. All-cell production/projection reports also
matched exactly. All reported `passed=False`: these loading benchmarks fail
safety rules and are not approved operational plans.

The homogeneous condition uses task 3.1 `kcs_departures.build_departure`: all
holds and the lowest two deck tiers, identical nonempty standard 20ft boxes,
90% upright design displacement, zero tank contents, unmodified production
lightship/fixed loads and no synthetic balancing moment. Its plan now names
only adjustable ballast tanks, preserving fixed tank contents; no
`checker.invalid_ballast` remains. The earlier homogeneous snapshot named
fixed tanks too and is superseded for that reason.

Reproduce final all-cell timing with the preceding production command using
`--output experiments/checker/timings-kcs-reviewed-final-allcells.json`; add
`STOWAGE_BENCH_LOADING=homogeneous --repeats 2` (the repeat flag is a command
argument, not an environment variable) and use the final-homogeneous output
path for the ordinary homogeneous condition.

Shared-machine load fell from roughly 22 to 5 during this work. Differences
between these smoke runs include contention and source changes; they are not
controlled estimates of isolated component speedups. Every job used one
numerical thread, no GPU, estimated below 1 GB; peak RAM was not measured.


### Warm geometry, novel condition (rollout-like latency)

`uv run --offline --no-sync python experiments/checker/benchmark_fresh_conditions.py`
warms each ordinary homogeneous departure, then deep-copies it and increases
one nonempty box VGM by exactly 1 kg (remaining below 30,480 kg). No caches
are cleared. Only the new-condition check is timed below. This avoids
confusing identical-condition memo hits with a new rollout.

| Vessel | New condition s | New base equilibrium misses | Table used |
| --- | ---: | ---: | --- |
| feeder_500 | 0.619 | 2 | Yes |
| feeder_1000 | 1.047 | 3 | Yes |
| feeder_1700 | 0.967 | 2 | Yes |

All seven parts ran without errors and all source hashes stayed stable.
Every stability displacement increased by exactly 1 kg. Checker version is
1.1; no invalid-ballast error remains. All plans still fail unrelated hard
rules, so these are throughput measurements, not feasibility certificates.

The supplementary cold feeder_500 profile (`profile-kcs-homogeneous-cold-
feeder_500.txt`) took 2.519 s with instrumentation. First-use table metadata
bilateral-symmetry validation used 1.232 s; two genuine base equilibrium
misses used 0.434 s. Warm visibility/lashing use cached identical-equilibrium
results: the final homogeneous feeder_1700 profile has no uncached solve.
First-check latency still exceeds 1 s. Repeated-condition samples are below
1 s; warmed new-condition latency is below 1 s for feeder_500/1700 and 1.047 s
for feeder_1000. The novel-rollout target remains unmet for that sample. This
small set does not establish a bound over random rollouts.

Final reproducible commands (one numerical thread is enforced inside the harness):

```sh
STOWAGE_BENCH_PRODUCTION_MODEL=1 UV_CACHE_DIR=/private/tmp/stow-checker-uv-cache uv run --offline --no-sync python experiments/checker/benchmark_full.py --sizes feeder_500 feeder_1000 feeder_1700 --repeats 1 --profile --output experiments/checker/timings-kcs-reviewed-final-allcells.json
STOWAGE_BENCH_LOADING=homogeneous STOWAGE_BENCH_PRODUCTION_MODEL=1 UV_CACHE_DIR=/private/tmp/stow-checker-uv-cache uv run --offline --no-sync python experiments/checker/benchmark_full.py --sizes feeder_500 feeder_1000 feeder_1700 --repeats 2 --profile --output experiments/checker/timings-kcs-reviewed-final-homogeneous.json
UV_CACHE_DIR=/private/tmp/stow-checker-uv-cache uv run --offline --no-sync python experiments/checker/benchmark_fresh_conditions.py
```

Compute accounting (all jobs one numerical thread, estimated <1 GB, no GPU;
actual is summed setup/check/profile/extra-validation time, imports additional):

| Run | Estimate | Measured seconds |
| --- | ---: | ---: |
| KCS parameter-projection baseline | <3 min | about 180 |
| Complete production direct | about 3 min | 159.3 |
| Initial qualified dispatch, before loading-boundary refinement | <2 min | 217.8 |
| Initial homogeneous dispatch | <1 min | 63.3 |
| Final all-cell dispatch | <1 min | 20.3 |
| Final homogeneous dispatch | <1 min | 6.0 |
| Novel condition before ballast-plan correction | <1 min | 17.7 |
| Novel condition after correction | <1 min | 9.1 |

The initial qualified-dispatch estimate missed because loading-boundary guards
restarted the full direct curve; those guards were refined by the stability
owner, without relaxing hard limits. No processes were killed. Heavy runs used
the root executive's aggregate checker reservation. Peak RAM was not measured.


## Checker 1.2 / schema 1.3 production remeasurement

Current command (one numerical thread is enforced inside the harness):

```sh
STOWAGE_BENCH_LOADING=homogeneous STOWAGE_BENCH_PRODUCTION_MODEL=1 UV_CACHE_DIR=/private/tmp/stow-checker-uv-cache uv run --offline --no-sync python experiments/checker/benchmark_full.py --sizes feeder_500 feeder_1000 feeder_1700 --repeats 2 --profile --output experiments/checker/v12-timings-homogeneous.json
```

The harness now owns a schema-current equivalent of the stability owner's
`kcs_departures.build_departure`: production geometry, lightship and fixed loads
are unmodified; `FixedLoad.mass_kg` is accessed by name and records serialize by
named model fields. All holds plus the lowest two deck tiers carry identical
20ft dry boxes, reaching 90% of upright design displacement in port water.
ASSUMPTION: empty tanks, no voyage consumables, no moves, no artificial balancing
mass in this homogeneous benchmark. This condition measures throughput and
is not an operational voyage approval; individual checker verdicts remain recorded. Prior reports' vessel cell counts and loading
geometry are historical and should not be compared as identical input latency.

Each vessel runs in its own process; imports, complete production vessel setup
and instance validation are excluded from checks. Numerical equilibrium caches
are cleared after setup. Cold is the first full check; warm is the median of
two identical subsequent checks. The additional instrumented check is excluded
from timings. Reports must be exactly identical across cold, both warm calls
and the profile. The JSON records source hashes before and after, component
versions, cache counters, cache environment, loading details, pass/fail and part
errors. The supplementary novel-condition script accepts `--sizes` and `--output`
so new runs can retain versioned artifacts; it was not rerun for this request.

The initial run crossed an unrelated ballast version bump (2.0→2.1), so its
`feeder_1000` source-stability flag is false. The final artifact merges fresh
stable 500/1000 rows from `v12-timings-final-500-1000.json` with the already stable
post-bump 1700 row, all recording ballast 2.1:

```sh
STOWAGE_BENCH_LOADING=homogeneous STOWAGE_BENCH_PRODUCTION_MODEL=1 UV_CACHE_DIR=/private/tmp/stow-checker-uv-cache uv run --offline --no-sync python experiments/checker/benchmark_full.py --sizes feeder_500 feeder_1000 --repeats 2 --profile --output experiments/checker/v12-timings-final-500-1000.json
```

See `v12-timing-summary.md` and `v12-timings-final.json` for the measured results
and performance blockers.


The unused all-cell balanced alternative was subsequently migrated to named
`FixedLoad(kind='cargo')` with an explicitly APPROXIMATION submerged cargo
envelope (z=2–4 m), matching the current schema's cargo geometry contract.
Its concentrated mass and moments are retained; the envelope is not a
reconstruction of omitted stacks. This alternative has not been remeasured.
The production homogeneous branch and all checker 1.2 timings above are
unchanged; saved source hashes identify the harness revision actually measured.


## Checker 1.3 follow-up

Current 516 / 816 / 1,604-cell production ships, post-cache_warm timings, method .1 half-tank measurements, full-suite results and owner triage are recorded in [v13-validation-report.md](v13-validation-report.md). This supersedes the older-version timings above for current-code performance.
