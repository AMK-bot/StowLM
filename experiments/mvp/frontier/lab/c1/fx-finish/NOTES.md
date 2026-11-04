# Finish acceleration experiment

## Scope and assumptions

Only this folder is written. The source finisher and checker are imported,
never edited. `fast_finish.py` copies `Evaluator.evaluate` from
`src/stowage/solver/ballast_v4_evaluator.py`, retaining its selected-margin,
unknown-calculation, near-limit, exact-probe and complete-acceptance behavior.
The only scheduling change is part-major rejection order. The experimental
rectangular-liquid integral and content caches are enabled for search parts
only. Independent checking runs the ordinary public checker.

Assumptions: use stored complete V6 TRAIN stows as medium references; use the
fixed lab quick set and stored 0.9.0 loading orders for tiny/small. No cargo
construction improvement is claimed. `STOWAGE_BALLAST_V4=1` selects the legacy
freeze-OFF pathway in these runs; no activation or tolerance is changed. Its
three voyage states are not the activated per-lift pumping optimization. See
`REVIEW.md` for the intermediate-state requirements and network model.

The sandbox denies `nice`'s setpriority request. Every long launch attempts
`nice -n 5` and requests one numerical thread; the underlying Python command
still runs. Long map jobs were limited to two busy numerical processes across
this team. A coordination mistake launched duplicate short rectangular
selftests after the profile ended; they may briefly have overlapped the
baseline and exceeded that cap. Subsequent CPU launches were explicitly
serialized or split into two disjoint map lists. No other session's process
was controlled.

## Model and literature

The cargo operation graph is a state path; each tank adds a water-inventory
path through those states, with pump/transfer arcs and capacity bounds. Small
TRAIN has 19 operation states and 342 tank-state vertices; selected medium
TRAIN has 263 operation states and 4,734 tank-state vertices. Nonproductive
cargo-operation arcs, plus checker-projected future operations, define lifts.
All tanks at each state couple through nonlinear physical side constraints.

References reviewed (primary links and applicability in `REVIEW.md`): Ford and
Fulkerson (1958), https://doi.org/10.1287/opre.6.3.419; Griffith and Stewart
(1961), https://doi.org/10.1287/mnsc.7.4.379; Broyden (1965),
https://www.ams.org/mcom/1965-19-092/S0025-5718-1965-0198670-6/;
Kelley (1960), https://doi.org/10.1137/0108053; Huangfu and Hall (2018),
https://doi.org/10.1007/s12532-017-0130-5.

For the securing spring-network bound, also consulted Miroslav Fiedler and
Vlastimil Pták (1962), *On matrices with non-positive off-diagonal elements
and positive principal minors*, Czechoslovak Mathematical Journal 12(3),
382–400, https://dml.cz/handle/10338.dmlcz/100526. The experiment's specific
grounded-chain positivity argument is written out in `REVIEW.md`; no paper
code was downloaded or run.

The chosen implementation uses elementary exact integration: when a tilted
liquid surface cuts four vertical edges of a rectangular tank, its depth is
affine in x/y. Integrating that depth and its first moments replaces repeated
polyhedron clipping. The original geometric provider handles all other cases.
The source's original surface-inertia routine is retained. This is implemented
from the repository's geometry definition, not downloaded code.

## Measurement protocol

First: bounded 600-second cProfile on an existing V6 medium watchdog stow.
See `PROFILE.md` and `results/profile-baseline.*`. A profiling stop is unknown,
never infeasibility. Historical stored counters are labeled separately.

Second: compare native watchdog-bounded V4 and the replacement on identical raw
stows via `benchmark.py`, with fresh unchanged independent checks after all
hooks are restored. `check_floor.py` separately times the existing certified
TRAIN medium plan. It passed at 131 lifts in 30.590835 seconds; this is one
hardware/load observation, not a mathematical lower bound.

Third: use the shared `lab/score.py` after its README exists. Paired quick runs
use identical reference actions, one try, 120-second finisher budget and one
worker per run. The stock Frontier finishing wrapper and independent scorecard
both check successful plans; timing therefore includes repeated complete
checks, identically in both arms. Native finish-only timing is reported
separately to avoid confusing checker repetition with candidate-search cost.

Reason for any quick run exceeding 20 minutes: the fixed 21-map gate contains
8 tiny, 10 small and 3 medium cases, each requiring the unchanged complete
checker (including independent rechecking), plus up to 120 seconds of bounded
finishing. Retaining that complete predeclared set is necessary to detect lost
passes and avoid selecting only fast cases. No single map budget exceeds 120
seconds in the quick runs; the initial profile is bounded at 600 seconds.

## Results

Completed evidence on the certified medium reference:

| Certified medium raw stow, same 131 lifts | Finish s | Fresh checker s |
|---|---:|---:|
| Native V4 | 82.019 | 43.317 |
| Lashing-first/cache/narrowed proof | 71.908 | 44.217 |
| Inherited full check first | 39.423 | 35.567 |
| Final v3 inherited first, with securing bound | 34.798 | 32.034 |

This is `medium-feeder_500-f60-high-s192200000`, identical raw-plan SHA-256 in
all arms. The final finish-only gain is **2.36x**, not 10x; including
the fresh independent checker it is 1.88x. All arms report 131 lifts and pass
the unchanged full checker. Exact array-gather replacements were bit-identical
in 40 paired tests but 0.83x as fast, and section-polynomial integration passed
24 comparisons but was 0.40x as fast; both remain disabled. Rectangular kernels
passed 78 paired cases at 54.2x/11.5x, but the profiled medium has shaped tanks.

The narrowed proof prepass returned identical answers on two TRAIN medium
stows and two oversized-height perturbations: 41.243→0.539 s and 8.265→0.517 s
for the real stows, 58.671→0.286 s and 4.497→0.479 s for the perturbations.
These are prepass gains, not complete-finisher gains. The unchanged public
checker still evaluates every static rule, including E6, before acceptance.

Shared lab runner tests: 25 passed, exit 0. The real-checker illegal-plan
rejection test took 41 seconds. See `results/lab-tests.log`. No 10x end-to-end
claim is made from a kernel, a prepass, or historical timeout evidence.

## Main retained result: reject a proved impossible stow before searching

The TRAIN timeout reference is
`medium-feeder_800-f60-high-s192200100`, with 163 decision loads. Its original
V6 result hit 1200 seconds; the new current-source profile hit 600 seconds.
A fresh **unprofiled** native V4 run hit its owned watchdog at **120.916 s**.
With the identical raw-plan/input hashes and the same 120-second budget:

| Final plug-in | Result | Finish s | Earlier resolution |
|---|---|---:|---:|
| Normal accelerated search, with bound | `infeasible_proven` | 3.956 | 30.56x |
| Bound then inherited-first lane | `infeasible_proven` | 2.510 | **48.17x** |

These are complete finisher timings including worker startup/watchdog, rather
than bound-only timings. **No pass is counted for this stow.** Another cargo
assignment is not excluded. Artifacts are
`results/profile-v4-timeout-medium.json`, `profile-v3-timeout-medium.json`, and
`profile-v3-inherited-timeout-medium.json`, with retained outcomes/witnesses.

The graph is the checker's grounded spring chain for a container end frame.
The source motion formula has a minimum transverse acceleration at elevated
cargo CGs, independently of ballast. Its resulting minimum forces and exact
wind enter a stiffness matrix with nonpositive off-diagonals and positive
grounding. More force cannot reduce the anchor displacement/rod pull. Even
the minimum rod force is **314,496.483 N**, above its **212,500 N** limit:
ratio **1.479983449** at slots `0180582/0180584/0180586/0180588`.

The code verifies the ordinary matrix solve against an independent positive
closed-form chain-compliance calculation, checks equilibrium residuals and
rounds the bound down. It returns unknown if source, input or numerical guards
fail. The governing coefficients, minimum forces, wind, both stiffness cases,
residuals and source bindings are saved in the witness. See `REVIEW.md` for
the full source-level proof. A 128-case regression covers monotonicity,
closed-form agreement, endpoint CG fractions and invalid inputs. Maximum
relative rod difference was 3.17e-15 and residual 5.40e-15.

This is an outer necessary-condition contradiction, unlike infeasibility of a
local SLP. It changes no checker rule, tolerance, table or sampling policy.
The inherited-first lane checks this bound before its full-checker call;
ordinary search checks it in `fast_proof.py` before expensive physics.

## Fixed quick set: complete results

Same reference loading actions, seed 0, one try, 120-second finisher budget,
no construction/search budget. All reported passes go through the unchanged
full checker inside finishing and independently in the scorecard.

| Size | Maps | Native V4 passes | v3 passes | Mutual mean lifts, V4 / v3 | Mean wall s, V4 / v3 |
|---|---:|---:|---:|---:|---:|
| Tiny | 8 | 8 | 8 | 6.375 / 6.375 | 103.299 / 60.968 |
| Small | 10 | 8 | **9** | 14.250 / 14.250 (8 pairs) | 97.555 / 85.538 |
| Medium | 3 | 1 | 1 | 131 / 131 (1 pair) | 64.186 / 54.764 |

Against stored **0.9.0**, v3 is 8/8 versus 8/8 on tiny, paired mean
6.375/6.375; **9/10 versus 9/10 on small**, paired mean
**13.8889/13.8889** across all 9 passing pairs. There is no measured 0.9.0
medium row, so no invented medium comparison. Full-planner 0.9.0 wall time is
not compared with these reference-stow finishing times.

`random.small.40100036.0.2` changes from a V4 finisher timeout to a passing
11-lift plan. No V4 pass is lost. Every mutual pass has identical actions and
lift count, and every input/proposal-method binding matches. The medium
feeder800 quick reference still times out in both arms. The D185 quick case
has no complete V6 reference plan and reports a method error; it is not a
measured finisher failure or an infeasibility proof. The stored failing small
case `sim.small.40100034` supplies no candidate in either arm.

Evidence: `results/quick-v4/summary.json`, `results/quick-fast/summary.json`,
per-map retained plans, and `results/paired-quick.json`. The final full quick
scorecard invocation resumed all 21 matching rows. Two disjoint lists,
`quick-a.txt` and `quick-b.txt`, used one worker each to stay within the CPU
limit; together they are exactly the fixed quick set.

### Timing and source-binding qualifications

This is a shared machine and a single observation per arm, with warm workers;
the timings are not controlled latency estimates. In particular tiny direct
checking follows the same numerical path, so its wall-time difference is not
evidence for the new kernels. The huge failed-stow reduction comes from the
proved contradiction replacing a search timeout; successful-plan 10x speed
has **not** been achieved.

The shared lab builder changed `catalog.py`, `summary.py` and `score.py` after
the baseline began (latest at 03:45), changing the aggregate runner hash from
`e06f4ba2...` to `54c4a017...`. This is explicitly recorded, not suppressed,
in `paired-quick.json`. Native checker/solver/frontier sources all predate the
task; the latest native mtime is 2026-10-31. A 236-file hash manifest was
checked unchanged at final verification. Direct v3 watchdog benchmarks retain
stable complete experimental helper bindings throughout each run. The quick
rows retain matching input hashes/proposal method and identical mutual-pass
actions, but are not advertised as identical aggregate-runner-hash trials.

Final source/compile evidence: `results/native-source-manifest.json` and
`results/final-verification.json`. No `src/`, params, shared lab code or other
executive's files were edited. No sealed evaluation inputs were opened.

## Entry, verdict and next step

**Keep part:** the spring-chain securing certificate and inherited-ballast
first-check lane. They resolve the measured impossible medium stow 48x earlier,
retain the certified medium pass and rescue one small quick timeout. Keep the
complete checker as the acceptance gate. The slower optional shaped kernels
stay off; isolated rectangular speedups do not establish a medium speedup.

Entry: `fast_finish.py:finish_inherited`; supporting proof:
`early_bound.py:securing_bound`. Both are confined to this folder. Reproduce:

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab \
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/score.py \
  --set quick --method experiments/mvp/frontier/lab/c1/fx-finish/method.py:propose \
  --finish experiments/mvp/frontier/lab/c1/fx-finish/fast_finish.py:finish_inherited \
  --budget-s 0 --finalize-s 120 --tries 1 --workers 2 \
  --out experiments/mvp/frontier/lab/c1/fx-finish/results/quick-fast
```

Scope remains legacy V4 with rule freeze OFF. Activated per-lift ballast and
intermediate-port checking are unchanged and have not been accelerated here.
Next: guarded Broyden Jacobian reuse, refreshing on active-set and slack/full
regime changes, to remove the observed expensive selected-margin derivatives.
Every proposed step and final plan must still receive exact checking.
