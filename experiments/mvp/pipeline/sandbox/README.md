# Break-it sandbox · task 1.7

Synthetic ships and cargo, simulated port. A drag proposes a new loading plan;
software checks it without running a planner or search. Spark can choose the order
of checker-backed statements. An offline template always works.

From the repository root:

```sh
UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run --offline --no-sync pytest experiments/mvp/pipeline/sandbox
```

```python
from experiments.mvp.pipeline import sandbox

# Inputs may be stowage Instance/MovePlan models or their JSON object dumps.
verdict = sandbox.try_move(instance, plan, box_id, target_slot,
                          on_fast=lambda v: send_to_browser(v.model_dump(mode='json')))
explanation = sandbox.explain_verdict(verdict)
candidate_plan = sandbox.move_plan(instance, plan, box_id, target_slot)
```

`try_move` returns the shared contract's `MoveVerdict`; `explain_verdict` returns
the shared `Explanation`. Their `.model_dump(mode='json')` values are JSON-able.
`move_plan` returns a `MovePlan`. Inputs are not mutated. Only a full legal verdict
permits accepting that proposed plan. `fast_screen` is also available separately.
The callback runs synchronously after the screen and before the full checker;
the caller can stream it from a background request worker.

The screen checks public static rules (slot fit, stack weight, height, dangerous
goods and other cargo rules) and computes approximate GM, trim, list and draught
from actual mass moments, tank data and hydrostatic tables. If these and crane
replay clear, an exact **departure securing screen** calls public `check_lashing`
on the proposed ship and declared ballast. Its original failures also gate the
provisional result; an unavailable part cannot clear it. This does not check
mid-voyage or arrival securing. A clear securing result is followed by exact
**departure visibility**, using public `check_visibility` and its original
findings. Voyage visibility remains pending. Its label is
**Fast screen, full check running**. Its `legal` flag is provisional. The rough
physics note identifies the approximations; missing table coverage fails closed.
The final verdict uses `stowage.checker.check`, including voyage conditions and
the active catalogue's required parts. No checker parts are disabled.

`gauges` is the stage-page physics block from `experiments/mvp/ui/CONTRACT.md`:
`frames`, station coordinates and SF/BM limits, `keyframes='final-only'`, and a
note. It also carries replay/count metadata. A final frame's GM, heel, trim,
draughts and strength values come from the checker's departure part. The overall
move verdict still includes failures at the other voyage conditions. GZ samples
are absent from checker reports, so `gz=[]` has an explicit reason. Fast-screen
stability/strength pass flags stay null. Full-frame numbers are never animated
or invented by this module.

If crane replay stops before the requested stow, the proposed loading sequence
cannot pass. The independent full checker also examines the requested final
stow through a diagnostic snapshot. `diagnostic_only=true` labels those gauges;
broken rows identify `proposal_replay` versus `requested_stow_snapshot` evidence.
That snapshot cannot approve the proposed plan. Every broken row retains the
checker/engine `rule_id`, original message, `measured`, `limit`, units and boxes;
unreported numbers remain null. Bad box ids or malformed slots yield an
`input_error` with no invented rule.

Final verdicts add three outcomes inside `gauges`, leaving the shared top-level
shape unchanged: `passes_as_edited`, `stow_fine_crane_order_failed`, and
`stow_breaks_rule`. `deciding_cause` leads the explanation. A failed existing
crane order leads even when a separate diagnostic stow also breaks a rule;
`stow_assessment` records that second finding and its provenance. A passing
snapshot means only that a replan **might** reach it. Invalid requests or an
unavailable assessment remain fail-closed and do not invent a stow diagnosis.

Occupied target slots swap boxes. Twenty-/forty-foot overlapping destinations
that cannot form a one-box swap remain illegal diagnostic proposals. The move
changes original loading destinations; it is a revised loading plan. Arrival
cargo uses explicit restow operations. `extra_lifts` is the real move engine's
current-call lift difference, often zero for revised load destinations. It is
null when the proposal cannot replay completely. The separate
`lift_counts.projected_later_delta` is a fixed-stow projection, not current crane
work. No search for a cheaper executable sequence is performed.
For the demo's loading-destination edits the exact scope text is: **"This edits
one box and keeps the crane order and ballast. It doesn't replan."** Arrival
edits that add explicit restow operations carry a different, truthful scope note.
Both lift measures are printed together, for example **"Crane lifts this call:
+0. Projected later-port lifts: +24 (projection)."** Those numbers are a formatting
example, not a claim that every hero drag adds 24 lifts. The actual value always
comes from the move engine. **"Legal isn't the same as good."** A failed replay
has unavailable deltas rather than an invented +0.

Explanations contain fixed statements plus evidence paths to verdict fields.
`explain_verdict` is offline by default, with no credentials or ledger access.
To opt into Spark, configure
`VerdictExplainer(client=SparkClient(...), use_api=True).explain(verdict)`.
Use the shared Spark client and ledger and enforce the task's $2 cap before
enabling calls. Spark returns statement ids, never display text; unknown ids,
repeated ids, omitted failures, malformed responses or transport/accounting
errors fall back to the complete template. The API cannot change the verdict.
Thai labels are deterministic; original checker messages remain English and
Thai has not had native-speaker review.

## Current hero demo and p22 handoff

The current source is [demo_source_hero.json](fixtures/demo_source_hero.json),
derived from p110-lattice's named **87-box feeder500 hero** and its original
crane sequence and ballast. The contract label is **"Demo cargo and limits
tightened for this challenge"**; the adjacent disclosure states precisely:
**only the cargo declaration changed; no vessel or rule limit was tightened.**
Synthetic box `QZXU0000014` changes from UN1263 paint, class 3, packing group III
to UN1689 sodium cyanide, class 6.1, packing group I. Mass and dimensions stay
the same. Existing `QZXU0000349`, UN1789 hydrochloric acid, stays unchanged.

The old artificial trim maximum **−0.155677 m** and stack rating **18.2564 t**
are retired. This ship uses its native trim window **−1.078 to +1.078 m** and
unchanged stack ratings, not limits copied from the old 190-box feeder800.
The ships have different native ratings: hero stack `00904D` is 30 t generic,
45 t for twenty-foot boxes and 30 t for forty-foot boxes. That difference is
not a demo tightening. The securing case uses the existing ratio limit **1.0**.
Its model uses declared synthetic gear and the checker's securing approximation;
it is not a class-approved securing calculation.

The fully checked hero pass (`QZXU0000082 → 0100104`) actually displays
**"Crane lifts this call: +0. Projected later-port lifts: +28 (projection)."**
The later-port figure is the fixed-stow move-engine projection, not a searched
future crane plan. It is why the pass also says **"Legal isn't the same as good."**

The four playback case kinds are now **`legal`, `crane_order`, `segregation`,
`securing`**. The invented `stability` case is removed; `stack_weight` becomes a
real securing failure at an unchanged limit. Top-level `MoveVerdict`/`Explanation`
and existing per-case playback fields stay the same. Outcome, cause, scope,
projection labels and provenance are additive inside `gauges` or fixture
metadata. [P22-HANDOFF.md](P22-HANDOFF.md) lists the required case-id and display
updates; this folder does not edit `app/`.

Recorded frames and verdicts are labelled **Recorded answer**, with their
**Checked on** date. Loading a fixture is not a new recheck. Show the stored-plan
computed date, and use "rechecked now" only after an actual fresh check.

The [demo-instance audit manifest](fixtures/demo-audit.manifest.json) freezes
**30 new requests on this exact hero cargo variant**, seed **170730**, before
any candidate fast or full result. The geometry-only pool has 747 requests;
selection fixes ten far, ten heavy and ten cross-hatch requests, five swaps and
five empty-slot moves in each stratum, across 29 boxes. Far means at least
**22.8402 m** longitudinal displacement (30% of this modeled span); heavy means
at least **15,178.6523 kg**, the stowed mass upper quartile; cross-hatch means
different actual cover-panel sets. Slots are size-compatible and empty targets
have geometric supports. This is a stratified challenge sample, not a random
population estimate or a sample of only screen-cleared moves.

**Completed demo-instance audit: 0/5 false-legal among five screen clears**
in **30 frozen drags**. The other 25 were flagged. Final outcomes were **5
passes as edited, 18 stows that pass but whose existing crane order fails, and
7 stows that break a rule**. Every canonical report ran all required parts
without errors: nine checks judged the edited plan, and 21 judged explicitly
diagnostic requested stows after replay failed. A diagnostic pass never approved
an edited crane plan. No requests were replaced and the gates were not tuned
using these outcomes. Five clears is a small denominator; this is not a claim
of 0/30 false-legal or a broad accuracy guarantee.

The audit used four workers, native thread caps one and fresh admission at
load1 **35.31–43.17**. Its all-drag screen median/p95 were **0.609/6.005 s**;
that median is dominated by early refusals and **does not measure clear-screen
latency or resolve the under-two-second target**. Canonical-checker median/p95
were **33.002/50.133 s**, and complete API **35.389/53.447 s**. These are loaded
concurrent timings, not low-load stage timings. See each row's admission and
end readings (end load1 **35.31–66.98**); later load increases do not become
qualifying low-load evidence. The five clear-screen calls took **5.615 s median**,
range **1.685–6.974 s** under this concurrent load.

Each audit request gets a fresh canonical call on its exact candidate plan or
its explicitly named requested-stow diagnostic snapshot. The latter cannot
approve the original edited crane plan. Raw reports, complete input bindings,
fast callbacks, outcome provenance and admission/end readings are retained in
`fixtures/demo-audit.results.json`; no request is replaced after its outcome.

The first **25 audit request indices were fixed for the cache before outcomes**.
`fixtures/drag-cache.json` replays those exact full verdicts and explanations;
the four curated showcase cases are separately held in `demo.json`. A cache hit
does not become another fresh check. Use:

```python
from experiments.mvp.pipeline.sandbox.demo_cache import lookup_cached_drag
recorded = lookup_cached_drag(instance, plan, box_id, target_slot)
# None means input/code/request mismatch: use the existing fresh checker path.
```

This checks normalized instance/plan identities, the request, checker/parameter
source bytes and component versions. Inputs must be the exact disclosed demo
variant, not the unmodified upstream hero. Returned data are labelled
**Recorded answer**, with `checked_utc`; no network or paid call is needed.
Pin `drag-cache.sha256` independently for release and see `drag-cache.index.json`
for the prepared request list.

All four current hero explanations were recorded once through live guarded
Spark selection in [spark_demo_hero.json](fixtures/spark_demo_hero.json):
**4/4 validated, $0.01424475 incremental, $0.02298500 cumulative task spend,
zero outstanding holds, no retries**, within the $2 cap. Their complete offline
templates remain alongside the recorded selections in `demo.json`. Spark only
selected and ordered the checked statement IDs; it did not author facts, alter
the cause, omit the projected lift cost, or decide legality. The 25 cached audit
explanations are explicitly recorded templates, not additional model calls.

## Independent development measurements

**Primary independent audit: 0/40 false-legal (0.0%)**, from 40 fresh canonical
full checks of requests frozen before any candidate full-check outcome. All 40
also cleared the unchanged screen at execution time. This is evidence for this
sample on one different plan, not a guarantee about all drags. The earlier 0/40
result below is an **adaptation-set replay**, using moves that informed the
gates; it is not independent accuracy evidence.

The new holdout uses the lattice `current-v3/084ffe943b997966` feeder800
plan (163 load-list boxes, 262 crane moves), a different instance and plan from
the 190-box development source. A fresh, input-bound canonical baseline passed
in 30.761 s at load1 23.65–25.99. Seed **177017**, the complete candidate draw,
20-swap/20-empty quotas and at most two requests per box were committed before
candidate screening. The immutable [40-request manifest](fixtures/holdout.manifest.json)
was sealed at **2026-11-02 02:40:53 UTC**, before all candidate full checks:
**20 swaps, 20 moves to empty slots, 34 distinct boxes and 40 unique resulting
stows**. No request was replaced and no gate was tuned after outcomes arrived.
The [complete results](fixtures/holdout.results.json) retain one fresh canonical
call per request, every original report and its exact input binding. Every
required part ran without error; all 40 candidate plans passed. No full verdict
from the development cohorts was reused.

The [pre-full dependency fingerprint](fixtures/holdout.readiness.json) pins
checker, vessel, move, rule and parameter sources before the first candidate
call. The [independent freeze audit](fixtures/holdout.independent-readiness-audit.json)
verifies the original draw, all 40 preserved requests and their provenance.
A Python 3.12 `marshal` hash false alarm occurred before sealing: string interning
can change that serialization without changing code. Recovery retained the
exact original 40 requests and verified all original source bytes and all six
gate code objects against the archived sources; [the proof](fixtures/holdout.marshal-audit.json)
is recorded. No candidate full outcome existed during this recovery.

The accuracy cohort ran with **four concurrent workers**, native thread caps
one and fresh admission at load1 **21.02–44.41** (limit ≤45). End-of-call
readings ranged **22.24–57.53**; a load increase after admission does not become
a low-load claim. Its fast-screen median/p95 were
**5.386/8.172 s**, canonical-checker **29.247/34.987 s**, and complete
`try_move` API **34.798/42.240 s**. These are concurrent loaded measurements,
including cold workers, **not low-load stage latency**. The separate 190-box
low-load measurements below supply that latency evidence.

`fixtures/` contains synthetic source plans, the current four-case hero demo and
historical development timings. Recorded explanations carry `mode='recorded'`.
Playback needs no network, GPU or paid call. Recheck a stored source plan before
accepting it as a current plan; a recorded verdict is labelled recorded evidence.
Each demo case includes a provisional `fast` frame and `fast_explanation`, then
the authoritative `verdict` and `explanation`. Current hero fast frames are
captured from the actual `on_fast` callback during the same request. No playback
phase claims to be a live computation. The superseded 190-box demo is retained
at `fixtures/history-v1/demo.json`; its early fast frames and tightened limits
are historical evidence, not current demo settings.

All four historical demo explanations were recorded through live Spark and the guarded
statement-id selector. [That historical recording](fixtures/spark_demo.json) retains selected
ids, request ids, input/prompt hashes and measured ledger settlements: **4/4
validated; $0.00874025 total; no outstanding holds; no retries**. Each demo case
also retains its complete `template_explanation`. It remains bound to its original
demo and archived renderer, and is never relabelled as an answer about new hero
facts. Playback and the default
`explain_verdict` remain offline. `record_spark.py` enforces the $2 task cap against
actual spend plus outstanding reservations and refuses to overwrite a recording.

Reproduce the timings (at least 50 distinct drags per plan):

```sh
UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run --offline --no-sync python -m experiments.mvp.pipeline.sandbox.benchmark
UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run --offline --no-sync python -m experiments.mvp.pipeline.sandbox.benchmark --source experiments/mvp/pipeline/sandbox/fixtures/largest354.json --output experiments/mvp/pipeline/sandbox/fixtures/benchmark354.json
```

The recorder waits for fresh fleet admission: at least eight mask bits and
load1 at most 45. Load readings in recorded rows are **admission readings**;
other sessions may increase load while a check runs. Each cohort uses three
worker processes with native thread caps of one; the two cohorts overlap, so
there are up to six measurement workers. End-load readings are also retained.
These response times measure concurrent benchmark load; an idle, single-request
UI timing was not measured.
The original development challenge set deliberately mixes clear and flagged fast screens;
the exact selection counts are recorded per cohort. Its frequencies do not
estimate the population of arbitrary drags.
Fast response latency includes preparing
and replaying the proposal; full response latency includes the screen and all
required checker work. Source-plan validation is recorded separately as cold
baseline time. Timed calls receive already validated `Instance`/`MovePlan`
models; fixture JSON loading and initial model validation are outside that region.
These are development measurements, not sealed performance or
pass-rate claims. Every drag starts from the same checked source plan.

The additional clear-screen audit selects distinct moves using replay and the
screen alone, before consulting full-check results. It requires at least 40 clear
screens and at least ten moves in each stratum: occupied-slot swaps and supported
empty-slot moves. Every selected drag then receives a fresh screen and canonical
full check; no stored base verdict substitutes for checking the changed plan.
This is a targeted development cohort, not a random sample of arbitrary drags.
Reproduce it with `python -m experiments.mvp.pipeline.sandbox.clear_benchmark discover`,
then the same command with `record`, using the environment above.

<!-- FOLLOWUP -->
The expanded **original-screen audit found 12/40 false-legals (30.0%)**: 40
distinct resulting stows from 34 dragged boxes, with **10 swaps and 30 moves to
empty slots**. All 12 misses violate `iacs.member_irs.container_securing`; three
also violate `solas.v22.visibility`. Counts describe moves, not repeated findings
across voyage conditions. The fitting failure for `QZXU0000605 → 0100688` is
1.011700917545739 > 1 at departure. These are actual full-checker findings.
[All 40 fresh full checks, including every miss](fixtures/clear190.json) remain
recorded; none was removed after seeing its result.

The securing-only revision reduced this to **1/40 false-legals (2.5%)** across
10 swaps and 30 empty-slot moves from 33 boxes. It blocked all 12 original misses.
The remaining move, `QZXU0001601 → 0090390`, violates `solas.v22.visibility`:
departure blind sea distance 278.9309622643067 m > 268.88 m (mid-voyage also fails).
[That second audit](fixtures/clear190-current.json) has 16 fresh full checks and
24 explicitly labelled recorded full results for identical reconstructed inputs.
The reused results require the original source hash, checker files and component
versions, matching archived proposal/full-path code, and reproduced placement
hashes. No full outcome selected a request. Fast median / p95 was 3.264 / 5.976 s.
Reused full latency retains its historical value; its mixture with new calls is
not a current full-response benchmark. The 16 fresh calls' full median / p95 was
24.790 / 36.469 s, at admission load1 33.56–40.89 and end readings 33.56–39.85.
Current fast observations had admission load1 33.56–42.25. These remain shared-load
measurements.

The added departure visibility check costs **0.39–0.56 s** after securing on
three distinct requests. It reproduces the remaining violation exactly and both
stored legal requests pass. The fresh canonical baseline passed in 20.512 s;
every required part ran, complete input binding matched, and inputs stayed
unchanged. [Raw measurements](fixtures/visibility190.json) retain admission/end
load and the public checker's findings. These are loaded observations.

<!-- FINAL_AUDIT -->
**Adaptation-set replay: 0/40 false-legals (not independent evidence)**, with **10 swaps and 30 empty-slot
moves**, 32 dragged boxes and 40 distinct resulting plans.
[The final audit](fixtures/clear190-final.json) retains all 40 current clear
screens and their full results: **39 explicitly attributed recorded full checks
for identical reconstructed inputs plus one fresh canonical full check**. This
is reuse of the same candidate's recorded result in an experiment, never reuse
of a base report to approve a different candidate. The production API always
runs its canonical full check. Selection uses the screen, never full outcomes.

| Screen version | False-legal moves / clear screens | Full-result evidence |
| --- | --- | --- |
| Original | 12/40 (30.0%) | 40 fresh full checks |
| Add departure securing | 1/40 (2.5%) | 24 attributed recordings + 16 fresh checks |
| Add departure visibility | **0/40 (0.0%)** | 39 attributed recordings + 1 fresh check |

These are different targeted cohorts, not random-drag population estimates. The
securing revision also rescreened and rejected all 12 original misses; the final
revision rescreened and rejected the visibility-only miss. All older cohorts
and their failures remain recorded.

Final current fast latency: **median 3.995 s / p95 5.549 s / maximum 8.785 s**,
at admission load1 **30.69–41.48**. Discovery used one native-thread-capped
process; the final fresh check used one worker. Current fast-end load was not
captured. Copied historical `load_after` fields belong to old full checks and
are never presented as current fast-end readings.

The single fresh final call (`QZXU0001643 → 0100188`) passed its canonical full
check in **31.478 s including its 6.490 s fast phase**, at load1 **30.69 before /
26.44 after**. That is one loaded observation, not a full-latency distribution
or an idle-stage claim. The 39 reused full latencies retain their historical
values; their aggregate is not a new response-time benchmark.

Final verification after the hero refresh: **135 tests passed offline in
48.66 s** using the command above, with fresh load1 **38.83** admission and native
thread caps one. This includes the completed 30-drag hero audit, exact 25-entry
cache identities and release pins, unchanged hero limits, three outcomes,
current and historical guarded Spark replay, and the earlier 40-clear holdout's
original reports and complete input bindings. [The earlier independent audit](fixtures/holdout.independent-final-audit.json)
remains evidence for its own frozen 40-request sample.
<!-- /FINAL_AUDIT -->

Historical sources are preserved in `fixtures/screen_v1/`, and historical timings
continue to describe their original implementation.

Original-screen response median / p95: **0.551 / 3.172 s fast**, **28.919 / 58.959 s
full**. Admission load1 was 29.89–44.71; the first four checks used one worker,
the next two used two workers and the remaining 34 used four workers, all native
thread caps one. These are loaded timings, not a low-load UI latency claim.

The exact securing addition has a measured startup cost. In fresh processes its
first call took **7.44–7.60 s**; a second identical call took **0.41–0.79 s**.
More relevant to a new drag, after a canonical base-plan full check in the same
worker, first calls on three distinct requests took **3.98 / 3.93 / 3.53 s**.
That base check took 35.294 s and passed with every required part run and exact
input binding. **Base validation did not establish subsecond first-drag cost.**
These timings exclude parsing, model validation and proposal preparation; each
call has its admission/end load recorded. [Cold/repeat observations](fixtures/securing190.json)
and [canonical-base warmup observations](fixtures/securing190_basewarm.json) retain
the raw part evidence. No base report substitutes for checking a changed plan.
<!-- /FOLLOWUP -->

Cost attribution comes from a separate [loaded cProfile run](fixtures/profile190_loaded.json):
57.589 s instrumented wall time at sampled load1 26.59–28.86. Its cumulative part
costs are visibility 21.100 s (36.6%), strength 16.222 s (28.2%), stability
14.740 s (25.6%) and lashing 3.620 s (6.3%); each runs at all three voyage
conditions. All required parts ran, the report passed, and its full input binding
matches the changed plan. Profiling adds overhead: these are cost shares, **not
uninstrumented response latency**. Parent/child cumulative rows overlap.
[The derived attribution](fixtures/profile190_attribution.json) retains the exact rows.

**Low-load full-check measurements are now available.** The explicitly chosen
gate is fresh load1 ≤20 with at least eight available mask bits, sustained across
the sampled readings. A [finite follow-up](fixtures/profile190_lowload_followup.json)
ran the canonical checker with unchanged default arguments, one process and
native thread caps one. The 190-box base took **22.892 s** at load1
**19.65–19.78**; a genuine changed swap took **17.387 s** at **19.16–19.78**.
Each report passed, matched its complete input binding, and ran every required
part without error. These are two individual measurements, not a latency
distribution or guarantee. Preparation, startup and admission waits are excluded.
A different move to an empty slot took **19.340 s**, but load1 rose to **21.20**;
it is explicitly **not sustained low-load evidence**. No other sandbox full-check
workers ran during these measurements. All three raw reports and sampled loads
are retained.

The earlier [observation record](fixtures/profile190_lowload_observation.json)
remains historical: through 2026-11-02 02:22 UTC it saw load1 **26.59–98.01**
without a qualifying window. Its original recorder PID 72196 exited without a
result; `profile190_lowload.json` is incomplete, not an active queued job.
The loaded 48-second historical figure is not an idle-stage claim.

The new [190-box screen profile](fixtures/perf_fast190_loaded.json) uses six
distinct development moves, separate from the holdout. The first three are
uninstrumented: **3.857 s median**, range **3.773–4.186 s**, with native thread
caps one and admission at load1 ≤45. Securing costs **2.378–3.194 s**;
visibility costs **0.567–0.823 s**. The instrumented runs identify the coupled
liquid-centroid/equilibrium calculation as the dominant work, including four
uncached equilibrium solves for the first profiled request. Visibility is not
the main bottleneck. Exact hull geometry and equilibrium-input caches already
exist in the checker. These loaded timings are separate from the low-load
measurements below. Repeating an identical request to benefit from a cached
solution would not measure a fresh drag.

A qualifying low-load window then produced [three distinct 190-box fast-screen
measurements](fixtures/perf_fast190_low.json): **3.224 s median**, range
**2.879–3.710 s**, with admission and end readings **load1 19.45–19.84**, fresh
readings, native thread caps one and no concurrent sandbox full-check workers.
Parsing and startup are excluded; move preparation, replay and every current
screen gate are included. The fresh base canonical report passed and matched
its inputs (13.850 s). Securing costs **1.949–2.899 s**, visibility
**0.382–0.454 s**. **The under-2-second target is unmet on this three-move
sample.** These development requests are separate from the accuracy holdout.

An [exact-workspace experiment](fixtures/perf_workspace190_loaded.json) also
reran public static rules, securing and visibility on three development
candidates. Every complete `PartReport` matched exactly between separate and
shared disposable numerical scopes. The alternating arms followed an explicit
warmup of each identical candidate; their warm timings cannot establish fresh
drag latency or attribute a speed gain to sharing scopes. No production cache
patch, omitted rule or base-verdict shortcut was introduced.

A cached base report does not provide a safe one-box full recheck here. Reports
bind the complete instance and plan; a changed destination breaks that binding.
`try_move` already makes only one full-checker call per drag, on the candidate
or its labelled diagnostic snapshot. Its base-plan replay supplies lift counts;
there is no repeated base full check to eliminate.
A changed placement can affect mass moments, finite-angle stability, global hull
loads, visibility, securing, static rules, replay/count and active in-port checks.
No proof that any affected part can be omitted was established. The canonical
checker already reuses exact geometry and parsed parameters, with a fresh
disposable numerical workspace per evaluation. **No faster full-check path or
base-verdict reuse is implemented or claimed.** Skipping a required part would
produce an incremental screen, not a full check.

The following table is the earlier v1 concurrent cohort, with just five clear
screens per plan. Use the fresh frozen 40-clear result above as the primary
independent agreement measurement; the intervening adaptation replays are
development evidence.

<!-- MEASUREMENTS -->
Measured development results, from completed recorded runs. Seconds are wall time.

| Boxes | Moves (full legal / illegal) | Fast median / p95 | Full median / p95 | Fast says legal, full disagrees |
| --- | --- | --- | --- | --- |
| [190](fixtures/benchmark190.json) | 50 (5 / 45) | 0.763 / 3.415 | 48.012 / 64.632 | 0 of 5 fast-legal (0.0%); 0 of 50 all drags |
| [354](fixtures/benchmark354.json) | 50 (5 / 45) | 8.853 / 17.179 | 57.381 / 89.489 | 0 of 5 fast-legal (0.0%); 0 of 50 all drags |

The false-legal denominator is the number of clear fast screens, not the number of all drags. The small, deliberately selected clear subset is not an estimate of arbitrary-drag accuracy. No fast screen is an approval.

- 190 boxes: admission load1 31.45–44.97; end readings 31.45–73.81; cold source-plan full check 64.611 s. Cohort: 5 clear / 45 flagged screens; 3 workers. [Recorded rows and source hashes](fixtures/benchmark190.json).
- 354 boxes: admission load1 32.74–44.97; end readings 32.74–62.02; cold source-plan full check 86.052 s. Cohort: 5 clear / 45 flagged screens; 3 workers. [Recorded rows and source hashes](fixtures/benchmark354.json).

Worker cold imports are included in response times. Fleet-admission waits and cohort selection are excluded; they are experiment scheduling, not work performed by `try_move`. p95 uses the nearest-rank definition. Timings exclude explanation rendering/network.
<!-- /MEASUREMENTS -->

The dev map list was absent when sources were selected. `source_candidates.json`
records the C6 result scan: a checked 190-box feeder-800 plan for the demo target
and the largest checked result found, 354 boxes on feeder-1700. The latter is
outside the pipeline's advertised envelope and is only a checker stress test.
No sealed sets were opened. `app/stage_fast.py`, `app/stage_physics.py`,
`src/llm/spark/explain.py` and `app/explanation_links.py` were read for their
incremental replay, physics and statement-selection approaches; none was edited.

The current hero demo changes one disclosed synthetic DG declaration. It does
not tighten trim, stack, securing or segregation limits. Exact changes, sources,
the fresh full baseline and prepared verdicts are recorded in `fixtures/demo.json`.

Weaknesses: full numerical checks can take tens of seconds on this shared
machine. An illegal replay is rejected by the move engine and receives one full
diagnostic check of the requested stow. The screen
does not assess finite-angle GZ, hull strength, voyage visibility or voyage securing and can
disagree with the full checker. Arbitrary physical drags need not have an
executable crane sequence. Synthetic cargo changes are labelled in the demo
fixture; they are not facts about a real vessel. No binding stub or planner is
used. Cumulative Spark spend for this task: **$0.02298500**; live guarded
selections and their offline templates are both recorded.
