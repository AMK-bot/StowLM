# Independent Cycle 4 review

Read: full CONTRACT including Cycle 4 corrections; `lab/README.md`; logic
review F7/F11; C3 proof scope and final proof-count table; C4 copies of bound,
joint rank model, lazy oracle, DG/heat/E6 strengthening, geometric disjunctions,
securing helper and public API; relevant authoritative checker source.
No numerical jobs, tests or benchmark input maps were run/opened. Only
proof-count summaries were read for validation. This reviewer edits only this
file and `proof_scope.md` in the assigned C4 folder.

## Required wording and interface

Use the exact scope sentence wherever an equality/proof table or narrative
uses the term optimal:

> fewest lifts among plans the checker passes, counting later ports by our D15 projection (fixed stow, no later loads)

The imported `certified.lower_bound(instance, *, budget_s, seed, tables,
upper_bound)` interface and dataclass fields should be left intact for the
engine integration. Extra construction work belongs in a separate entry point.
Keep the DG-row rebuilding in `strengthening.safe_unary_mask` without reverting
to the legacy empty-ship probe. This is the substantive F7 correction.

## Audited questions

- No discovered background reconstruction bug: the lazy oracle's ship contains
  every decision assignment, while the original instance retains all fixed
  background, which authoritative rules load separately. Fixed background
  boxes must not become decision destinations.
- The rank lower bound includes arbitrary current restows. “First aboard”
  differs from “final placed”; per-box ship-restow flags preserve that
  distinction. One direct shift is one lift, not an off/on pair.
- A static witness is neither replay nor physics legality. A proven relaxation
  optimum is a lower bound until equality with a full-check passing plan.
- The shortcut that proves an initial packing bound above `U-1` may correctly
  return no assignment. Use an ordinary no-cutoff construction solve when the
  purpose is to obtain a new plan; do not count proof-only shortcut runs in the
  cold-constructor denominator.
- Oracle hard failure is suitable for final-assignment exclusion only if it is
  final-state and ballast-independent. Never use a failed expansion/order,
  a failed particular ballast, or an exception as an unrestricted cargo cut.

## Protocol for the 13 remaining validation maps

Before inspecting new results, freeze a generic extension policy: fixed seed,
same model and source hashes; the same longer budget for every selected
remaining map; deterministic discovery of approved newer checked upper rows;
and a generic candidate construction/finishing limit. The original 32 equality
certificates can be carried as historical evidence, or cheaply rechecked;
report which route was used. A fresh checker pass is still needed for each new
upper plan under the recorded current source. The set is development data,
reserved for proof counts in this assignment, not a tuning set.

Separate outcomes: improved upper; improved lower; new equality; no passing
upper; solver timeout/UNKNOWN; and checker failure. Count all 45 in the final
denominator and the original remaining 13 as a secondary denominator. A failed
bounded run is not evidence of map infeasibility.

## Cold construction and replacement claim

Evaluate no-upper-target/no-stored-stow/no-stored-ballast construction on
`quick_small` first. Preserve the first raw master witness before lazy cuts,
and first fully expanded candidate before any retry. Report raw static,
replay, first full-check, and eventual full-check rates separately. If an
attempt fails only a costly ballast finish it still fails the first full-check
rate. Log actual end-to-end wall time and model time separately. A proof-backed
search replacement requires a complete legal plan plus bound equality, with
the required scope sentence; it cannot follow from a lower-bound-only result.

For a solver speed/quality claim, run a fresh 0.9.0+E9 arm at the same budgets
and machine load, or restrict the claim to measured absolute constructor
latency and label the missing fair comparator. Historical 300-second rows do
not establish a measured speedup on this shared machine.

## Medium scaling

Use the three named 88-box layout upper plans 71/55/70 and prior lower bounds
65/51/70 as benchmark targets. State these V6-passing maps were preselected as
passable. Report model build time/size, CP bound progression/work, candidate
generation, full checks and total time. Any lift savings concern the D15
projection rather than arbitrary executable future voyage schedules.

## General strengthening ideas, not validation tuning

The current eager OOG rows already group geometric equivalence classes.
Any further symmetry grouping should identify truly exchangeable boxes across
yard order, POD, requests, weights and securing properties; slot-only grouping
does not justify exchanging identities in first-aboard order. A generic
same-attribute symmetry improvement can be designed/tested on quick/synthetic
inputs before a new source freeze, but the observed validation failures must
not choose its classes or parameters. No additional strengthening is required
to justify giving the unchanged exact model a predeclared longer proof budget.

No correctness flaw was found in the inspected copied C3 formulation beyond
the previously documented scope, numeric/source guards and construction
limitations. This review is source reasoning, not an independent formal proof
or a replacement for model regression tests and the full checker.

## Follow-up: constructor and proof-run adapters

Read-only inspection of `constructor.py`, `method.py`, `raw_method.py` and
`proofs.py` confirms:

- Construction adds `ship_restowed == 0`, so it is explicitly a restricted
  load-once **ship placement** family. Yard relocations remain allowed. Its
  result exposes no global `lower_bound`, and replay-order failures exclude
  only the identical assignment plus identical first-aboard rank permutation.
  These exclusions are construction-only, not global proof cuts.
- The constructor does not consume a historical upper target, assignment or
  ballast. `method.py` reads only `context['problem']` and its numeric tables;
  the global scorer's mere presence of baseline fields does not warm it.
- `dataclasses.asdict(Construction)` recursively converts the nested frozen
  `Place` dataclasses into `{'box', 'slot'}` dictionaries, including inside
  tuples. `_jsonable` then converts tuples to lists, so the proposed diagnostic
  serialization is valid without a custom Place encoder.
- The first returned CP assignment is saved before static/securing-cut
  retries, with its static violations and independent replay score. The raw
  admission adapter returns only this one candidate for independent cold
  finishing. That is a sound first-returned-witness rate, distinct from the
  eventual filtered-candidate rate. A minimization solve may internally find
  several incumbents before returning; do not describe the returned witness
  as the solver's literal first discovered incumbent.
- `PROOF_FREEZE.json.global_bound_modules` contains exactly the unchanged eight
  C3 global-bound dependencies. The initial 120-second budget and 300-second
  continuation were declared before new validation outcomes, with seed zero.
  Constructor/medium experiments do not change that proof method.

Provenance safeguards requested of the run owner before final reporting:

1. Pin and verify cached raw diagnostics to input SHA, constructor source and
   seed/budget. A map-ID-only filename can otherwise silently admit an earlier
   diagnostic from a different run. Count missing/failed raw candidates in the
   denominator rather than silently omitting them.
2. A raw-cache scorer run measures cold finishing/checking, not original
   construction wall time. Report its cost separately, or explicitly add the
   recorded construction time as a reconstructed two-stage total. The filtered
   constructor run itself provides direct end-to-end timing.
3. Pin upper-index/list/C3 certificate evidence in the proof manifest; reject
   cached proof rows whose source hashes, seed, upper or evidence do not match
   the current frozen protocol. The initial `proofs.py` cache check checks only
   input and budget, so safe reuse currently depends on the owner preserving
   the other files unchanged.
4. A proof-count `proved` flag based on a supplied historical upper remains
   provisional until that plan receives the promised fresh full-check gate.
   A changed finishing plan must use its actual checked lifts for equality.

No numerical job or test was run for this follow-up. Constructor regression
tests were read as source; their execution remains the owner's responsibility.

## Follow-up: optional weather-KG projection

Reviewed `medium_physics.py`, `physics_bound.py`,
`medium_physics_review.md`, their source-pin manifest and test source. This
review read the authoritative loading, criteria, stability, liquid/tank and
hydrostatic sources; no numerical work or validation outcome was read.

The proposed algebra is a valid necessary **departure** inequality:

1. Seagoing `weather_table_applicability` is mandatory and cannot receive a
   scalar policy waiver. Its Boolean condition requires virtual `KG/d <= 1.5`.
2. The weather caller supplies `condition.kg_m + condition.fsc_m`, with upright
   physical cargo/tank moments and nonnegative free-surface correction. Thus
   physical upright KG is no larger than the virtual KG used in this test.
   This claim does not identify the separate lashing/visibility CG provider
   with stability's provider.
3. `FloatingCondition.mean_draught_m` is the average of the same aft/fwd values
   to which the seagoing hull/port limits are applied. No hydrostatic-table
   coverage bound or displacement-to-draft approximation is used here.
4. The maximum displacement is the separate `_loading_policy` manual limit,
   checked against actual `condition.mass_kg`; it is not a table's end row.
5. Stability's cargo moment uses ordinary box height, mean supporting floors
   and one half own height. A legal prefix-supported decision column has k
   boxes below ordinal k, so k times the minimum ordinary decision-box height
   is a lower floor increment. Fixed background remains in its immutable
   aggregate moments and is not added again as decision-box mass.
6. Tank mass is nonnegative and bounded by declared capacity*density. The
   actual upright centroid lies within its raw solid's validated z bounds.
   Hull-section solid validation permits only 1e-8 m extent tolerance, covered
   by the proposed 1e-6 m lower-coordinate allowance. Negative-benefit tanks
   and operational/fixed-fill constraints can be discarded to enlarge the
   projected set. Fractional knapsack is exact for the remaining independent
   capacities plus total-mass cap, including fractional fills and negative
   benefit coefficients.
7. Downward integer rounding of cargo coefficients and upward rounding of
   the right side preserve a necessary row. Restricting intermediate tank
   operations is not required, because a checker-passing departure itself
   must have a feasible endpoint.

**Guard gap in the initially reviewed implementation:** raw geometry/tanks are
loaded via `hull_offsets_ref`'s stem, but authoritative stability `_context`
loads via `vessel.slice_of or vessel.id`. Matching only L/B/D does not establish
that these providers have identical tanks or capacity geometry. Require those
names to match, or consume the authoritative context with an equivalent
parameter identity check. Until then, block an unqualified global-admissibility
claim for arbitrary supported inputs; this is not evidence the three named
TRAIN inputs actually have mismatching providers.

Source pinning should also retain `hydrostatics.py` (the exact mean-draft
property), `hull_tanks.py` (section bounds/centroids) and the sounding/FSM helper
sources used for the nonnegative-free-surface/centroid premise. The parameter
file digest must name the same provider used by the actual stability checker.
The current record of the parameter digest is useful provenance, but a source
change must disable the optional row unless its derivation has been reviewed.

The fixed 1e-6 m, 1 kg and 1 kg m margins exceed ordinary floating evaluation
errors for moderate canonical fleet inputs; they are not a scale-independent
roundoff proof for arbitrary unbounded coordinate, mass, fixed-load-cancellation
or tier-count inputs. Either constrain and verify a finite numeric domain with
an error bound or retain this explicit numerical/canonical-fleet qualification.
`Fraction` makes the projection arithmetic exact after conversion; it does not
make all upstream checker floating sums exact. Synthetic knapsack tests and
projection of known passing TRAIN plans are useful implementation checks but
do not replace these guards. No validation outcome should select a tolerance.

The optional module leaves the eight frozen C3 global-bound dependencies and
the public `certified.lower_bound` interface untouched. It imposes no current
load-once restriction. A raw row or CP result does not supply a full-checker
pass; equality still needs fresh upper evidence and the exact scope sentence:

> fewest lifts among plans the checker passes, counting later ports by our D15 projection (fixed stow, no later loads)

### Weather guard follow-up

The revised optional implementation now requires
`(vessel.slice_of or vessel.id) == hull-reference stem == 'feeder_500'`, pins
that canonical parameter file's SHA-256, and consumes authoritative
`stability._context`. The substantive provider mismatch above is resolved.
Its source manifest also pins the requested hydrostatics, hull-section,
sounding, tank-envelope/transfer, liquid and parameter-loader dependencies.

The row now explicitly rejects more than 1,000 mass terms, total potential
mass above 1e9 kg, individual mass above 1e8 kg, |vertical input coordinates|
above 1,000 m, support ordinals above 100 and ordinary heights above 10 m.
It records the resulting canonical-fleet/finite-domain numerical scope.
These fixed generic guards are not selected from validation outcomes.

Conditional source-level approval for this pinned finite-domain experiment:
the algebra and outward integer directions are sound, and the identified
provider blocker is closed. Preserve the explicit qualification that finite
guards and micrometre/kg allowances are not interval-certified all-fill
sounding/hull arithmetic. Execute the queued synthetic and known-passing-plan
projection tests before numerical use; omit the optional row on unsupported
evidence. The eight frozen global-bound dependencies and public API remain
unchanged. This follow-up itself ran no numerical job or validation read.

