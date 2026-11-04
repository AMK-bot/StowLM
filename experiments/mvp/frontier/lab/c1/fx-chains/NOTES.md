# Capacitated chains and Hall screens

Scope: only this directory changes. Native source, parameters, checker and
finisher are imported unchanged. No sealed data or evaluation-only corpus is
used. No code is copied from papers. The joint placement model is independently
written using native feasibility tables; its formulation is also informed by
the existing `master.py`, `construct.py`, and `carrier_v6.py`. See `RULES.md`.

## Model on actual maps

For validation `random.small.40100000.0.2` (13 boxes, 16 physical stacks), and
TRAIN `random.medium.185100000.0.2` (121 boxes, 64 physical stacks), make one
vertex per load-list box. Put a directed edge from a possible lower box to an
upper box when the lower box's discharge port is no earlier and its mass is no
smaller. Equal attribute pairs break ties by box ID. This is a partial order.
A path is a zero-overstow, zero-mass-tolerance stack chain. Duplicate every
vertex into left and right copies and find a maximum bipartite matching; each
matched edge joins two chains. The minimum chain count is n minus the matching
size. An alternating search also yields an antichain of the same size, a
checkable optimality witness for this restricted object.

Both example maps have width 2. Indeed all twelve medium12 maps have width 2
in both exact-mass and class-rank variants (`results/chain-bounds.json`). With
only two ports this bound is intrinsically weak: each port's boxes can always
be sorted by mass into one unlimited chain. This says nothing about fitting
many short chains into real stacks. Capacity, bookings and paired long-box
geometry are the hard part, not unlimited chain decomposition.

A physical twenty-foot slot consumes one cell; a forty/forty-five-foot slot
consumes two cells in distinct physical stacks. Native tables specify size,
reefer, DG and special-request domains. Joint CP-SAT placement enforces
nonoverlap, support/no gaps, native class/mass ordering, individual stack mass
and height, hold size purity, top-only closure, and equal *actual* supporting
roof heights for long boxes. Port ordering is a search restriction, then is
relaxed in subsequent arms. Native mask replay, departure E6 and securing
geometry remain acceptance gates. Full physics remains with the finisher and
unchanged checker.

A lift is not an edge count in this graph. Yard blockers, current/future hatch
operations and future restows depend on the expanded loading/discharge replay.
`alns.Problem.score` computes that objective in the real units. Port-monotone
chains avoid one source of future restows; they do not optimize yard digging
or hatch lifts. Native legal stows can violate port ordering and pay restows,
so failed chain packing is never a proof that the original map is infeasible.
Positive native E2 tolerance is also nontransitive; the constructor compares
every upper box to every lower box instead of treating adjacent edges as a
complete legality test.

The first giant slot-assignment trials found no incumbent on the first
121-box map in two 90-second trials, and the first two medium12 maps also
returned no candidate at 120 seconds each. These are bounded negative results,
not infeasibility. `model-first121.json` uses an earlier stronger full-hold
restriction; `model-second121.json` and `results/medium12-120-f300/` retain the
later model's evidence. The exact singleton arm can spend its entire slice
in CP-SAT presolve; grouped arms do branch but did not find a witness.

`pattern_model.py` replaces most slot choices by whole-column patterns. A
pattern is a complete bottom-up chain of twenty-foot boxes, or a synchronized
pair of columns of long boxes. A smaller master chooses patterns that cover
each cargo group exactly and share no physical stack. The implementation
samples patterns, retains all their prefixes, and anchors rare cargo groups.
It does not implement exact pricing/column generation: a deficient sampled
pattern pool is not an infeasibility certificate. Homogeneous deck columns
and fixed whole-column long pairs are additional restrictions. `method.py`
uses this model first on maps above 60 boxes, then spends the remaining budget
on joint slot assignment. Small maps use joint assignment directly.

## Safe flow bounds

`hall.py` instead makes a true relaxation: a box can send its one/two cell
units to any cell appearing in any compatible static footprint, even if those
units cannot form a real long slot. Unsupported upper tiers remain available.
Independent optimistic flows test stack mass and total height. A deficient
minimum cut names boxes whose joint demand exceeds all their compatible
resources; the returned integer inequality is independently checked. See
`HALL.md` for assumptions, rounding and prefix semantics.

Fresh root flows on all twelve medium12 maps show **no cell, mass or height
deficit** (`results/hall-medium12.json`). All maps remain unclassified, with
no feasibility or infeasibility proof. A flow pass is not a complete stow.

## Sources

- R. P. Dilworth (1950), [A Decomposition Theorem for Partially Ordered Sets](https://mathweb.ucsd.edu/~asuk/dilworth.pdf): unlimited chain cover equals maximum antichain. We implement matching and retain both witnesses.
- D. R. Fulkerson (1956), [Note on Dilworth's Decomposition Theorem for Partially Ordered Sets](https://www.ams.org/proc/1956-007-04/S0002-9939-1956-0078334-6/S0002-9939-1956-0078334-6.pdf): reduction to bipartite matching.
- P. Hall (1935), [On Representatives of Subsets](https://doi.org/10.1112/jlms/s1-10.37.26): pooled compatibility deficits.
- L. R. Ford Jr. and D. R. Fulkerson (1956), [Maximal Flow Through a Network](https://doi.org/10.4153/CJM-1956-045-5): max-flow/min-cut certificates.
- C. Greene and D. J. Kleitman (1976), [The Structure of Sperner k-families](https://scholarship.haverford.edu/mathematics_facpubs/171/): reviewed as a stronger bounded-chain direction; not implemented, because unequal physical stack capacities and coupled long slots need additional constraints.
- P. C. Gilmore and R. E. Gomory (1961), [A Linear Programming Approach to the Cutting-Stock Problem](https://pubsonline.informs.org/doi/10.1287/opre.9.6.849): the idea of selecting whole feasible packing patterns rather than individual item placements. Our sampled integer master omits their dual pricing and does not inherit their guarantees.

## Verification and measurement protocol

`verify_graph.py` exhaustively compares matching widths and chain/antichain
witnesses against brute-force antichains on 1,365 tiny attribute assignments.
`hall.py --self-test` checks five flow/certificate/rounding cases. Construction
and full measurements follow below once complete. `method.py:propose` is the
shared scorecard entry point. It explicitly labels stored-plan fallbacks;
those passes must not be attributed to the new constructor.

The 60-second finisher pilot (`results/quick20-f60/`) was cooperatively stopped
between maps after four rows: two stored fallbacks timed out and two genuinely
constructed tiny stows passed at 8 and 6 lifts. It is an incomplete pilot,
not a quick-set estimate; helper sources were evolving during that pilot.
Its native complete candidate actions are retained under `results/candidates/`.
The two-map giant-assignment cohort pilot was likewise stopped between maps
to replace its unproductive formulation with pattern packing. Cooperative
stop markers target only PIDs directly recorded by this method; no unrelated
process is inspected or signaled.

Final runs use `method.py:finish`: unchanged V4 `finalize`, then the shared
runner's fresh full checker. This avoids the default adapter's duplicate full
check (`planner._finish` checks once, then `score.py` checks again). No physics
rule or acceptance gate is changed. Final quick budget is 30 seconds proposal,
300 seconds finishing, up to two candidates; medium12 uses 120/300 seconds,
one candidate. One worker per run, both concurrently (two busy slots total).

The hard exact-cover pattern pilot also returned no candidate on its first
four 121-box medium maps, then was cooperatively stopped. The final medium12
constructor is `optimized_patterns.py:propose`, retaining the same conservative
pattern grammar but maximizing coverage before demanding exact cover. Empty
and partial covers give it feasible incumbents and warm-start hints. The
primary objective is number of covered boxes; rarity is strictly secondary.
Only all-box covers can reach native validation, the finisher, or lift scoring.
See `OPTIMIZED.md`. Partial coverage is **not** a stow, feasibility result or
lift result. A separate final fixed-set directory is used:
`results/medium12-optimized120-f300/`.

Parent verification passed: 1,365 exhaustive graph cases, five Hall cases,
five exact tiny pattern-master cases (including stack exclusion, manifest
upper bounds and strict objective priority), syntax compilation, and all 25
shared scorecard tests. The scorecard's real-checker rejection test took
57.7 seconds; it did reject the deliberately illegal finished plan.

The homogeneous optimized pilot reached 117/121 on seed 185100000, which is
only partial packing evidence. Inspection of the two *existing* complete
229-box witnesses in `strategy/blockrule/runs/medium-train/maps/` found 10
mixed deck columns on seed 185100100 and eight on 185100111. The homogeneous
grammar cannot represent either complete witness. The first has 128/132 hold
cells occupied, so a compulsory-full-hold arm also excludes it. Their native
historical physical-replay flags and plan/input hashes agree with the stored
summaries. This is source inspection, not a fresh legality/pass claim. These
witnesses are not returned by our constructors or counted as new results.

`mixed_patterns.py` adds pairs of independent twenty-foot bases followed by
synchronized long tops on deck. Each paired base level uses equal actual
heights, a sufficient support condition; both columns retain independent
all-lower weight limits. Mixed stack rating uses the smaller of the native
mixed and checker twenty-foot ratings. Holds remain homogeneous. The new
`optimized_mixed.py:propose` copies our coverage-search machinery and uses
this extended pattern class. See `OPTIMIZED_MIXED.md`. It is the final
medium12 constructor; its fixed-set results directory is
`results/medium12-mixed120-f300/`. All prior incomplete cohort pilots remain
explicitly separate, with their bounded negative outcomes retained.

## Final measured results

All figures below are fresh shared-scorecard runs. Lift means use only mutual
passes with stored 0.9.0, separately by size. Stored medium 0.9.0 evidence is
unmeasured in the shared manifest, so a missing medium row is not counted as
a measured baseline failure. The historical D185 0/12 is background evidence
from the contract, not a paired rerun.

| Fixed set / size | Passes | Stored 0.9.0 passes | Paired lifts, ours / stored | Mean wall s/map |
|---|---:|---:|---:|---:|
| quick tiny | 8/8 | 8/8 | 6.375 / 6.375 (8 pairs) | 82.962 |
| quick small | 9/10 | 9/10 | 13.889 / 13.889 (9 pairs) | 84.229 |
| quick medium | 0/3 | unmeasured | no pairs | 124.221 |
| medium12, mixed patterns | 0/12 | unmeasured | no pairs | 121.053 |

Quick totals: 17/21 passes, 1,878.644 cumulative serial wall seconds (31.31 min).
There were 18 newly constructed complete native-legal assignments: six tiny
ones passed the finisher and full checker; eleven other new assignments were
not finished because a cheaper stored fallback passed first; one 88-box medium
assignment reached finishing and failed. These outcomes are enumerated in
`results/construction-summary.json`, with full candidate actions retained under
`results/candidates/`. Thus the 17 passes include **six constructor passes and
eleven stored fallbacks**, not seventeen cold-start successes. No cold-start
speedup is claimed from comparison with a stored-plan fallback.

The 88-box, 60%-fill feeder_500 stow is native-mask and carrier legal, built in
8.97 s by homogeneous patterns, and scores 121 lifts. V4 returns
`not_found / timeout:worker` after 300 s. A separate fresh full checker at the
explicit original adjustable-tank masses rejects **securing and trim** in
15.79 s (`results/failed-medium-check/` retains the plan and report). This
tests one tank vector only; it neither proves that fixed stow unfinishable
nor classifies the map. The other two quick medium maps returned no complete
candidate. These are construction/finishing outcomes, not move-quality wins.

The final mixed-pattern medium12 run takes 1,452.638 cumulative serial wall
seconds (24.21 min) and returns **no complete native-legal stow on any map**.
Partial coverage is retained solely as search telemetry. No medium12 plan
enters finishing or the full checker. The exact/mixed grammar and search
budget are insufficient on the four maps without the mask obstruction below;
their original feasibility remains unknown.

An independent HiGHS backend (`highs_patterns.py`) passes three tiny master
checks, then covers 117/121 boxes on diagnostic TRAIN seed 185100001 in 60.38 s.
Its restricted sampled-pool upper bound is 119 boxes, proving only that **this
generated pattern pool** cannot cover the whole manifest. No original-map
bound follows. This map was selected diagnostically because it lacks the OOG
mask obstruction, not as an unbiased sample. See `HIGHS.md` and
`results/highs-one60-f300/`.

## Concrete mask obstruction and a checker-feasible counterexample

`MASK-OBSTRUCTION.md` independently audits a legacy native-mask parameter
resolution mismatch affecting **8/12 D185 maps (11 required boxes)** and the
failed quick-small map. `_e9_batch` reads an explicit raw OOG clearance, sees
`None`, and unconditionally rejects width/length OOG boxes. The unchanged
checker instead resolves the valid catalogue default `0.0` and checks actual
geometry. Every affected input and relevant source hash is retained. The
document traces forced-restow recursion and states the load-once-interface
scope precisely. This is a **native construction obstruction, not a physical
or full-checker infeasibility certificate**. No mask, checker, parameter,
catalogue activation or input was changed to avoid it.

The explicit manual small seed in `special_chain.py:propose_hand_seed` reaches
11/12 through native loading; only the isolated OOG remains. A reproducible
fresh probe (`verify_mask_obstruction.py`) confirms an open slot, exact
`placement_violations == []`, raw clearance `None`, resolved clearance `0.0`,
and native fast E9 / `is_legal == False`. The original heat/OOG geometry model
improves heat handling but cannot repair this separate mask defect.

`diagnose_small_checker.py` expands that explicitly labelled manual seed
without pretending it is mask legal, then calls unchanged V4 `finalize` and
the unchanged full checker. V4 succeeds in 54.24 s; the independent full check
passes in 19.55 s with **14 lifts, zero violations**. The input hash is unchanged.
The finished plan and input-bound full report are in
`results/small-checker-diagnostic/`. This proves that this particular small
input has a checker-feasible stow despite the native mask obstruction. It is
**excluded from the mask-legal quick score**, not a new algorithm success and
not evidence that any D185 map is checker feasible.

## Verdict and next step

**Drop the current bounded chain constructors as a scalable improvement;
keep the mask-obstruction finding and diagnostic artifacts.** Uncapacitated
chain cover is easy and uninformative here; the sampled pattern models need
better generation/pricing and less conservative packing. Hall relaxations
remain useful necessary-condition tools but give no original-map certificate
on these inputs. The immediate prerequisite is an owner-reviewed native-mask
parameter-resolution fix and regression against the exact special checker,
then fresh fixed-set measurements. Such a source fix is outside this folder's
write scope and was not made. After that, target the four unobstructed D185
maps with mixed-pattern pricing driven by uncovered groups and LP duals.

Final verification reran all 25 shared scorecard tests after the shared lab
runner's development updates; all passed (20.3 s for its real-checker negative
case). All experiment Python files compile. The reproducible exact-versus-fast
mask probe passes; the manual small full-checker witness and rejected medium
representative both retain their exact plans, full reports and input hashes.
`results/verification.json` records final source/artifact hashes and report
bindings. Solver sources were frozen during each final fixed-set run; each
row also retains source fingerprints. The four incomplete pilot runs are not
combined into the fixed-set estimates. No background jobs remain.

Numerical workers use one native thread. At most two CPU-busy processes run
across this team. All long runs request `nice -n 5`; this host rejects
setpriority with `Operation not permitted` but runs the job. No unrelated
processes are stopped. The fixed 21-map quick scorecard may exceed 20 minutes:
it requires up to 300 seconds of V4 finishing per candidate, and shortening
that gate caused tiny stored fallbacks to time out in the 60-second pilot.
This is a coverage/verification run, not a long search on one selected map.
Medium12 construction uses a bounded 120 seconds per map; its full cohort
runner may likewise exceed 20 minutes because all twelve maps are required.
