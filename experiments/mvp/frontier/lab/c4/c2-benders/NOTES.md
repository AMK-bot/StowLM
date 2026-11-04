# Cycle 4: cold area/securing pipeline and layout

## Final measured outcome

**Cold reproduction was not achieved.** The initial frozen `medium12` TRAIN
cohort has **12/12 maps with complete replay-legal stows**, 27 distinct
constructor stows, 26 ballast-independent securing certificates for fixed
cargo stows, and **0/12 full-checker passes**. Mean wall time412.222126s/map;
all map-specific counts, binding rules and times are in `RESULTS.md` and the
source-bound consolidated audit `results/frozen-medium12-01`.

Two later profiles were tested on the first map only, not retrospectively
substituted into that cohort: corrected-first-01 failed in1006.378162s;
projected-first-01 failed in1111.409674s. The latter restored all five pair
finite-difference directions but still failed arrival
`containership_area_0_flood_mrad`:0.284925904362 versus0.291225632914mrad,
a deficit0.006299728552. Securing/loading pass; all23 native report parts
ran without errors, and exact input/plan/cargo/tank/source bindings match.
Its112lifts are diagnostic and supply no passing lift mean. Runtime
linearizations are search guidance, not outward infeasibility certificates.
Other cargo arrangements and unsearched tank families remain unknown.

**Separately warm:** the archived C3 witness was improved from114 to100
weighted projected lifts, later-restow lifts58 to44. A fresh independent
whole-checker run verified121/121 requested boxes, zero violations, all23
parts, in25.9362s. This is not a cold pass; verification time is not search
time. The fixed-stow D15 projection assumes no later loads and measures
projected later-port work, not terminal operations or a global optimum.

Fresh E9-fixed0.9.0 returned no candidates/passes on all12 development maps,
mean251.873628s. That does not establish internal construction counts.
Budget allocations differ and machine load is not matched; there are no
mutual passing lift pairs, fair speedup claims or stored-row comparisons.

Verification: the complete root-level C4 test suite passed against the final
23-file source snapshot; shell syntax passes. Final constructor/source and
native report binding audits are under each run. `audit.py` also inspected
the final projected first-map row with zero issues. All experiment processes
exited normally; none was killed. No cold outcome is inferred from a local
LP rejection, a timeout or a failed diagnostic lift count.

Keep the fixed-stow securing screen, consistent native-TCG projection and
verified warm lift reduction; the tested cold pipeline remains unresolved.
Next, reserve final native refinement for the best protected incumbent
before a weaker repair branch. In the final run the best descent cargo
stopped after five iterations; about180s then went to a worse CP branch,
leaving no final best-point polish and4 of16 SLP iterations unused. Further
work should also price securing-safe patterns and penalize cargo transverse
moment. These are future proposals, not measured gains.

Reproduce one map with `sh run-map.sh MAP.json FRESH-OUTPUT-NAME`; the
launcher calls the shared `score.py` propose/finish interface. Cold entry is
`method.py:propose` plus `method.py:finish`. The final source snapshot is
`results/projected-profile-source-01`; initial and intermediate snapshots
remain preserved. Numerical threads are one, and no job is currently active.


Scope: write only this folder, no sealed data, no commits or checker changes.
The complete contract and Cycle 4 corrections were read. BENCH-RUNNING exists
at admission: at most **one CPU-busy process** across the whole team, single
numerical thread. Code/review agents do not start numerical jobs without a
recorded root admission. Every long job records its managed session and PID;
only owned processes may be stopped. Attempt `nice -n 5`; this sandbox denies
setpriority, in which case the underlying one-thread Python still runs.

## Model

The master selects concrete ordered stack patterns covering every box once,
with overlapping physical footprints mutually exclusive. Cover-to-port and
closed-hold choices control the later-port projection; three actual voyage
states couple cargo moments to tank masses through native righting-area,
securing, draught, trim and sight checks. A legal complete stow is counted
separately from an unchanged `stowage.checker.check` pass.

The inherited small illustration has 18 boxes, 20 physical stacks and two
covers. The actual first D185 map has 121 requested boxes, 64 physical stacks
and six covers. Lift costs are the native weighted sum of relocations,
current/later restows and current/later hatch lifts. Later-port terms use the
D15 fixed-stow/no-later-load projection; they are not measured terminal work.

## Cold boundary and reuse

Production entry points accept only the live instance plus settings and
current-run objects. They ignore shared-score stored assignments, plans,
recipes and baseline rows. No C3 result or historical incumbent is loaded by
the cold pipeline. The map's declared initial tank state is valid input;
generic geometry-based seeds and numeric vessel tables are derived afresh.
E9's authorized process-local mask fix is installed before constructing
feasibility tables; any gain solely due to E9 is a bug fix, not a solver gain.

`page_tools.py` and `permutation.py` copy C3's reusable refine/box-exchange
APIs, excluding their historical/archive CLI branches. They use only a plan
constructed in the current run. Native physics separation is copied to this
folder with its cache path changed. Stable C1/C2 page, pricing, replay and E9
code may be imported; C3 unused-cover logic is copied without its warm
reference loader. Mutable `engine/` is not imported. All copies are documented
in source; native checker and rule providers remain unchanged.

## Mathematical references

- Gilmore and Gomory (1961), [A Linear Programming Approach to the Cutting
  Stock Problem](https://doi.org/10.1287/opre.9.6.849): stack patterns, covering
  master and knapsack pricing.
- Sadykov, Vanderbeck and Pessoa (2019), [Primal Heuristics for Branch and
  Price](https://doi.org/10.1287/ijoc.2018.0822): restricted integer masters and
  constructive diving. The finite pool/rounded pricing are search heuristics.
- Griffith and Stewart (1961), [A Nonlinear Programming Technique for the
  Optimization of Continuous Processing Systems](https://doi.org/10.1287/mnsc.7.4.379):
  successive local linearization of the exact ballast margins.
- Broyden (1965), [A Class of Methods for Solving Nonlinear Simultaneous
  Equations](https://www.ams.org/mcom/1965-19-092/S0025-5718-1965-0198670-6/):
  secant updates. Local failure is always unknown, not physical infeasibility.

No downloaded code is run. These references carry forward the reviewed
source algorithms, not new claims of global convergence or optimality.

## Measurement protocol

First reproduce 185100000 cold; then freeze the method/settings and run all
twelve development `medium12` inputs through the unmodified shared scorecard.
Per-map construction, ballast and independent-check stages share up to 1,200
seconds; work-count caps and machine load are recorded. The fixed cohort run
may exceed twenty minutes in total because it measures twelve separately
budgeted maps, never because one failed stow receives unlimited search.

Every map reports complete-replay-legal coverage, full-check status, native
lift parts when passing, time, and binding native rule/metric on failure.
Budget exhaustion is unknown. No whole-map impossibility is inferred from
failure of these constructive families. The existing C3 pass is a TRAIN
development witness; all twelve maps are development data.

Stored 0.9.0 rows are historical context only. Any claimed lift/runtime
comparison uses a fresh E9-fixed 0.9.0 arm at the same load/budgets, following
`c1/leader/method_e9_val.py`. V6 is a constructor designed to pass and is not
used as a lift benchmark. Results and exact commands will be appended below.

## Read-only diagnostic: how much cargo-KG repair can this topology offer?

Ballast search cannot change cargo KG. A cold cargo KG near 9.8 m does not by
itself establish whether cargo repair can reach 9.51 m: the latter is the
earlier targeted witness's cargo value, not a regulatory limit or a universal
requirement for full-checker acceptance. Current LCG, stack securing, ballast,
and the actual righting-area form factor also matter. No sorting value for a
fresh 9.8 m candidate has been measured in this read-only analysis.

The existing `permutation.py:optimize_assignment` already reports
`group_sorting_relaxation.cargo_kg_m` and `improvement_room_m`. For each current
exchange group it matches the largest concrete masses to the lowest currently
occupied physical heights. The rearrangement inequality makes that the least
vertical moment possible if every box in the group could use every occupied
position in the group. It ignores eligibility, stack mass, E1/E2, fore-aft and
transverse moments, and reference securing. Those omissions enlarge the family,
so this is a necessary lower bound for the requested cargo-KG target within
these fixed occupied slots and groups, subject to ordinary floating arithmetic.
It is neither a whole-map lower bound nor a physical infeasibility certificate.
Physical heights are the native stability-loading convention, not expanded OOG
silhouette heights or the ship's total KG.

If this relaxed value is above a proposed cargo-KG target by more than numerical
roundoff, extra CP time cannot reach that target inside the same groups and
occupied topology. A lower relaxed value only indicates potential room; native
constraints can still prevent attaining it. Groups currently retain length,
physical height, ISO type and reefer status, with protected boxes fixed; the
optional category/rank/heavy relaxations do not release the occupied topology.

No extra diagnostic computation is needed: the value is assembled in
`permutation.py` immediately after the baseline/variable metadata and before
fitted securing rows. `repair.py` embeds that row in the
`fixed_topology_exchange` member of `attempts`; the finisher retains it under
`finish-metadata.json -> repair -> attempts`. No production-source change is
required to collect it. A future display-only summary could copy those two
fields after `meta['repair'] = repair_meta` in `ballast.py`.

For a topology with too little room, an efficient next search is a pair of
disjoint legal hold/deck exchanges with opposite longitudinal-moment changes,
or repricing a small set of top-heavy deck and low hold columns together.
Current greedy descent accepts improving moves one at a time and checks LCG
after each; it can miss a jointly balanced two-exchange move. The exact whole
column, replay and full-check gates must remain authoritative. These are proposed
current-run neighborhoods, not measured improvements or archived starts.

## Development failures and corrections

The first 180 s constructor probe found no complete stow (132.3 s actual).
Its continuous master covered every box, but short integer/tail searches did
not complete. Restoring the unmodified C2 RMP model for the first reference
fixed this: `first-cold-01` produced a complete 121-box, 118-lift stow in
11.15 s and measured the full pipeline in 180.76 s. It gave **zero full-checker
passes**. The source spring-chain bound proved its five-high deck chain has
rod ratio at least 1.1480357 for every ballast setting; no ballast search or
native full-check call was spent on that fixed impossible arrangement. This
is not a map-level proof. `ADAPTER.md` preserves the independent row audit.

The second constructor-only probe preserved native construction through the
first physics rung, then applied fresh vertical descent and soft cover costs.
In 180.13 s it generated two complete replay-legal stows (118 and 116 lifts,
cargo KG 10.1243 and 10.0562 m); physics/layout alternatives still failed to
complete. These are development construction measurements, not cold passes.

A diagnostic on the second probe's own freshly constructed stow found the
reason to change its pricing reference. Native initial tanks give securing
GM 1.32772 m and end draughts 5.88997/7.59439 m. The ordinary C2 affine seed
algorithm with the native minimum-GM floor gives a fresh reference with GM
.91434 m and draughts 6.39298/6.76100 m. Floor .6 instead gives GM 1.41313
and forward draught 7.16617 m, still over the limit. Thus the next constructor
derives ballast from its own current-run stow before building securing and
sight references. No stored mass recipe is used; every final candidate still
needs full native checking. Diagnostic data are in `results/reference-probe.json`.

The staged finisher now reserves time for cargo repair and **reoptimizes tanks
for the repaired cargo**, retaining the cargo/evaluator/vector/result as one
record. Direct native forward equilibrium supplies mass-compensated search
directions; all three real GZ/securing states still decide acceptance.
Twenty-three C4 tests pass, including a regression whose repaired cargo passes
only after a fresh ballast stage. The shared runner itself is unchanged.

`permutation.py` adds cover-opening/same-stack D15 restow costs as a search
objective under vertical-moment, longitudinal, transverse, weight-order and
reference-securing restrictions. Native replay scores actual projected work,
including policy/crossing costs omitted by the surrogate. `repair.py` exposes
an optional post-pass polisher; it accepts improvements only after a new full
checker pass. Its tiny cover scenario verifies that an extra later box above
an earlier hold discharge adds two projected restow lifts.

Load during the first cold run was sampled at 33.19/30.31/23.42 (1/5/15 min).
Those timings are load-dependent descriptions. All runs stayed serial while
the benchmark marker existed; unlike Cycle 3, no overlap has been admitted.

Further constructor probes reached zero fractional box shortfall but no
physics-guided integer cover. The first HiGHS recovery accidentally made the
native LP's penalized longitudinal target a hard constraint; its presolve
infeasibility therefore said nothing about the original soft model. Recovery
now retains a continuous nonnegative moment slack, minimizes it, and reports
its value separately from physical trim. A real tiny regression distinguishes
the hard and soft cases. Current-pool greedy seeding also checks the original
securing gate at each append, retaining mass-tight patterns that rounded
knapsack pricing may miss. No physical rule has changed. The full C4 test
suite passed 31 tests before this seeding integration; new tests are rerun
before the next constructor probe. All these are development attempts, not
additional cold passes or a claim of whole-map infeasibility.

## Probe 06: first cold candidates not ruled out by securing

`results/cold-constructor-06/` is a **development constructor probe** on the
first fixed `medium12` map, `random.medium.185100000.0.2`, seed 0. Its 235 s
construction allowance used 221.47 s. The entire then-current C4 test suite
passed before construction. The one numerical process was PID 69136
(session 38826), with one numerical thread; it exited normally. No full
checker or ballast-finishing search was run in this probe.

| Candidate | Complete replay-legal boxes | Lifts | Cargo KG, m | Securing rod lower bound | Bound verdict |
|---|---:|---:|---:|---:|---|
| `cold-000`, native plain | 121/121 | 118 | 10.124290 | 1.148036 | This fixed stow cannot pass for any ballast |
| `cold-001`, fresh descent | 121/121 | 116 | 10.056194 | 1.147398 | This fixed stow cannot pass for any ballast |
| `cold-002`, fresh affine reference | 121/121 | 114 | 9.848319 | 0.819359 | Not ruled out; feasibility unknown |
| `cold-003`, its fresh descent | 121/121 | 114 | 9.784813 | 0.917038 | Not ruled out; feasibility unknown |

Thus the gate counts are **four complete stows; full-checker passes not
measured**, with two candidates surviving this necessary securing screen.
A bound below one is not a proof of feasibility. Each concrete plan,
construction record and independently evaluated securing bound is saved
separately beside the aggregate `metadata.json`. There is no fresh paired
0.9.0 comparison from this probe and no claim of a lift improvement.

The change that made these candidates available was to protect scarce
20-foot positions from earlier long-page choices. The previous greedy
attempts consistently stopped at a heavy 20-foot reefer or deck DG box:
sorting rare boxes only *within* the long and 20-foot groups could not reserve
their stacks before long pages consumed them. Sixteen bounded trials now
alternate length order, special-20-foot priority, static eligible-page rarity,
and best-fit versus low-height preference. Every append still goes through
the original column legality and reference securing gates. Failed trials add
only their individually gated partial patterns to this invocation's pool.

All direct trials still failed to cover the whole ship. Their new patterns
nevertheless enabled the native affine-reference rung's subsequent dive to
complete. The native-instance-tank rung did not complete. Both rungs tried
the new pool MIP and CP fallback; neither recovery solver supplied an
incumbent. This result therefore supports the combined new-pattern-and-dive
path, not a claim that the MIP solved integer recovery. The C1 fallback was
prepared but was not taken once the affine rung had completed.

References are explicit, fresh and physically applied. The constructor
derives one motion reference from this run's cargo at the instance's initial
tank masses, and another from the C2 affine algorithm at the ordinary
checker minimum-GM policy (0.15 m here). It resolves candidate declarations
through `PhysicsEvaluator.ship`; plain `apply_plan` would ignore the declared
ballast. No stored assignment, historical recipe or C3 witness is read.

The cheap rearrangement relaxation also diagnoses the next limitation.
With the present occupied slots, fixed special boxes and interchangeable
physical groups, the plain/refined baselines have cargo-KG lower bounds
10.027811/9.975911 m. The two new candidates have bounds
9.723264/9.695271 m. These relax static eligibility, E1/E2 order, stack mass,
securing and longitudinal/transverse moments, so an actual restricted
assignment can only do worse. Fixed-topology permutation in this family
cannot reproduce Cycle 3's approximately 9.51 m cargo KG. That does not prove
physical infeasibility: actual area and securing still depend on the new
geometry and ballast. If finishing fails, lower KG requires changing occupied
slots or broadening that stated exchange family, rather than spending more
CP time on the same topology.

The six constructor-only development attempts through probe 06 consumed
about **1,222 s (20.4 min)** in aggregate, in addition to the initial
`first-cold-01` full-pipeline development run and reference diagnostics.
This is tuning on development data, not one successful 235 s solve, and
must remain separate from the next fresh production-method measurement at
its declared 240 s construction plus 900 s finishing budgets. Production
Python sources were frozen after probe 06; this diagnosis changes only notes.

## First-cold-02 live diagnosis and proposed seed-regime patch

This is a **proposed change**, not code applied to the running method. Python
sources remain frozen while `first-cold-02` finishes; no numerical experiment
was launched for this diagnosis. The evidence is the saved trace under
`results/first-cold-02/details/random.medium.185100000.0.2-s0-p69857-427342499960041/finish-000/`.
The trace is partial, so these observations are not its final verdict.

No evaluated geometry seed empties the whole foremost wide mirror pair.
For the actual vessel, the pair is DB-F: native fills are `(1, 1)`, the fresh
affine seed is approximately `(0.034318, 1)`, aggregate/single redistribution
produces `(0, 1)`, and near-full seeds put both tanks back at `.99` or `.985`.
The mechanism is generic: `_narrow_redistribute` selects only individual tanks
with `.001 < fill < .98`; a pressed partner is excluded even when the pair
was selected. Near-full generation then uses `any(partner >= .98)` and writes
both partners, refilling the emptied side. Thus a whole-wide-pair-empty
regime is absent. This is a search-family omission, not a physical proof.

At 26 recorded evaluations, original-cargo SLP has reduced the best normalized
merit to 1.38919, but departure rod ratio is still 1.21191, forward draft is
7.21014 m against 7.05 m, and arrival area misses by 0.0137703 mrad.
Arrival raw upright TCG is already within about 0.00001 m of zero. Lateral
centering alone cannot explain these deficits. No native acceptance or
map-infeasibility claim follows from this partial trace.

The next patch should prioritize the missing tank regime before spending
more constructor time on a new cargo topology:

1. Add an explicit **whole selected pair** redistribution mode. Candidate
   pairs come from mirror geometry and high half-fill FSM/capacity, including
   pressed and full pairs whenever their combined present mass is positive.
   Rank the forward high-FSM pairs first, then by removable FSM and replacement
   LP feasibility; do not select by tank identifier. Set both selected
   partners to zero, then replace their total mass and longitudinal/transverse
   moments using available narrow tanks. Keep the original aggregate slack
   mode as a fallback. A failed replacement LP only rejects this seed; it is
   not a physical infeasibility certificate.
2. Preserve the selected empty-pair regime in its near-full descendants.
   Only adjust a retained low tank pair when **both** partners were already
   pressed in that parent seed. Do not refill a pair because only one partner
   is pressed. Generate `.99`/`.985` from the current run's retained pair
   masses and the native `.98` boundary, never from a stored recipe.
3. Deduplicate exact mass vectors before spending a native curve evaluation.
   Keep the existing total seed/work limits. Prioritize native initial,
   affine, foremost feasible whole-wide-pair replacement, its retained-pair
   near-full descendants, a second feasible wide-pair replacement, and the
   slack aggregate fallback; omit duplicate or failed proposals. Save each
   proposal's selected geometric pair, LP status, mass/moment residuals,
   changed fill regimes, and exact-check outcome.
4. If the compensated forward line remains unavailable, permit its drawdown
   control to be a full wider forward mirror pair, ranked by actual removable
   longitudinal moment. The primary pair may remain narrow. The width screen
   is a search preference, not a restriction on legal drawdown. Exact native
   draft root, raw-TCG balance and complete physics still judge every result.
5. Make `_balance` try more than one geometrically eligible occupied mirror
   pair. For each pair, use its actual mass and remaining capacity to derive
   the mass-preserving transfer interval, then evaluate the exact arrival
   raw-TCG residual at both endpoints. Select a bracketed pair, preferring
   narrow/high-leverage geometry, and apply the native root. If no pair
   brackets zero, retain the original vector and record that limitation;
   never claim centered from equilibrium list alone. The repaired cargo
   currently has arrival raw TCG about -0.00880083 m, despite its parent's
   near-zero TCG; the nearly empty preferred HEEL pair may not contain enough
   transferable mass. A stocked alternative pair can offer a legal root.
   Repair's CP transverse target is departure-oriented, while the binding
   containership area is arrival-oriented. Before evaluating each repaired
   initial point, construct its fresh evaluator and geometry, balance its
   carried current-run masses for exact raw arrival TCG, and attach that
   vector to the matching repaired plan. Preserve the atomic cargo/vector
   binding through all later stages and final acceptance.

Targeted validation after source-edit and CPU admission:

- A pure synthetic mirror-pair fixture with fills `(.03, 1)` must remove both
  partners in whole-pair mode, whereas the existing slack-only mode retains
  the full partner. Verify replacement mass, longitudinal moment and
  transverse moment within declared solver tolerances and tank capacities.
- A second fixture with two wide pairs verifies geometry-based forward
  priority, including a pressed pair; shuffled tank names must not change
  the selected physical pair. An infeasible replacement returns a rejected
  proposal with a diagnostic, never a proof or silently altered mass.
- Near-full fixtures with `(0, 1)`, `(0, 0)`, `(.985, .99)` must preserve the
  first two and adjust only the third pair. A whole emptied pair stays empty
  in every descendant. Verify deduplication and the seven-seed cap.
- A forward-control fixture with empty narrow forward tanks and a stocked
  wider forward pair verifies the proposed fallback is eligible and ranked
  by removable moment. Its line remains a search proposal requiring native
  checking, not a feasibility relaxation.
- A balancing fixture makes the preferred narrow pair fail its root bracket
  because it is nearly empty, while a stocked mirror pair brackets zero.
  Verify the fallback preserves each pair's total mass, all capacities and
  exact arrival TCG. A no-bracket fixture returns the original masses with a
  diagnostic. Extend the mocked repaired-stage test so the balanced vector
  is evaluated and remains attached to that repaired cargo, never its parent.
- Run the C4 regression suite, then a fresh fixed-budget cold invocation from
  the original map and instance tanks. Record full seed fractions, native
  all-three-state area/securing/draft margins, work counts, source hashes and
  unchanged full-checker outcome. Keep this tuning attempt separate from
  `first-cold-02`; never overwrite its evidence or use the C3 passing masses.

## Next proposed patch while first-cold-03 is frozen

These are **design notes only**. The running Python and its archived source
snapshot are unchanged. No numerical experiment accompanies this addendum.
The first-cold-03 live trace demonstrates both a valid new regime and a
local-search defect: symmetric WING-A fill from zero improves exact area,
but unavailable transverse derivatives can still acquire an unconstrained
LP step. Its first split at `(0, 0)` has no feasible finite difference;
nevertheless a combined proposal creates `(0, .06)` fill, deteriorating the
arrival-area deficit from about .05459 to .06594 mrad. A later asymmetric
proposal also deteriorates area. This evidence does not establish a general
first-fill free-surface barrier; symmetric `.006` and `.012` fills improve
the exact native result and are retained.

### 1. Pin unavailable derivative directions

Allocate `valid_fd = np.zeros(G.shape[1], dtype=bool)` with each new Jacobian.
Leave a direction false if `_fd_step` is zero, the selected free-surface
correction membership changes, required margin keys are absent, or a
derivative is nonfinite. Mark it true only after completing all checks and
assigning the complete margin column and raw-TCG derivative. Build the LP
direction bounds as `(-radius, radius)` only for true entries and `(0, 0)`
otherwise. Zero-initialized Jacobian columns must never imply that an
unmeasured physical direction is free to move. Preserve useful exact
finite-difference samples even when their derivative is unusable: they may
become the next stage's incumbent, with a fresh Jacobian around that point.
If no direction is valid, retain the best exact sample and end/restart the
local stage instead of repeatedly evaluating a zero step. Record invalid
direction indices and reasons in metadata.

Regression: provide an empty mirror pair whose P/S split has no feasible
finite difference, plus a valid symmetric-fill direction. Capture the real
LP variable bounds and candidate vector, verifying the split receives
`(0, 0)` and cannot create an unmodeled asymmetric fill. A second fixture
changes correction-tank membership on a feasible finite difference and
verifies that direction is also pinned. Check that a favorable exact sample
across the branch may still be retained without treating its derivative as
valid, and that the next Jacobian does not reuse validity from the prior one.

### 2. Add large narrow-pair fill rays from current loading headroom

For each feasible whole-wide-pair replacement, preserve that selected pair
at zero. Keep retained low wide pairs pressed. Choose two available narrow
mirror pairs from vessel geometry with longitudinal centers `x_a < L < x_f`,
where `L` is this current candidate's exact ordinary departure loading LCG.
Rank by low FSM/capacity, low vertical center and usable capacity; the pair
identifiers and historical successful masses play no role. Aft/fore added
mass shares `(x_f-L)/(x_f-x_a)` and `(L-x_a)/(x_f-x_a)` conserve the **linear
geometry estimate** of LCG, with each share split equally between its mirror
partners. Report the exact resulting loading LCG because changing tank
centroids can make that approximation imperfect. If needed, a cheap native
loading-only inner root may adjust the aft/fore ratio to conserve the actual
departure LCG; failed bracketing rejects this ray, never the stow.

The ray's capacity endpoint follows the smallest tank's remaining capacity
divided by its positive kg weight. Determine its draft endpoint using the
ordinary departure `loading_condition` and `floating_equilibrium`, checking
both forward and aft drafts against
`min(hull.max_draught_m, port.max_draught_m or hull.max_draught_m) - .0005`.
Use a bounded bracket/scan followed by a native scalar root for the first
draft limit reached; do not assume an approximate LP draft is exact or
silently accept an unbracketed endpoint. A previously infeasible baseline
does not supply positive fill headroom. Capacity and draft merely bound this
search ray; all other loading/physics rules still need their exact checks.

Propose the midpoint and a near-cap point of this **current draft-derived
interval**, not a fixed tank fraction or mass. These can enter substantial
slack fills in one jump rather than relying on at most twelve small SLP
steps. Balance each proposal's exact arrival raw TCG with the occupied-pair
fallback, and reroot/reject if that transfer changes the limiting native
draft. Then evaluate ordinary all-three-state area and securing. Persist
the selected geometric pairs, kg weights, capacity/draft endpoint, direct
equilibrium call count, exact draft/LCG/TCG residuals, seed fractions and
all native outcomes. The seed portfolio remains at most nine unique
vectors; prioritize these two fill rays ahead of the poor second-wide-pair
near-full descendants. This retains a cold geometry-derived family and
does not read the C3 witness or hardcode its WING-A fraction.

Regressions: verify nonnegative aft/fore kg weights conserve linear mass
and moment, unchanged empty/pressed pair status, capacity-limited and
draft-limited endpoints, both forward and aft limiting cases, no-bracket
rejection, exact arrival balancing followed by a draft recheck, and the
nine-seed/deduplication limits. A short admitted real-map loading-only
diagnostic should save the finite fill interval and exact endpoint drafts
before a fresh full cold run; it is not a full-checker pass.

### 3. Permit primary fill before requiring a drawdown control

In `_compensated_trial`, derive and add the geometry-selected primary pair
first, balance exact arrival raw TCG, and compute its exact departure forward
and aft drafts. If both are under the reserved limit and capacities hold,
return that uncompensated search point immediately. Only require an occupied
forward control pair when native draft actually needs relieving. Retain the
existing actual-removable-moment ranking for drawdown, root using both draft
limits, rebalance and recheck. No available control is a reason to reject a
needed drawdown, not a reason to discard a pure primary increase that still
has freeboard. Native full physics always decides acceptance.

Regression: empty all forward control pairs, retain ample native draft
headroom, and verify a legal primary increase is returned. A counterpart
with no freeboard and no usable control must be rejected. Existing stocked
control ranking and exact-vector/cargo association tests must continue to
pass. All changes await explicit source unfreeze and numerical admission.

## Proposed cargo neighborhood after the frozen runs

This is source inspection and a raw-JSON geometry census, **not an implemented
or numerically tested improvement**. No production Python file was changed for
this plan. The separate original-C1 cold helper is a different experiment.

There is no empty low hold target in either probe-06 `cold-003` or the fresh
`first-cold-02` `cold-003`: all **60 hold cells** are occupied. Every deck
physical stack also has a box, and every deck base is the same **10.4 m**.
Consequently, relocating a whole pattern to an already empty lower page is
not an available escape on these concrete stows. This census follows their
current-port load destinations and the input vessel's physical cell/stack
geometry; it does not claim any proposed relocation is legal. Other maps can
still have such targets.

`page_tools.descend` examines only pairs of already selected columns, with
the same page kind. It tries a whole-column swap, a single-box swap and a
single-box transfer. It cannot create an unused page, empty a one-box donor,
or consider a hold-to-deck transfer. Every accepted move must immediately
improve vertical moment and satisfy the global LCG/heel guards. This is a
greedy local stop, not a proof of minimum cargo KG. Two changes whose combined
moment is favorable can be missed when either first change violates a guard
or raises the objective temporarily. A protected box also prevents its
column's whole-pattern swap, although ordinary boxes elsewhere in that
column might still move legally.

The first extension should be a bounded **atomic two-column repacking**:

1. Select high-moment deck columns and nearby compatible hold/deck columns,
   prioritizing actual securing witnesses and complementary fore/aft moments.
   Freeze all boxes outside those columns. Keep protected special-box slots
   fixed, and conservatively freeze OOG-support geometry initially.
2. Enumerate two-column tail cuts and repartitions of their concrete union,
   allowing both final tier counts to change. For a small union, at most
   10 ordinary boxes, enumerate subset assignments and a few explicitly
   defined E1/E2-compatible orders rather than an unbounded permutation
   search. Such order restrictions are search choices, never certificates.
   Evaluate final columns together: no intermediate arrangement needs to
   satisfy a transient LCG or objective guard.
3. Run original static, mass, height, E1/E2, special and reference Fast gates
   on both full final columns. Preserve concrete-box coverage and physical
   stack disjointness. Rank the *combined* native ordinary-height vertical
   moment, longitudinal and transverse changes. Keep the current expanded
   OOG-envelope visibility proxy separately: `_column_geometry` is not the
   native stability cargo-height convention.
4. If two columns are insufficient, retain a bounded list of individually
   column-legal proposals with signed `(vertical, longitudinal, transverse)`
   moment changes. Pair proposals on four disjoint columns with opposing
   longitudinal changes, then check the combined global guards. Reject
   overlapping box/stack support and dominated proposals before expensive
   Fast evaluations. This permits a jointly balanced descent that the
   current one-improving-move-at-a-time search cannot take.

For a map with genuinely free stacks, add whole-pattern relocation as a
separate cheap move. A destination page is available only when its **physical
stack support** is free after withdrawing the changed columns. An unselected
20-foot page may overlap a selected long page, so absence from the chosen
page list alone is insufficient. Admit empty donor columns explicitly and
retain exact complete coverage. This generic extension should not consume
search time on the present zero-free-stack census.

A larger escape changes the **page footprint or height family**, which is
outside the fixed-geometry sorting bound. For example, atomically free a pair
of hold stacks and a compatible deck pair, then regenerate alternatives
using pure long, paired pure 20-foot, and native-legal mixed deck pages.
Both directions of the length-mode change should be scored; none is assumed
to improve KG. The hold remains pure, and the union must contain exactly the
same concrete boxes. A three-level 20-foot deck base could be relevant when
exchanging an entire three-tier hold pair, but existing mixed generation
supports only bases one and two: supporting base three would need explicit
geometry, equal-height support and securing validation, not a silent new
pattern assumption. Standard/high-cube exchanges can also change the heights
of boxes above them and therefore leave the current equal-height/ISO exchange
family even when slot addresses are retained.

These operations seek better mass distribution within a full hold; they
cannot manufacture additional hold volume. The 9.695271 m sorting relaxation
does not bound these broader footprint/height changes. Conversely, 9.5 m is
a search motivation from earlier development, not a universal physical
requirement. Actual containership area, trim, draught and securing decide
whether the new geometry helps.

After rebuilding an assignment, run the actual Sequencer and replay, attach
ballast only through the ordinary resolver, and recompute all search guards
after any balance/OOG repair. Cargo and tank masses must remain one atomic
candidate. A new exact physics/ballast stage is necessary: reference Fast and
moment deltas do not establish a pass. Only the ordinary full checker can
accept the result, including the actual projected later-port lifts.

After unfreezing, meaningful first fixtures are: two individually rejected
LCG moves whose joint result is legal and improves KG; an allegedly empty
page that overlaps a retained long footprint; a full-hold census that skips
empty-target search; a height-class exchange whose final native moment differs
from the fixed-position approximation; and a footprint conversion preserving
every box while rejecting illegal mixed hold support. A first measured search
could cap at 20 column pairs, 10 ordinary boxes per union, 16 retained moment
proposals and 10 seconds, followed by separately budgeted replay/native checks.
No speed, feasibility or lift-saving result is claimed for this proposal.

## Original-C1 cold reference probe and compact continuation

`legacy_cold.py` isolates the original C1 page Model and `one_stow` through
unique source imports. It installs the authorized E9 fix before constructing
a new Problem, pins its private LP calls to one HiGHS thread, and restores
ordinary import names after loading. It does not import C2's modified Model
or use stored plans, numerical LCG targets, tank recipes or C3 witnesses.
Its first stow is original C1 plain construction. A second C1 stow uses
that same run's native-tank securing reference, caps `(4,5,5)`, securing
search cap 1.0, soft LCG target `fresh_plain_LCG - 1.5 m`, and a generic
seed offset of five.

`results/legacy-cold-probe-01/` measures this on the first development
`medium12` map, seed 0, with a 235 s allowance. It returned in **70.41 s**:

| Candidate | Complete boxes | Lifts | Cargo KG, m | Cargo LCG, m | Securing rod lower bound |
|---|---:|---:|---:|---:|---:|
| Original-C1 plain | 121/121 | 114 | 10.130340 | 58.552404 | 1.146177 |
| Fresh-reference C1 physics | 121/121 | 110 | 9.540211 | 55.133147 | 0.770226 |

The derived target was **57.052404 m**, not a copied historical target.
The physics rung used 19,870 columns, LP shortfall .333, two retreats and
48.8 s of `one_stow`. Its 110 replay lifts comprise 24 relocations, 12
current hatch lifts, 22 projected later hatch lifts and 52 projected later
restow lifts. The first stow is proved impossible for any ballast by the
securing screen; the second is merely **not ruled out**. Counts are **two
complete stows; no full-checker pass measured** in this probe. This is a new
cold plan, not claimed identity with the earlier 98-lift development stow.
Three targeted helper tests passed before the numerical run. PID 72276,
session 68599, exited normally; the benchmark marker was absent, and only
one numerical process/thread was used by this probe.

The production adapter now tries this legacy sequence first only on empty
arrival maps with at most 150 load-list boxes and at least 240 s construction
budget. It measures actual remaining construction time rather than charging
the full legacy allowance. Larger maps retain the faster C2-first path.
This is portfolio selection by instance size, not a stored-map lookup.

`cold_compact_after_legacy=True` explicitly permits one additional context
field, `cold_live_scored`, supplied internally from that invocation's legacy
stage. It validates instance identity, ordinary replay and complete concrete
coverage before reusing the live plan. General stored/warm context fields
remain ignored. The compact C2 continuation has a **90 s cap**, with bounded
mixed/per-stack-sight, unused-cover layout and soft-pool stages. Destination
page gates revalidate live patterns; unchanged legacy assignments are not
re-emitted. New `cold-*` and original `legacy-*` records retain distinct
identities, source/input hashes, live provenance and separate replay versus
full-checker status. Sixteen targeted cold/helper tests passed after wiring.

The next fresh measurement is `first-cold-03`, configured for 300 s
construction and 840 s finishing, within the adapter's global map deadline.
All Python sources were frozen before that run; this section records only
the completed development probe and intended integration. No production
pass or paired improvement is inferred from the constructor result.

## Measured cold attempt and separate warm improvement

`results/first-cold-02` is a fresh shared-scorecard run: 240 s construction,
900 s finishing allowance, 1,105.554 s actual pipeline wall time, four distinct
complete replay-legal 121-box stows, **zero full-checker passes**. Both finished
candidates failed securing, forward draught and arrival second-check area;
the independent runner retained those same three rule IDs. All 23 native
report parts ran without errors. Plan/instance bindings, source snapshots and
before/after dependency hashes were audited in `independent-json-audit.json`.
The first attempt's 57 violations were 54 securing, two draught and one area;
the second's 51 were 48 securing, two draught and one area. These are unknown
map outcomes, not whole-map impossibility proofs. Load sampled during this
run was 13.67/13.42/12.68. Requested `nice -n 5` was denied by the host.

`results/warm-restows-01` deliberately loads the archived C3 witness and is
**warm, excluded from cold counts**. A fixed-topology exchange reduced lifts
114→100: 24 relocations + 12 current hatch + 44 projected later restow + 20
projected later hatch lifts. Thus the original 58 later restow lifts fall to
44, a verified 14-lift reduction. All 121 requested boxes are loaded. The
run took 114.896 s; its unused-cover restricted pools gave no further gain.
A separate fresh native checker process passed the resulting plan in
25.936 s with zero violations and all 23 parts run; canonical input/plan
bindings and all 319 native Python source files matched before/after.
The independently rechecked arrival area spare is 0.000210482 m·rad and the
smallest securing margin 0.001379234, so this remains a narrow witness.
Independent artifacts are in `results/warm-restows-01-independent/`.
This is projected fixed-stow later-port work under D15, with no later loads;
no global lift-optimality or 0.9.0 comparison is claimed.

The benchmark marker was observed absent at 15:58:36 UTC. Only then was a
second numerical job admitted; warm optimization and independent verification
each occupied that second slot serially while the cold run occupied the first.
All numerical threads remained one. Subsequent admissions recheck the marker.

## Read-only larger-map first-stow budget review

While `first-cold-04` and its baseline were running with Python sources
frozen, the approved C2 `medium12` records were inspected without numerical
experiments. The **12/12 complete replay-legal** C2 RMP result belongs to the
paired constructor benchmark: each map allowed 60 s for the root and a
shared 60 s for phase 3 plus diving. It is not a 12/12 result for the later
60 s shared-scorecard entry. That entry completed **11/12**, with **zero
full-checker passes**. Its missed map was the 229-box
`random.medium.185100100.0.2`: the finite root pool was integer-infeasible,
then the dive used 22.166 s and stopped with 160 boxes left. This is a
search-budget miss, not a proof that the map is infeasible. The separate
120 s base score entry completed that map, and the paired RMP benchmark
completed it after further pricing/diving.

The larger allowance mainly protects the 229-box dive path. All four
428-box RMP score stows already completed, in 13.143, 1.760, 12.103 and
1.784 s respectively, excluding model initialization and the subsequent
checker/ladder. Several used retained first-fit incumbents, so a larger
deadline need not consume more time. The unequal base120/rmp60 score
budgets do not establish a causal speed improvement.

Recommended bounded change after unfreeze: **only for load lists over 150
boxes**, raise the first plain stage from its current 50 s maximum to a
120 s maximum at a 300 s constructor budget. Use a total stage stop of
`min(global_deadline, constructor_started + min(120, max(50, .4*budget)))`
so setup also counts toward that share; the native stow should return
immediately when complete. This leaves approximately 180 s of a 300 s
constructor for stronger patterns and references, before their own
bounded overhead. Preserve the <=150 legacy-first path, compact live
continuation and their deadlines exactly. Do not adopt C2's 85% first-stow
share for 428 boxes: it would leave only 45 s at budget 300. Opaque native
LP, balancing and replay work can still overrun a cooperative deadline;
record actual stage wall time and configured stop separately.

Evidence: `c2/c2-cgscale/results/benchmark-rmp-timing.md`,
`benchmark-rmp-summary.json`, `score-timing.md`, and
`score-medium12-rmp/summary.json`; source deadline behavior was checked in
`c2/c2-cgscale/method.py`, `rmp_method.py`, and current `cold.py`. No Python
files changed and no tests or numerical jobs were run for this review.

## Planned cold legacy securing-reserve rung (source still frozen)

Read-only source review supports adding a third original-C1 rung after
the present plain and `lash_limit=1.0` constructions. The proposed rung
keeps caps `(4,5,5)`, derives its soft target from the same invocation's
plain cargo LCG minus 1.5 m, reuses that plain stow's native-tank Fast
reference, and changes only the securing search cap to **0.97** and seed
offset to **six**. This is a generic small-load portfolio profile, without
map IDs, historical assignments or tank recipes. The cap provides search
reserve at the chosen reference motions; it is not a claim of a 3% native
full-voyage securing margin after ballast changes.

Keep every complete replay-legal 1.0 candidate even if the 0.97 construction
fails. Both strengthened records should retain kind
`legacy_native_physics`, with separate settings and construction seeds,
so the existing adapter recognizes the last successful strengthened stow
as a live compact-continuation hint. Reserve time by stopping the 1.0
rung at `min(legacy_deadline, legacy_started + .70*legacy_budget)` and
give the 0.97 rung only the actual remainder of the same deadline. At the
production 210 s legacy allowance this reserves up to 63 s; an early
1.0 success leaves more. Runtime setup and native cooperative overruns
remain charged to actual constructor wall time.

A private warm-pool helper can map source patterns to destination pages
by kind and complete physical stack support, then require destination
tier capacity, original `legal`, and original `lash_ok` before `add`.
Cap it at 4,000 source patterns and 2 s within the remaining legacy
deadline. Treat old patterns as suggestions only: never copy the previous
integer seed or its acceptance status into the tighter model. All output
still passes the existing real replay and full concrete coverage gate.
The helper should keep C1 unique imports; importing C2 solely for warming
would risk unnecessary module coupling.

Targeted tests after unfreeze should verify the fresh target/reference
and seed offsets; exact destination warming rejection for illegal or
over-cap patterns; bounded deadlines; and retention of the 1.0 record
when the 0.97 rung returns no stow. Existing ranking uses strengthened
status, cargo KG and lifts. An adapter-owned extension can prefer a lower
construction securing cap after KG, while actual full-checker passes
remain the acceptance criterion. No Python edits or numerical jobs were
performed for this planning note.

## Final constructor profile implementation and measured tighter-cap attempt

After `first-cold-04` exited, the planned three-rung legacy profile and
larger-load deadline guard were implemented. Twenty targeted tests passed
(`test_legacy_cold.py` and `test_cold.py`). The tests check fresh target and
reference derivation, all three seeds, retention of the 1.0 candidate when
0.97 fails, bounded warming through destination gates, and unchanged small
load deadlines versus the 120 s total-stage cap for larger loads.

`results/legacy-cold-probe-02/` ran once on the first approved `medium12`
map with a 235 s constructor allowance. PID 77115/session 51785 exited
normally after **124.065 s**, including cheap securing-bound evaluation.
The benchmark marker was checked absent before admission; this job used
the second authorized CPU slot alongside the baseline, one numerical
thread, and explicit HiGHS `threads=1`.

| Rung | Actual stow time, s | Complete | Lifts | Cargo KG, m | Securing rod lower bound |
|---|---:|---|---:|---:|---:|
| Original C1 plain, seed 0 | 7.854 | yes, 121/121 | 114 | 10.130340 | 1.146177 |
| Native reference, cap 1.0, seed 5 | 48.902 | yes, 121/121 | 110 | 9.540211 | 0.770226 |
| Same reference, cap 0.97, seed 6 | 46.809 | no, nine boxes left | unmeasured | unmeasured | unmeasured |

The tighter rung examined 4,000 current-run patterns and accepted 3,928
through its own capacity, original legality and securing gates. Its LP
shortfall was 0.167; diving stopped at the tail after eight retreats.
Neither that shortfall nor the failed tail proves physical infeasibility.
The original 1.0 candidate was retained with its fresh-derived target
57.052404 m. Thus this probe measured **two complete replay-legal stows,
zero tighter-cap complete stows, and no full-checker pass measurement**.
The plain stow has a fixed-stow securing impossibility certificate; the
1.0 stow is merely not ruled out by that bound. There is no measured
securing-reserve versus KG tradeoff to use for ranking on this map.
Per-stage rows and plans were saved before completion, followed by final
candidate metadata and bound diagnostics. The numerical slot was released
immediately after exit. Production Python sources are now frozen for the
parent's final suite and cold cohort; no further first-map tuning is
proposed from this negative reserve-rung result.

The parent subsequently authorized one final generic bracket: if cap 0.97
is incomplete and at least 20 s remain, try **one** cap-0.985 rung with
seed offset seven, the same fresh reference/target and gated warming from
the latest current-run pool. Keep every earlier complete candidate.
No repeated bracket or map-specific target is used. Twenty-two targeted
cold/legacy tests passed, including midpoint success/fallback retention,
latest-pool reuse and insufficient-time rejection.

The final `results/legacy-cold-probe-03/` measurement used the same first
development map, seed 0 and 235 s allowance. PID 77562/session 1922 exited
normally in **176.825 s**, releasing its admitted second CPU slot. The
plain and cap-1.0 rungs again returned 114 and 110 lifts respectively,
with unchanged cargo KG and securing lower bounds. Cap 0.97 stopped
after 44.100 s with nine boxes left, LP shortfall 0.167 and eight retreats.
The single cap-0.985 retry stopped after 52.589 s with nine boxes left,
LP shortfall 0.704 and eight retreats. Both are unknown construction
outcomes, not infeasibility proofs. Final counts remain **two complete
replay-legal stows, zero complete reserve-cap stows, and no full-checker
pass measured**. The 110-lift cap-1.0 candidate remains available. Python
sources were frozen at launch and remain frozen; the final cohort should
proceed without further constructor-profile additions.

## Frozen-cohort read-only reproducibility diagnosis

While the final two-worker cohort remained frozen, source and retained
development evidence identified two concrete search differences from the
C3 warm pass. No Python files changed and no native numerical evaluations
were run for this diagnosis. Arithmetic below uses only standard-library
JSON reads and weighted sums from the first fresh plain plan.

**Legacy longitudinal coordinates are inconsistent.** Original C1
`method.py` derives its reference cargo LCG from the first support cell of
each placement, and `pages.Model.lcg_moment` uses that same first-cell
coordinate. The cold wrapper instead derives its target from the physical
average of both support cells for long boxes. On the current-run plain
121-box stow, cargo mass is 1,906,191.2922867078 kg, physical average LCG
58.55240421769252 m, and C1-master first-cell LCG 60.40941850350786 m:
an offset of **1.85701428581534 m**. Subtracting 1.5 m from the physical
average therefore feeds a 57.052404 m target into a master expressed in
a different coordinate. A coordinate-consistent minus-1.5 policy would
instead yield 58.909419 m.

There is a separate portfolio-policy omission: the original C1 adaptive
ladder relaxed its target back to its own first-cell reference baseline
before the successful caps-`(4,5,5)`, seed-5 rung. The historical 60.409419 m
target equals the first-cell baseline freshly computed above; it was not
evidence of a different plain stow. Thus a generic baseline fallback can
reach that candidate family without reading a stored number. After the
frozen cohort is archived, the authorized next profile will derive a
separate `c1_master_cargo_lcg_m`, retain physical-average `cargo_lcg_m` for
reporting, and use **zero offset from the fresh C1-master baseline** for
strengthened rungs. This combines a coordinate bug fix with an explicitly
different baseline-relaxation policy; it is not a result of the frozen
cohort. A long-slot regression should deliberately make average and first
cell coordinates differ and verify the fresh target and both metrics.

**The cargo-exchange CP can exclude its own descended incumbent.** The
first final cold map already descended to cargo KG **9.5044306203 m**,
below the C3 passing cargo's **9.5112125711 m**. Therefore missing low-KG
geometry alone is not supported as the cause of this near miss. Its CP
had 3,797 variables/15 groups and correct rank/heavy/category relaxations,
but returned UNKNOWN after 31.55 s. The descended cargo's transverse
moment was -3.806664 t·m; its hard target under the old ballast was
+11.219173 t·m with a +/-2 t·m band. The 15.025837 t·m mismatch excludes
the full current-assignment hint. By comparison, the C3 successful CP
target and baseline both equalled approximately -0.124215 t·m, preserving
the identity hint. This isolates a substantive search restriction, though
it does not prove that restriction is the sole reason for UNKNOWN.

The minimal next change is confined to `repair.py`: pass
`balance_reference_tcg=False` to the cargo-exchange CP, retaining its
ordinary heel/LCG/static/E1/E2/stack/Fast gates. `ballast.py` already
rebalances each returned new-cargo plan with the native current-run
`_balance` before its first exact physics evaluation, so a hard zero-TCG
row under the old tanks is unnecessary. This preserves a useful search
incumbent; it changes no acceptance rule. An integration regression should
simulate a descent-induced transverse change, confirm that the CP is not
forced to offset it under stale ballast, and retain caller rebalancing
and exact native reevaluation. Both fixes await the parent's post-cohort
source snapshot and explicit unfreeze. Any corrected-profile measurement
must be reported separately from the frozen twelve-map cohort.

## Final cohort first map: checked failure and search-family limitation

`results/cohort-first-05/` is the first map of the frozen final cold cohort,
not another tuning opportunity. On `random.medium.185100000.0.2`, seed 0,
the method produced **two complete stows and zero full-checker passes** in
1061.298 s (252.064 s construction, 809.105 s adapter finishing). Its saved
native finisher used 142 evaluations and all 16 allowed SLP iterations;
the declared caps remain 160 evaluations, six FD directions and nine seeds.
Sixteen protected local LP solves completed without an elastic fallback;
twelve proposed steps were rejected by exact native protection. The retained
incumbent satisfies every native securing and loading margin. Its unchanged
full checker still rejects one containership second-check criterion: arrival
area is **0.2853143784 m·rad versus 0.2922221349 m·rad**, a deficit of
**0.0069077565 m·rad**. This is a checked search failure, not a ballast or
map-infeasibility certificate. The remaining eleven maps continue under the
same frozen Python; no search changes were made during the cohort.

The finisher does not search all eighteen tank masses independently. A local
Jacobian has at most five common mirror-pair increment directions plus one
P/S split, so its linear span has rank at most six. The chosen common pairs
in this vessel are aft narrow, forward peak, large low double-bottom,
high-leverage midship, and forward moment-capacity deep tanks; geometry,
not their identifiers, selects them. Many tanks have no independent local
direction. Common pair increments also preserve their starting mass/fill
differences when capacities match. The fresh affine LP can supply an extreme
asymmetric start: in this run the retained FPT fill difference remains
approximately 0.2437273, and its final fills are approximately
`(0.3290773, 0.0853500)`. The retained HEEL difference likewise remains
approximately 0.0375615 after its prior balancing step. These observations
describe the local search family, not physical restrictions on the vessel.

Seed generation, exact arrival-TCG balancing, special compensated lines and
cargo repair can change that family between stages; the entire algorithm is
therefore not literally one fixed six-dimensional affine space. Nevertheless,
those operations do not provide a complete independent exploration of all
eighteen masses or every tank regime. Exact TCG balancing normally changes
one currently bracketed pair, while a later local stage again uses common
pair increments and one selected split. The known warm full-checker witness
therefore proves that a feasible physical solution exists for its own cargo
and tanks, but does not imply that this cold restricted local optimizer must
find that solution from a newly constructed cargo arrangement.

A **next task**, after the cohort and outside this implementation, could use
all eighteen bounded tank masses with an initial native Jacobian followed
by Broyden/low-rank updates from exact evaluated steps. That would allow
independent P/S adjustment and mass redistribution outside the current
pair span while avoiding eighteen fresh full-oracle finite differences on
every iteration.
The current C4 `run_stage` recomputes its finite-difference Jacobian on each
iteration; **no Broyden or secant update is implemented or measured here**.
The proposed future method must retain native securing/loading as constraints,
the best exact protected incumbent, bounded trust regions, explicit work
and wall-clock caps, and the independent final full-checker reserve within
the 20-minute map allowance. Rebuild or invalidate affected derivatives at
pressed/full boundaries, small-tank correction-membership changes and other
native regime changes; a low-rank update across those discontinuities is
not a certificate. Record actual costs before claiming that a full Jacobian
plus updates fits that allowance. This is a proposed broader search family,
not a promised rescue or an optimality proof. No implementation, tuning run
or additional numerical process was started for this diagnosis.

## Post-cohort candidate fix: transverse-CG cusp, not a measured frontier

A source/JSON-only review found a concrete local-model defect beyond the
restricted direction span. The native curve subtracts
`abs(condition.tcg_m) * geometry._axes(...)[2][1]`
(`src/stowage/checker/stability_curve.py`, line 55). Its area is therefore
nonsmooth at upright TCG zero. In first-cohort-05, raw arrival TCG is about
-0.0000213 m. The fixed P/S difference stencil of +0.0005 tank fraction
changes TCG by roughly +0.00039 m, crossing that cusp. Its area secant can
have the opposite sign from the true derivative on the current side.

The local TCG row currently compounds this: for nonzero `r = raw TCG`,
`s = sign(r)` and predicted change `q = R delta`, it enforces only
`-s*q <= abs(r)`. That prevents crossing zero in one direction but permits
arbitrarily moving farther from zero in the other. The spurious P/S area
gradient can therefore send the LP away from transverse balance. Final
stage step 7 changes the WING-A fill difference from about -0.01994 to
-0.04510: the approximate added lateral moment is -84,600 kg·m, or about
-0.0098 m TCG at this displacement. Its area deficit worsens to 0.013785
m·rad while securing and loading still pass. These exact trial failures are
consistent with the defect; they do not establish the physical optimum of
the six-direction family. The added moment is a geometry estimate, not a
new native evaluation of that trial's raw TCG.

The proposed **post-cohort**, generic repair is:

1. Before spending a GZ evaluation on a finite difference, probe its raw
   arrival TCG cheaply with the ordinary loading helper. Prefer a feasible
   stencil endpoint on the same TCG sign branch. Near zero, reversing the
   P/S stencil to move away from zero often retains its ordinary step size
   and avoids cancellation. All capacity, pressed/full regime and native
   correction-membership checks still apply.
2. If only a toward-zero stencil is feasible, find the native raw-TCG zero
   along that direction and cap the derivative step to at most one quarter
   of that root distance. If the step falls below a documented numerical
   finite-difference floor, or the center itself is within the centering
   tolerance, pin that direction rather than use a cross-cusp area secant.
   An alternative is to project the center, every FD point and every trial
   through the same exact arrival-TCG balancing root. Such a projected model
   must save the actual projected mass vector atomically with its native
   report; it cannot compute a projected derivative but accept unprojected
   proposals. Projection failures or branch changes invalidate the column.
3. On a nonzero sign branch impose **both** `-abs(r) <= s*q <= 0`, requiring
   predicted TCG to keep its sign and move no farther from zero. Within a
   centering band use `-eps <= r+q <= eps`; an undefined P/S objective
   derivative still needs pinning/projection, not an arbitrary selected
   slope. These are search restrictions and exact native acceptance remains
   mandatory.

Meaningful artificial regression: maximize the restoring surrogate
`f(u) = f0 - abs(-2e-5 + .8*u)`. The old +.0005 stencil has slope -.72,
although the correct derivative at zero is +.8. A -.0005 same-branch stencil
has slope +.8. The old one-sided row permits negative `u`, following the
false gradient away from the optimum. The two corrected rows permit only
`0 <= u <= 2.5e-5`; their optimum reaches TCG zero and improves the exact
surrogate. Add mirrored positive-TCG, exactly-zero, capacity-limited and
tiny-root cases. Verify that invalid/too-small stencils stay pinned and that
an all-zero center does not invent a preferred P/S direction.

Securing and loading derivatives are not automatically invalid merely
because an area secant crossed the TCG cusp. A normal-sized same-sign
stencil can retain their signal if its ordinary providers and free-surface
membership are unchanged. If an area-safe stencil becomes too small for
reliable finite differences, do not shrink all physical derivatives into
roundoff or reuse the invalid area column: either pin the common LP
variable, or use a consistently projected/split nonsmooth model with
separate validity evidence. The simplest proposed correction uses the
normal-sized away-side stencil and two-sided TCG rows first.

After the frozen twelve-map audit, a bounded next experiment could apply
this correction and the separately reviewed current-run cargo rebalance
correction, run their functional regressions, then make one fresh cold
first-map run under the same 20-minute limit with unchanged full acceptance.
Preserve the frozen cohort results and label the new profile separately.
No Python implementation or numerical experiment was performed during this
review, and no rescue or infeasibility claim follows in advance.


## Corrected cold profile, after the completed initial cohort

At 18:15 UTC the complete root-level C4 suite passed against the final corrected source bytes, and run-map.sh passed shell syntax checking. The first collection command omitted the experiment PYTHONPATH and failed collection; supplying its documented source path corrected the invocation. All 23 root Python files are snapshotted in results/corrected-profile-source-01.

The new profile fixes the C1 first-support-cell versus physical-mean LCG mismatch and explicitly uses its fresh plain baseline as the zero-offset relaxed search target. It disables the repair CP old-ballast zero-TCG band because the caller natively rebalances every repaired cargo before exact evaluation. It uses all-phase same-branch native TCG stencils, pins undefined/tiny derivatives, and adds both no-away/no-crossing local rows. These are search restrictions and coordinate corrections, not checker changes or physical certificates. No stored cargo assignment, tank vector, map-specific constant, or historical artifact enters this cold run.

The initial frozen cohort remains 12 complete-stow maps, 27 stows, 26 fixed-stow certificates and zero passes, mean wall412.222126s; it is not retrospectively relabelled as this new profile. A fresh corrected first-map run is next; no rescue is claimed before its ordinary full check.


## Consistent native-TCG projection follow-up

The first corrected cold run failed in1006.378162s: two completeconstructorstows, zero passes; final118lifts diagnostic, solearrivalarea deficit0.016167055782mrad, all23full-checkparts/noerrors, worstrod0.999528033. Its complete identity/source audit is results/corrected-first-01/independent-final-audit.json; sources remain in corrected-profile-source-01. Its CP now returns a secondrepair, but the cusp guard pins useful asymmetric common-pair fills, leaving onlyDB-M/FWD-DEEP finite-difference directions. ColdcargoKG9.5155668 differs only0.004354m from warm; ~214tless lowballast and more forwardtrim explain the remaininghydro gap. This is an artificial search-family restriction, not a physical impossibility result.

The next profile projects stage bases, cheap finite-difference probes, evaluated endpoints and LP trials through the same native arrival-TCG balance. Each Jacobian and LP capacity/delta column uses the actual projected displacement divided by the same step. Acceptance records actual projected vectors atomically with their cargo/results; null directions pin. Native all-phase branch and free-surface membership checks remain. It uses unchanged legacy/repair code and unchanged16SLP/160evaluation/9seed caps. All29targeted projection/ballast tests and the full root C4 suite passed; static review clear, public acceptance unchanged. Source bytes are frozen in results/projected-profile-source-01; another fresh map-file-to-checker run is bounded by the same20-minute cooperative allocation. No previous assignment/tank vector is loaded.
