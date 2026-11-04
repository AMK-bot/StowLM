# Native hero gate latency

The first checked alternative (bay closure) completed the actual public lattice lookup at
2026-11-02T05:24:25Z. Its fresh gate passed the full checker, rule audit and all
146 crane frames. Gate time was **1148.710 seconds**; admission was **0.000149
seconds**. Total lookup time was **1151.439 seconds**. The older stored gate
reported 258.770 seconds; these observations do not isolate a cause for the
difference. The first refusal had separately waited 267.816 seconds including
admission and proof work.

The completed preparation receipts record the following original numerical
work. Dates are UTC; native gate wall time excludes the separately reported
admission wait. Weighted lifts include projected later-port work.

| Prepared result | Native checked date on 2026-11-02 | Gate wall time | Admission wait | Frames / result | Weighted lifts |
| --- | --- | ---: | ---: | --- | ---: |
| [Bay closure](fixtures/hero-final/native/72ccfc883f3db82da140879280c499b280fa1edd179c31f5e69f06056ea32530.json) | 05:24:25.430781Z | 1148.710433 s | 0.000149 s | 146, passed all | 82 |
| [DG clearance](fixtures/hero-final/native/9381a5a54b03ee22ba61bb73d2b68d07ffebdc604968672bcbd349dcdf84018a.json) | 05:33:34.342527Z | 546.400426 s | 0.000120 s | 146, passed all | 70 |
| [Baseline](fixtures/hero-final/native/e47b847abf288cf9ef1d74ee92cb80ad31a6f7c58e903565c8b1aa8a808cc77f.json) | 05:44:02.067441Z | 624.389161 s | 0.000172 s | 144, passed all | 66 |
| [Original engine candidate](fixtures/hero-final/native/original-candidate-gate.json) | 05:47:34.568515Z | 211.297611 s | 0.000132 s | 79 visited before abort; failed step 76 | 66 |

The failed candidate had measured heel **2.003047833000845 degrees** at step 76,
with `port.stability.cargo_operations`, stability false, strength true and no
frame error. This is genuine outside-limits evidence, not an exception or a
complete pass. The failed original and passing baseline share the instance hash
but have different plan hashes; equal 66-lift counts do not make them
interchangeable. These
receipts do not measure the latency of a newly requested binding search.

A separate admitted frame-zero probe completed in **41.038 seconds**, with both
native stability and strength passing. A timed stack sample found execution in
`stability_tank_transfer._section_polynomials`, through the tank-transfer and
stability-curve path. The probe is one frame, not a complete plan check and not
a model scenario. Its raw result and sampled stack are in
`fixtures/hero-final/frame-probe.json` and `frame-probe-stack.txt`.

This supports native numerical work as a latency contributor. It does not prove
a binding deadlock, a serialization bug, or a general per-frame runtime. No
checker, engine, stability implementation or numerical criterion was changed.
Process-list inspection was unavailable in the sandbox; no unrelated process
was stopped. Later gate receipts provide their own actual timing.

The original preparation warmed the real public lookup. The later interpreter
uses separately pinned, immutable preparation receipts through `NativeHandoff`;
this is explicitly **recorded native evidence reuse**, not a fresh numerical
invocation or private lattice-cache seeding. Reuse requires the authentic source
catalogue/row and receipt hashes, current binding source, engine, exact physics
epoch, input-bound clean full report, every crane frame, and a new independent
current-reader rule audit. A miss delegates to the real lookup, which can be
expensive; a confirmed miss can launch a background replan.

The pre-fix verified take's lookup misses had a reproducible integration cause,
not unexplained long numerical latency. Its first ToolSet call imported the
runner's pure envelope through the package initializer; `runner.core` then
overwrote the campaign vessel-cache environment path. The resulting checker
epoch drift invalidated all current receipt tokens. The [saved offline
diagnosis](fixtures/hero-verified/diagnostic-only.json) demonstrates valid
preflight, drift after import, and restored validity after restoring the exact
original environment. Shared numerical sources did not change. The copilot's
lazy import now preserves that caller environment, including an originally
unset key. It does not disable drift detection. Four import cases, a real
fresh-process first-call/two-checked-choice case, and upper-bound proof wording
were tested red before repair; 95 tool and 27 handoff tests passed afterwards.

The pre-fix [shot job](fixtures/hero-verified/shot10-background-terminal.json),
[confirmed-DG job](fixtures/hero-verified/hero_confirmed_dg_stored_choice-background-terminal.json)
and [other job](fixtures/hero-verified/background-terminal.json) terminated with
`ValueError` after **81.220 s**, **15.653 s** and **69.948 s** respectively. All
three reported no result available, no native work still running and no accepted
plan. Their private exception stacks were not captured; elapsed time and an
error type do not prove a binding or serialization cause. Full checker or
all-frame success must not be inferred from these job receipts.

Interactive replans use detached jobs and measured elapsed seconds. A pending
job is neither a completed plan nor proof of impossibility; requested stage
budgets are not wall-clock deadlines. These CPU durations have no recorded USD
charge. The completed original and pre-fix verified model-call files separately
record **$0.11908605** and **$0.19798450**, respectively; neither is a cumulative
task total or the cost of numerical handoff. This document does not inspect or
score fresh-goal traces or the separate demo take.

The completed [post-fix background terminal](fixtures/hero-postfix/background-terminal.json)
now records **213.496 seconds** measured job elapsed, `job_status: completed`
and `status: relaxed_plan`. It returned a real candidate through the configured
default binding path, but `passed` is false and there is no accepted plan hash.
The reader-confirmed hold-tier rule passed its independent recheck. Its host
capability source contract is `copilot_planner_confirmed:<reading_id>`; this
terminal does not preserve the exact confirmation ID/source field.

Its [new native gate](fixtures/hero-postfix/native/additional-e6c14c4071ffc6db392453e8b0ac3cd67d38e375a6191bfcd817d3db31ab6d7c.json)
finished at **2026-11-02T06:26:33.406052Z** after **127.779961 seconds** gate wall
time and **0.000150 seconds** admission wait. This is genuine new numerical
work, distinct from handoff of the earlier recorded passing alternatives. The
full checker failed on securing, visibility and stability criteria, with no
part execution errors. The crane-frame gate visited 27 frames before stopping
at step 24, heel **2.037241970645079 degrees**, stability false, strength true
and no frame error; it is explicitly not an all-frame pass. Its 94 weighted
nonproductive lifts are a rejected-candidate diagnostic, not a feasible option.

The 213.496-second job time and 127.779961-second gate time measure different
scopes; their difference is not an isolated measurement of engine search.
Native completion is now observed for this job, with correct non-acceptance of
the candidate. A successful background-generated plan remains unproved by this
receipt. This update uses only that completed terminal and associated gate;
post-fix fresh-goal raw traces and the separate demo were not read.
