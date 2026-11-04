# V4 finisher cost audit

The profiling case is the permitted TRAIN map
`medium-feeder_800-f60-high-s192200100`, with 163 load-list containers. Input:
`experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_800/f60-high/seed-192200100.json`.
The exact V6 constructor result is
`experiments/mvp/scale/runs/v6/maps/medium-feeder_800-f60-high-s192200100/raw.json`.
`profile_baseline.py:load_stored` resolves only manifest-listed TRAIN maps and
checks their input SHA-256. No sealed inputs were opened.

The object being optimized is a fixed cargo-stow graph plus a sequence of tank
fill vectors. Cargo slots are vertices, supports/access restrictions are edges,
and each move is a crane action; the finisher searches continuous liquid fills
while preserving that graph and all exact checker constraints. The profiled
legacy V4 lane samples departure, midpoint and arrival, rather than solving a
time-expanded pump/crane network.

## Stored timeout evidence (historical, not a new run)

The V6 summary records a 1200.052 s watchdog failure for this raw plan. Its last
completed telemetry checkpoint at 1191.158 s records:

| Work | Completed calls | Seconds |
|---|---:|---:|
| Candidate vectors | 143 | — |
| Full candidate evaluations | 71 | — |
| Selected-margin evaluations | 100 | — |
| Cheap exact loading probes | 339 | 326.476 |
| Public physics parts | 178 | 778.319 |
| Full checker | 0 | 0 |
| Visibility/lashing loading misses | 116 | 718.450 |
| Context setup | — | 3.883 |

The historical V6 already uses exact numerical caching. Its 62 shared loading
cache hits avoided an additional estimated 221.176 s. Geometry construction was
small: tank construction 3.426 s and cargo construction 5.603 s. This identifies
repeated equilibrium/liquid calculations, not LP or full-checker calls, as the
historical dominant cost. Timing counters include only completed calls.

## New measurement protocol

The current, unmodified `src/stowage/solver/ballast_v4.py:finish` is invoked
in-process under cProfile, allowing native Python hydrostatics and search to
appear in one profile. A POSIX alarm stops the measurement at 600 s even inside
opaque work. This is a profiling stop and cannot establish infeasibility.
`Evaluator._part` is temporarily wrapped for timing only; its original function
always executes. Full-checker acceptance remains unchanged.

One numerical thread is requested for OpenMP, OpenBLAS and Accelerate. The
`nice -n 5` command was attempted but this sandbox denied `setpriority`; the
Python process continued. Only one CPU-busy process belongs to this profiling
task. There are no spawned workers.

Artifacts: `results/profile-baseline.json`, `.progress.json`, `.txt` and `.pstats`.
The 0.1-second smoke test verified manifest/hash loading and alarm cleanup.
No passing plan, lift result or speedup follows from this profiling smoke test.

## Current-source 600-second result

The measurement ended at **600.052 s**, interrupted during a departure securing
call, without acceptance. It tried 59 tank vectors: 18 full candidates and 41
selected-margin evaluations. Completed telemetry contains 72 cheap probes
(25.848 s), 70 physics parts (478.888 s), and zero full-checker calls. cProfile
records 147.4 million function calls, so these are instrumented wall times,
not an unprofiled speed benchmark.

| cProfile cumulative work | Calls | Seconds |
|---|---:|---:|
| Shared visibility/securing `_loading_condition` | 47 | 356.141 |
| Securing `check_lashing` | 35 | 297.674 |
| Hull `_solve` | 317 | 240.430 |
| Hull section-liquid `liquid` | 9,203 | 145.777 |
| Visibility `check_visibility` | 12 | 102.079 |
| Strength `check_strength` | 12 | 74.272 |
| Existing `prove_infeasible` | 1 | 68.331 |
| E6 static departure check, nested in proof | 1 | 62.096 |
| E6 compatible-placement checks | 150 | 61.542 |
| Stability `check_stability` | 12 | 23.534 |
| Validated move replay | 1 | 0.037 |

Cumulative rows overlap and must not be added. The loading-condition row alone
is 59.4% of the measured budget. One-time setup includes the expensive existing
proof's full static scan, mostly E6 alternative hold-slot tests; it is not move
replay or ballast LP work. Only two LP inputs were parsed; the HiGHS option
wrapper consumed 2.785 s cumulative (under 0.5% of the budget). Replacing the
LP with network simplex cannot remove the observed dominant work.

Finite-difference securing derivatives increasingly dominate after seed
screening. Before the derivative phase, V4 also computes stability and strength
for tank fills which securing then rejects. Exact arithmetic reuse, analytic
rectangular-liquid integration and earlier securing evaluation therefore target
measured work. None can remove the required unchanged full-checker gate on a
passing plan.

## Analytic kernel regression

`fast_finish.py --selftest` passed 40 interior-slice cases, 38 general root
cases and 202 fallback cases. Maximum absolute differences versus the original
liquid helper were 1.78e-15 m in centroid coordinates, 2.20e-12 m in plane level,
5.06e-11 m² in area and 3.69e-13 m⁴ in free-surface inertia. The isolated
interior-slice calculation was 54.16x faster and the general rectangular root
11.52x faster in this run. These are kernel timings, not end-to-end speedups.
The selftest also verifies restoration of original helpers outside part probes.
Raw output: `results/profile-kernel-selftest.log`.

## Optional securing necessary bound

`early_bound.securing_bound` took 2.691 s on the same timed-out feeder800 stow
(431 physical boxes including background cargo), plus 0.042 s for move replay.
It returned **bounded, no contradiction**. Lower ratios were corner-post
0.384, fitting 0.383, cell-guide 0.076 and longitudinal racking 0.529. The
original unmodified securing checker took 5.355 s and returned corresponding
actual ratios 1.433, 2.236, 0.272 and 1.136. Every corresponding lower bound
was below the actual value, as required. This one-point numerical comparison
does not replace the source-based monotonicity argument in REVIEW.md and does
not prove the failed stow infeasible. The bound did not rescue this timeout.

Artifacts: `results/profile-bound-probe.json` and
`results/profile-bound-probe.lashing.json`; primary activation was not changed
by the profiler.

## Source proof scan equivalence and certified medium benchmark

The narrowed `fast_proof` matched the original proof on four cases: both exact
TRAIN raw stows return unknown, and in-memory 100 m container-height
perturbations on both stows return the identical `expert.physical.tier_height`
contradiction string. These perturbations are diagnostic copies, never edited
inputs or passing plans. Feeder800 raw proof time fell 41.243 → 0.539 s;
its height-violation proof fell 58.671 → 0.286 s. Feeder500 raw times were
8.265 → 0.517 s, and perturbed times 4.497 → 0.479 s. This comparison exercises
the positive certificate branch as well as unknown results. Artifact:
`results/profile-proof-equivalence.json`.

On the identical certified feeder500 high-mass seed192200000 raw plan:

| Finisher | Finish s | Independent full check s | Pass | Lifts |
|---|---:|---:|---|---:|
| Native V4 | 82.019 | 43.317 | yes | 131 |
| Experimental v2, shaped kernels off | 71.908 | 44.217 | yes | 131 |

This is **1.14x finishing speed**, far below the 10x target. Both accepted the
inherited recipe as their first candidate and retained the unchanged internal
full-checker gate plus a separate unchanged check after restoring all hooks.
The v2 source hashes were stable during its run. Native V4's experimental-source
stability field was false because files it never imports changed concurrently;
the native source and absence of experimental installation were unchanged.

The experiment's 3 loading-cache hits estimated 16.132 s of avoided miss work,
but public-part time was 33.683 s versus native V4's 31.166 s. Such avoided-time
estimates are not measured end-to-end savings: the original numerical helpers
also maintain exact low-level caches. Internal full checks took 21.737 s and
21.085 s respectively. Cold independent checking took over 43 s in both runs;
these observations are empirical costs, not mathematical lower bounds.

The earlier experimental-v1 failed feeder800 run still hit its 120.062 s
watchdog. No 10x end-to-end rescue follows from that run. Shaped-liquid
polynomial and gather variants passed numerical regression but were slower in
the isolated tested cases (0.399x and 0.827x), so both are off in v2. Artifacts:
`results/profile-v4-certified-medium.*`, `results/profile-fast-certified-medium.*`,
`results/profile-fast-timeout-medium.*`, `results/profile-shaped-selftest.json`.

## Stronger securing bounds: clean negative result

The final reviewed bound adds elevated-CG motion floors and monotone deck
fitting-shear lower forces. It was tested again on the same two TRAIN raw stows,
with helper SHA256
`13529457e1fe51e681c4db2e648e6dea9f93899c76037fca0cfcbcdcac471df4`
verified unchanged throughout the run. Both still return unknown rather than
an infeasibility certificate. On the failed feeder800 stow, the strongest
longitudinal-racking and fitting ratios are 0.842867 and 0.797811 (1.932 s).
On certified feeder500 they are 0.428087 and 0.428181 (1.781 s). Every lower
ratio remains below the corresponding independently computed original securing
metric. No optional bound was enabled in the measured primary finisher.
Artifact: `results/profile-bound3-probe.json`, linking the unchanged actual
securing reports from `profile-bound2-probe.*.lashing.json`. Intermediate bound
versions remain recorded as `profile-bound-probe` and `profile-bound2-probe`;
only the third probe has complete helper-source binding.

The optional inherited-recipe-first policy passed the same certified raw stow
at 131 lifts in **39.423 s**, followed by a 35.567 s independent full check.
Its finishing stage made exactly one unchanged full-checker call (36.184 s),
with search numerical hooks inactive. That is **2.08x** versus native finishing,
or **1.67x** including the independent check. The source hashes were unchanged
throughout the run. This isolates duplicated search-screening work as avoidable
on a first-recipe pass; it does not establish 10x or the same improvement on
failing recipes. Artifact: `results/profile-inherited-certified-medium.*`.

## Positive fixed-stow contradiction from the rod bound

The next reviewed bound uses the monotone grounded spring/rod system to lower
bound rod tension from the same componentwise minimum deck forces (see
REVIEW.md). On the timed-out feeder800 stow it produces
**`max_lashing_ratio >= 1.479983449217763` for every ballast fill**, exceeding
the unchanged checker limit. Its original actual securing ratio at inherited
fills is 2.1770283234383734. The witness is the four-box chain at slots
`0180582`, `0180584`, `0180586`, `0180588`.

The source-pinned bound ran in **1.702 s** and returned
`infeasible_fixed_stow`. This is over 352x shorter than the new 600 s profile
limit and over 705x shorter than the historical 1200 s watchdog, **for the bound
alone**, before measuring dispatcher/startup overhead. It is a necessary-condition
contradiction for this cargo stow, not an infeasibility proof for the whole
instance and not a passing plan. No full-checker acceptance is bypassed.

On the certified feeder500 stow it returned unknown in 1.575 s:
`max_lashing_ratio >= 0.6012259079579906`, below the original actual
0.8216276033827384. All corresponding lower ratios for both plans stayed below
their measured original values. The helper SHA256 was pinned before import and
verified unchanged after each call:
`e697b9ad86d0773b40d81497310ffe4a3f3d70a1849fd093f177968c8e0a806c`.
Artifact: `results/profile-bound4-probe.json`; the unchanged securing reports
are linked there. This final probe supersedes the earlier bound-only negative
results; those remain retained rather than overwritten.

## Final v3 unprofiled timeout comparison

All rows below use the identical hash-bound feeder800 raw cargo stow,
`STOWAGE_BALLAST_V4`'s native owned watchdog and one numerical thread. They are
unprofiled; the 600 s cProfile timing is not used to calculate these speedups.

| Finisher | Result | Wall s |
|---|---|---:|
| Unmodified native V4, 120 s allowance | watchdog, not found | 120.916 |
| v3 proof-first normal search entry | fixed-stow infeasible proven | 3.956 |
| v3 proof-first inherited entry | fixed-stow infeasible proven | 2.510 |

The normal v3 entry resolves the doomed candidate **30.6x sooner**, and the
inherited entry **48.2x sooner**, including startup/dispatch overhead. The old
runtime is censored at its watchdog; these are time-to-resolution comparisons,
not equal-status feasible-plan solve times. Neither failed plan counts as a
pass. Every accepted plan still requires the unchanged full checker.

The final guarded rod lower bound is 314496.482927 N against 212500 N allowed,
ratio 1.4799834490697648. Its auditable certificate includes minimum forces,
fixed winds, heights, CG fractions, positive rod-force coefficients, all rod
limits, source-equilibrium residual and witness slots. The relative source
linear-system residual is 6.16e-16. The bound's 128 deterministic selftest cases
passed, including invalid-input rejection, original/closed-form agreement and
monotonicity under increased forces; maximum relative rod difference was
3.17e-15 and maximum relative equilibrium residual 5.40e-15.

Final helper SHA256 is
`51e2be2ccc92bd43abbe9763642e24b8de748877d00d9168c47ac54ffdc26062`.
All four experimental source hashes stayed unchanged during both v3 runs.
Artifacts: `results/profile-v4-timeout-medium.*`,
`results/profile-v3-timeout-medium.*`,
`results/profile-v3-inherited-timeout-medium.*`, and
`results/profile-bound-selftest.json`.

The final v3 inherited policy also preserved the certified feeder500 pass at
**131 lifts**, taking **34.798 s** to finish plus **32.034 s** for the independent
unchanged full checker. Native V4 took 82.019 + 43.317 s on the identical raw
plan: 2.36x faster finishing, or 1.88x including independent checking. This
successful-plan result does **not** meet 10x. Both original full-checker gates
remain; the v3 internal call itself took 29.297 s. All four experimental source
hashes stayed unchanged. Final artifact:
`results/profile-v3-inherited-certified-medium.json` and its `.report.json`.

Verdict for these two fixed TRAIN stows: keep the necessary securing certificate
and inherited-first path. The certificate resolves the impossible stow over
48x sooner without accepting it, and the passing stow retains its full-checker
pass and lift count. The executive owns the complete paired quick-set result;
this profiling task makes no complete quick-set claim. Generalization beyond
these two stows and the selected checker/source configuration remains unmeasured.
