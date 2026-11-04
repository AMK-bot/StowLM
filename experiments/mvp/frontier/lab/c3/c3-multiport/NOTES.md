# Multiport TRAIN experiment

Status: all 117 fixed-cohort measurements and final verification are complete. See the final verified results below.

## Scope and assumptions

Only this folder is written. `cohort.py` is a thin, scoped wrapper around the
existing synthetic random-yard generator. All 39 map specifications and fresh
seeds 263310000–263310038 are fixed before any planner result. Tiny has six boxes
so five discharge ports can all appear; this differs from historical tiny maps
with three or four boxes. Small retains the original equipment mixture. Medium
uses dry20 cargo at approximately 60% geometric fill. Every requested POD is
represented; POD coverage is a generation condition, never a planner-success
filter. These are empty-arrival, single-loading-call instances, with no future
loading calls. This does not reproduce full multi-load rotations.

The graph master's exact top/hatch union model also assumes each long page's
two support stacks share a hatch cover. Its inherited builder does not guard
this condition on general inputs and could undercount its surrogate for a
cross-cover long page. Real replay/full checking and the separate multi-stack
assignment bound remain authoritative. The JSON-only audit
`cohort-cover-scope.json` checks all 39 manifest-bound input geometries:
**zero possible long footprints cross covers**. Its saved-main snapshot also
checks 240 selected long placements in its 84-row development snapshot, finding
zero exceptions. This historical assignment snapshot predates the completed
sweep; the geometry census covers all 39 fixed inputs. This limitation therefore does not affect the
fixed cohort; a general-input version needs a guard or cover-set model.

`snapshot_engine.py` copies the engine into `frozen_engine/`; copied files,
namespace/bootstrap rewrites, and hashes are recorded in `engine-snapshot.json`.
See `snapshot-notes.md`. Shared frozen Frontier helpers, native sources, rules,
and parameters are imported unchanged. No live engine imports are permitted.

## Gate, budgets and compute

From repository root, reproduce/resume the fixed cohort comparison with:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 nice -n 5 .venv/bin/python \
  experiments/mvp/frontier/lab/c3/c3-multiport/parallel_run.py
```

The proposal entry point is `graph_method.py:propose`; finishing is
`graph_finish.py:finish`. `cohort.py` reproduces the fixed synthetic inputs.
`audit.py`, `measure_bounds.py`, and `verify_sources.py` perform post-run audits.

`run.py` intentionally does not call `lab/score.py`'s approved-input dispatcher:
these new TRAIN paths are outside its allowlist. It restricts inputs to this
folder's cohort and calls the same `score.evaluate`, including its unchanged,
independent `stowage.checker.check` on every retained completed plan. It writes
the full checked plan, lift parts, failure information, code/input hashes and
wall time in each row. All load-list boxes must appear in complete candidates.

The 0.9.0 arm calls the existing reference adapter for `planner.plan_and_check`,
with search `min(120,max(20,6*n))`, 300-second finishing, and three tries. The
adapter changes only CP-SAT workers to one, restoring the original function.
The copied engine and graph use the contract-approved E9 catalogue-lookup fix;
the as-is 0.9.0 arm does not. An extra pass attributable to that fix is a bug
fix rather than evidence of a mathematical solver gain without an ablation.
The copied engine uses all default switches, its default 120 extra seconds on
tiny/small, 375 seconds on medium, and 300-second native finishing. Engine historical
search and finishing floors remain. The cold graph arm receives 15/45/180
construction seconds on tiny/small/medium and the same 300-second V4 finishing,
trying up to six replay-legal candidates (the reference adapter uses three).
`graph_finish.py` uses the copied engine's securing proof, default-tank check
and inherited-tank finishing lanes before the full-budget native V4 fallback;
the runner still independently performs the unchanged full checker. The initial
pre-freeze `results/smoke/` used only native V4; it is not part of final results.
Budgets are per stage, not strict whole-map cutoffs; atomic checks can overrun.
Reported times compare these configured pipelines, with unequal construction
budgets and candidate limits; they do not isolate algorithmic speedup.
Machine load also changed during the sweep: early reference rows ran with the
leader benchmark active and one local worker; later graph rows ran after that
sentinel disappeared with up to two local workers. No controlled speedup is
claimed from those cross-phase wall times.

The original driver runs one CPU-busy child. The resumable capped driver,
`parallel_run.py`, runs one while the leader's `BENCH-RUNNING` sentinel exists
and at most two once it disappears. Numerical thread environment values are
one. Long commands request `nice -n 5`; macOS can deny priority adjustment,
in which case the process continues at inherited priority. No unrelated process
is stopped. `results/main/process.json` and `active-child.json` record our PIDs.
The resumed controller also records `parallel-process.json` and
`pids/parallel-*.json`; see `cap-migration.json` for the serial stop/reap audit.

The complete run is expected to exceed 20 minutes: 39 fixed maps × three
planners, retaining historical search and finishing floors and a full checker
for every pass, requires sustained serial work under the Cycle 3 process cap.
No maps will be removed for speed or after failures. Before the first small
engine run reached 30 minutes, a uniform 1,800-second external whole-arm/map
limit was fixed for all 117 jobs. Every previously completed row was below it.
Timed-out workers are interrupted and reaped, with no returned plan or checker
pass invented; their replay status is unknown. They count as bounded-run
failures, not infeasibility proofs. See `controller-notes.md` and the output
`measurement-policy.json`. Cleanup time can exceed the measurement limit.
An engine timeout may discard an internally checked incumbent because its
public `plan_and_check` has not returned; no unpublished incumbent is credited.
Timeout wall times are censored at 1,800 seconds, with actual cleanup timing in
the controller PID records. They are not measurements of natural completion.
An individual arm/map can exceed 20 minutes because several distinct cargo
stows each receive inherited and native 300-second finishing attempts before
the whole-map cap; this applies to both engine and graph candidate portfolios.
The pre-freeze small native-V4 smoke may itself exceed 20 minutes: it tries
up to six fixed stows, each with the unchanged 300-second ballast finisher.
It is retained solely as development failure/timing evidence, not a final row.

## Model and sources

See [graph-notes.md](graph-notes.md#primary-sources-and-what-was-adopted) for the
graph model, detailed adoption, and which sources were read only as abstracts.
Primary papers used:

* Avriel, Penn and Shpirer (2000), [Container ship stowage problem: complexity and connection to the coloring of circle graphs](https://doi.org/10.1016/S0166-218X(99)00245-0).
* Tanaka and Matsui (2021), [New Formulation for Coloring Circle Graphs and its Application to Capacitated Stowage Stack Minimization](https://arxiv.org/abs/2102.00691).
* Bernhart and Kainen (1979), [The book thickness of a graph](https://doi.org/10.1016/0095-8956(79)90021-2), for page terminology.
* Heath and Rosenberg (1992), [Laying Out Graphs Using Queues](https://doi.org/10.1137/0221055): the queue/FIFO versus stack/LIFO distinction; no-nesting queues are not no-crossing book pages.
* Gavril (1973), [Algorithms for a maximum clique and a maximum independent set of a circle graph](https://doi.org/10.1002/net.3230030305).
* Belady (1966), [A study of replacement algorithms for a virtual-storage computer](https://doi.org/10.1147/sj.52.0078).
* Hack, Grund and Goos (2005), [Towards Register Allocation for Programs in SSA-Form](https://publikationen.bibliothek.kit.edu/1000004125/2838), the three-author technical report, not Hack's later solo dissertation.
* Kindermann, Klute and Terziadis (2024), [PACE 2024: One-Sided Crossing Minimization](https://doi.org/10.4230/LIPIcs.IPEC.2024.26); Boehmer, George, Hauser and Palarus (2024), [Arcee solver description](https://kimonboehmer.net/files/papers/pace24.pdf).

The crucial distinction is between an exact count for D15's fixed departure
coordinates and a minimum over physically executable future policies. D15
removes discharged boxes but leaves survivors at their original coordinates;
its projected lifts are the checker objective, not a future operational
feasibility certificate. More discharge ports alone do not imply nontrivial
circle-graph colouring when load order is free. Physical constraints and shared
hatches can still force or favour nonzero projected restows.
All cohort port calls have distinct port identifiers; repeated visits to the
same port are outside this solver's supported scope. Yard costs enter through
real replay rather than the page master. Phase-1 LP duals are often zero, so
circle MWIS can act mainly as a diversity seed; its individual contribution
has not been isolated. An LP pricing `proved` flag is not a physical-family
feasibility certificate, especially after rejection of a heat-invalid pattern.

`tiny_bound.py` adds a global geometric relaxation for the six-cell tiny
instances: all 720 box-to-cell bijections are enumerated, with physics, unary
placement restrictions, yard order and crane paths relaxed. It separately
minimizes restow lifts and the joint weighted current/future hatch plus future
restow cost. Combining the joint minimum with mandatory nonload yard blockers
gives a total lift lower bound. `measure_bounds.py` records all 18 certificates
(12,960 complete assignments). The restow-only minimum is zero on every tiny
map; this is a geometry relaxation, not evidence that physical rules allow
zero restows. A full-checker pass attaining the total bound is globally optimal
for the checker's D15 objective. Separate minima are not added as though their
witnesses must coincide.

### How the old engine handles more than two discharge calls

The real replay/checker already iterates over every later call. Its top-access
closure counts a surviving blocker once per call, including indirect access
through spanning long boxes; hatch opening adds all surviving deck boxes over
that cover before a set union removes duplicate same-call charges.

The construction surrogates are weaker. In copied `frozen_engine/scout/pages.py`,
`Model.restows` already counts distinct earlier calls inside each fixed stack.
However `Model.integer` adds the cross-hatch deck-restow constraint only under
`len(self.ports) == 2`; with three or more ports that variable is minimized to
zero without such constraints. The new graph master replaces that omission
with one opening variable per cover/call and a union with internal stack events.
The engine's main scout ladder uses LP diving rather than this integer helper,
so this helper defect alone does not explain measured engine outcomes.
The original slot master in `experiments/mvp/frontier/master.py` charges one
overstow flag per occupied upper cell, so a box that blocks at multiple earlier
calls can be underpriced. Its per-call hatch terms do generalize, but can
overlap stack penalties. All of these are search surrogates: final real replay
and the full checker still count the actual D15 lifts, so no objective omission
silently establishes a pass or a false final lift count.

The engine replay gate must include scout ladder rows with non-null real
`lifts`, even if `full_check` subsequently rejects them and its top-level archive
is empty. `report.recorded_replay` reads this evidence. The tiny seed 263310005
demonstrated the distinction: engine pages constructed a 13-lift complete replay
but failed E8 heat clearance and draft, so it is a construction and not a pass.

## Results and verification

The graph/bound unit suites pass **22 tests** (12 graph, 10 tiny bound),
bytecode-free with no pytest cache, one numerical thread. Tests cover exact
MWIS against exhaustive subsets, all-port sorting, repeated blockers, hatch
union, departed long-box bridges, all legal tiny ordered patterns, three-port
master pricing, and exhaustive six-cell bound guards/weights.
The added graph tests cover exact E8 heat geometry on the actual lower prefix,
including a supported upper placement that an empty probe incorrectly refuses.
The E6 regressions verify that other hold pages can repair a deck pattern and
that a whole-combination no-good leaves shared individual pages available.

Pre-freeze smoke: tiny `263310000` passed at 17 lifts (1 relocation, 2 current
hatch, 6 later hatch, 8 later restow); small `263310018` failed after 1206.73 s
on ballast timeouts. This is not an infeasibility proof. The latter job completed
normally (exit 0) when polled; no process was terminated. Both old rows are in
`results/smoke/`, excluded from final means. Expanded pricing and the copied
engine finishing chain define the final graph implementation.

During the first complete launch, an unqualified `report` import resolved the
frozen Frontier helper. The coordinator now loads this folder's reporter by
explicit file path; its first completed 0.9 row is preserved separately under
`results/integration-failed-report/` and was rerun under the corrected hash.

During graph development on tiny seed 263310005, the inherited E8 empty-probe
fallback admitted heat-invalid pages, leading to futile ballast search. The
graph now defers E8 to an exact necessary per-pattern check on actual lower
placements, including pattern-balancing moves. This does not alter the checker
or any engine copy. Completed old graph rows live in `results/graph-before-e8/`;
the unfinished sixth attempt was explicitly interrupted using our recorded
execution session, and PID 53869 was confirmed absent. The sandbox refused a
direct signal first; no pattern-based process lookup or termination was used.
`results/main/graph-development-interruption.json` records this development
event. Neither it nor the old graph rows enters final means. Native reference
rows retain their original budgets, source hashes and measurements. All graph
maps are rerun under the new E8 implementation hash.

The E8 smoke rejects seed 263310005's invalid page family in 1.02 s, without
claiming global infeasibility, and still fully passes seed 263310000 at 7 lifts.

The first small graph run returned complete replay-legal candidates at eight
lifts but no full pass after 1,467.36 seconds. Direct checking identified E6
heavy-under-deck violations, which ballast cannot repair. E6 depends on all
occupied hold opportunities, so it cannot be banned per deck pattern. The
graph construction now checks E6 on the complete stow and excludes only whole
failing selected-pattern combinations before resolving. The prior 19 completed
graph rows are retained under `results/graph-before-e6/`; all graph maps are
rerun under the new implementation hash. Native arms remain unchanged. Our
recorded controller session was interrupted to apply this sound construction
fix, and every recorded descendant PID was confirmed absent; see
`e6-development-interruption.json`.
The first automatic graph timeout (small seed 263310025) stopped the controller
because one recorded native child was still exiting. All recorded descendants
were subsequently confirmed absent before the timeout was written. The
controller now retains cleanup evidence and waits up to 360 seconds for native
exit, while still enforcing other workers' deadlines and a returning leader
sentinel. Controller-only revisions are recorded separately; planner code,
run keys and the uniform 1,800-second measurement policy did not change.
See `timeout-cleanup-followup.json`, `sentinel-grace-restart.json` and the
`controller-revision*.json` records in `results/main/`.
A later graph timeout (seed 263310026) showed that a native 300-second ballast
calculation can outlive the 60-second initial cleanup grace after its parent
is interrupted. All recorded PIDs were again confirmed absent before recording
the timeout. Cleanup now allows six minutes, with one-second polling and no
new job launches, while preserving the 1,800-second solve cap. This cleanup
overhead is separate from censored solve times. The other interrupted graph
map is rerun; no incomplete attempt is treated as a finished measurement.
The fixed implementation fully passes the first small map at 20 lifts in
421.62 seconds (4 relocations, 4 current plus 6 later hatch lifts, 6 later
restow lifts), compared with the baseline's 16. It passes the second at 12
lifts in 408.69 seconds, matching the baseline. These are early fixed-cohort
observations, not the final all-map comparison.

Baseline finishing-failure case: on four-port `random.small.263310025.0.2`, 0.9.0 built
two complete replay-legal stows (two-step and blocked), each at replay objective
22; this is not a passing lift count. Both native V4 finishes exhausted their
300-second budgets without proving infeasibility. The complete-replay gate is
true, but no completed plan reached the full checker; the outer `no_candidates`
status means no finished plan was returned. None of the 20 selected loads is
OOG, so the horizontal-OOG E9 correction cannot explain the engine's subsequent
rescue. The copied engine fully passed at **16 lifts** in **1,043.88 seconds**:
4 relocations, 4 current plus 4 later hatch lifts, and 4 later restow lifts.
Its recorded winner is `ranked-0`, with archive origins `cycles`/`classes`;
the cycle trace improves replay cost 18 to 16, and the inherited finishing
lane accepts the winning stow. This records an end-to-end pipeline rescue of
a baseline finishing timeout. The stow and finishing pathway both differ;
it does not isolate a causal finishing improvement without an ablation.

First observed paired engine lift gain: on five-port
`random.small.263310030.0.2`, both native 0.9.0 and the copied engine produced
complete replay-legal stows and passed the full checker. Native used **18 lifts
in 503.96 seconds**; engine used **12 in 666.19 seconds**. The entire six-lift
difference is projected later restows, **6 to 0**; both retain 4 relocations,
4 current and 4 later hatch lifts, with no current restows. Engine winner
`ranked-0` passes through the inherited finishing lane. Its archive has ALNS,
extra-ALNS, cycles and classes origins, but baseline ALNS already reaches 12:
the blocked trace goes 26 to 12 and the two-step trace 30 to 28 to 26 to 12.
Cycles starts at 12 and records no improvement. No selected load is OOG.
This is a measured gain for the configured engine pipeline; it does not
isolate a causal gain from any individual search or finishing stage.

Four-port medium case `random.medium.263310036.0.2` has 144 dry20 loads and
no selected OOG. Both pipelines obtained complete legal replays and full
checker passes: native **95 lifts in 220.12 seconds**, copied engine **53 in
341.11 seconds**. The 42-lift reduction comprises later restows **34 to 0**
and later hatch lifts **20 to 12**; relocations remain 29, current hatch lifts
12, and current restows zero. Engine winner `pages-0` comes from the copied
scout's checker-guided page/layout ladder. A 271-lift stow passed before the
port-to-hatch layout trials; the first 53-lift layout failed draft, while a
later 53-lift layout with a revised longitudinal target passed. Thus the
earlier replay cost alone is not a pass. The winning 53-lift plan passed the
direct default-tank gate unchanged, without ballast finishing. Cycles started
at 53 and recorded no improvement. This is a full-checked gain for the
configured pipeline, not an isolated causal estimate for a stage or a proof
of global optimality.

Final measurements and independent verification are complete; the final results below supersede the development summaries.

<!-- FINALIZE_AFTER_RESULTS_BEGIN -->
## Final verified cohort results

All 117 arm/map rows are complete: 39 frozen TRAIN maps per arm. The unchanged full checker was rerun in a fresh process on every passing stored plan; all 90 audits agree with the stored lifts and graph top/hatch counts. Source/snapshot verification and all 22 graph/bound regression tests pass.

| Size | Ports | Measured 09/E/G | Known replay 09/E/G | Pass 09/E/G | Timeouts 09/E/G | Pairs | Paired lifts 09/E/G | Paired restow lifts 09/E/G | Mean seconds 09/E/G |
|---|---:|---|---|---|---|---:|---|---|---|
| tiny | 3 | 6/6/6 | 5/6/5 | 5/5/5 | 0/0/0 | 5 | 8.60/8.60/8.60 | 1.20/1.20/1.20 | 61.12/73.46/8.91 |
| tiny | 4 | 6/6/6 | 5/6/5 | 5/5/5 | 0/0/0 | 5 | 8.60/8.60/8.60 | 0/0/0 | 61.03/70.26/21.68 |
| tiny | 5 | 6/6/6 | 6/6/6 | 6/6/6 | 0/0/0 | 6 | 9.67/9.67/9.67 | 0.33/0.33/0 | 70.10/65.34/15.54 |
| small | 3 | 6/6/6 | 6/5/6 | 6/5/3 | 0/1/0 | 2 | 18.00/18.00/18.00 | 6/6/5 | 389.46/721.23/892.17 |
| small | 4 | 6/6/6 | 5/6/4 | 4/6/1 | 0/0/2 | 1 | 16.00/16.00/60.00 | 4/4/42 | 353.23/582.39/1229.76 |
| small | 5 | 6/6/6 | 6/6/6 | 6/6/3 | 0/0/0 | 3 | 18.00/18.00/22.00 | 5.33/5.33/8 | 374.56/420.84/588.89 |
| medium | 4 | 3/3/3 | 3/1/3 | 1/1/0 | 0/2/0 | 0 | —/—/— | —/—/— | 984.68/1313.70/890.31 |

**Unpaired passing-plan means.** Each arm uses its own full-check passes; these columns can describe different maps and are not paired comparisons.

| Size | Ports | Pass counts 09/E/G | Unpaired mean lifts 09/E/G | Unpaired mean restow lifts 09/E/G | Timeouts 09/E/G |
|---|---:|---|---|---|---|
| tiny | 3 | 5/5/5 | 8.60/8.60/8.60 | 1.20/1.20/1.20 | 0/0/0 |
| tiny | 4 | 5/5/5 | 8.60/8.60/8.60 | 0/0/0 | 0/0/0 |
| tiny | 5 | 6/6/6 | 9.67/9.67/9.67 | 0.33/0.33/0 | 0/0/0 |
| small | 3 | 6/5/3 | 16.33/16.40/18.67 | 4.33/4.40/5.33 | 0/1/0 |
| small | 4 | 4/6/1 | 14.00/16.67/60.00 | 1.50/4.33/42 | 0/0/2 |
| small | 5 | 6/6/3 | 18.67/17.00/22.00 | 6/4/8 | 0/0/0 |
| medium | 4 | 1/1/0 | 95.00/53.00/— | 34/0/— | 0/2/0 |

Arm order in the table is 0.9.0 / copied engine / graph. Paired means use only maps fully passed by all three arms, within each size and port count; no cross-size mean is used. Known replay counts are separate from full-check passes. Restows are lift counts, including the configured off/on charge. Timeouts are bounded-search failures, not infeasibility proofs. The uniform outer 1800-second cap censors runtime; cleanup can add time. Search, finishing and candidate limits differ across methods, so the timing columns describe these configured runs and do not isolate an algorithmic speedup.

Across all sizes, passes/39 and outer timeouts are frontier09: 33/39, 0 timeouts; engine: 34/39, 3 timeouts; graph: 23/39, 2 timeouts. These are pass counts only, not pooled lift means.

The 18 tiny certificates enumerate 12,960 complete six-cell bijections. All have a restow-only geometric minimum of zero; this does not prove zero restows physically feasible. Checked plans attain the joint hatch/restow plus mandatory-yard total bound on 15/15/15 tiny maps for 0.9.0/engine/graph respectively. Bound attainment proves optimality for the checker's D15 departure-coordinate objective, not for arbitrary adaptive future restow policies.

Scope: one loading call, empty arrival, no future loading calls; tiny uses six boxes in six cells, small uses the inherited equipment mix, and the three medium maps condition equipment to dry20 at 60% fill. More discharge ports alone still allow sorted crossing-free stacks. Physical restrictions and shared-hatch activations create the nontrivial projected-restow choices here.

Verdict: keep the all-call blocker/hatch accounting and exact tiny bounds. The mixed-page constructor remains experimental; these TRAIN measurements do not establish a general solver advantage. Next, test broader page families and real multi-load rotations while retaining the full checker and independent evaluation split.
<!-- FINALIZE_AFTER_RESULTS_END -->
