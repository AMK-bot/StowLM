# Cycle 6: fewer lifts after a cold securing stow

All writes are confined to this directory. Read CONTRACT in full (including the cycle 5/6 update), rule tiers, lab README, and c5 securing NOTES/method/gap/stow/rows in the requested order. No sealed maps or c6-maps opened. No shared source edits or commit.

## Fixed design and assumptions

Legal first, all boxes aboard second, fewer lifts third. Only the unchanged full checker certifies a pass; D15 future work fixes the stow and has no later loads. A concrete passing incumbent remains available throughout polishing. No method reads an earlier plan, assignment, recipe, or ballast. Scorecard's stored comparison fields are ignored; comparison uses a fresh unchanged c5 arm.

Before any tuning, the primary 28 were selected using only `passed`, `input`, and `instance_id` from frozen-48. Sort by ID and take zero-based positions 0,5,10,15,20,25 for development. `SPLIT.json`, `dev6.txt`, `held22.txt`, `primary28.txt` record the split. The held 22 remain unused until source freeze. Whole all48 and quick tiny/small are final frozen checks.

Copied verbatim from c5: baseline.py (original method.py), gap.py, stow.py, rows.py, chainlab.py and their tests. method.py starts as the same copy; optional research lives in separate modules. Environment cache redirected here. Read/import existing c4 page methods rather than modifying them. Fresh baseline's code is byte-for-byte c5 method; output paths follow this directory through HERE.

Compute: at most two CPU-busy processes in the entire team (one if BENCH-RUNNING exists), one numerical thread each. Every solver command requests `nice -n 5`. This machine reports `nice: setpriority: Operation not permitted`; the command still executes, and no privilege escalation or process interference is attempted. Long frozen sweeps exceed 20 minutes because both complete 48-map arms and the prescribed quick sets are required; individual map budgets are 240 s proposal and 120 s finish, tries 2, seed 0, workers 2 when permitted. Load averages and stage diagnostics are saved per map.

## Parallel work

Independent agents develop a passing-stow polisher, optional fixed-charge assignment objective, and a certified lower bound. Agents do not run numerical work while the scorecard occupies both slots. Root integrates and measures.

Detailed mathematical explanations and primary-paper links: `POLISH.md`, `OBJECTIVE.md`, `BOUND.md` (incorporated below after implementation).

## Initial verification

Combined copied securing tests and new bound/objective/polisher controls: 23 passed. The new tests exercise nonadjacent blocking and hatch/stack deduplication, fixed cargo activation in the integer tail, transitive physical dependencies, incumbent retention on rejection/exception, and necessary securing root-capacity spreading. HiGHS emits only an options-forwarding warning for `threads=1` in three objective tests.

Fresh development baseline, workers 2: all 6 full passes, lifts 77, 74, 87, 147, 129, 149 in split order. Mean wall 85.563 s/map. Every baseline row has full checker evidence and method_metadata. Stored 0.9.0 fields in the scorecard summary are irrelevant to this comparison.

## Exploratory development (not frozen evidence)

The first narrow polisher sweep gave six passes and three wins: 77,74,81,139,125,149 against fresh c5 77,74,87,147,129,149. This initial sweep is exploratory only: a numerical precision correction in polish.py occurred at 14:45:46 while the sweep started at about 14:44:25. No held-back map had been opened. A final six-map development sweep follows after integration of an optional wider rank-safe neighborhood, before freezing and accessing held maps.

The narrower groups keep length, height, E1-heavy class and E2 rank invariant. Wider groups allow different E2 ranks but put explicit rank inequalities against every physical predecessor in the CP model, still keeping securing, capacity and side moments hard. All acceptance remains native replay followed by a complete unchanged checker pass. At most 90 seconds total polishing is allocated inside the original proposal deadline; stages are recorded separately.

New wider-neighborhood controls: 8 passed; total focused tests 31. Fresh dev bounds in split order: 65,52,59,89,113,115. These are lower bounds, not certified optimal plans. The CP budget is 15 seconds including model construction; preprocessing sometimes exceeds it (74 s on one dev map), so construction time is reported separately in wall_s. Source-pinned minimum-motion envelopes and conservative rounding are used; the lower bound always takes solver BestObjectiveBound rather than its feasible incumbent.

Final frozen pairing design: execute each arm through lab/score.py on the same two-map batch at workers 2 and identical 240/120 s budgets, tries 2, seed 0. Reverse arm order on alternating batches. Each map runs in a fresh worker process. Record external load averages per method row; absolute equal machine load cannot be guaranteed on this shared host. Run fresh primary28 certificates between paired batches, with no overlapping numerical jobs. All48, quick_tiny and quick_small use frozen runtime sources. No earlier plan is provided to any solver.
# Current-run lift polishing

`polish.py:polish` takes a stow that already passes the unchanged full checker.
It never opens an archived assignment, plan, recipe or ballast. If its search
fails or every candidate fails the checker, it returns the exact incoming
`Scored` object. A candidate carries the incoming plan's concrete ballast.

The graph has occupied positions as vertices, top-access dependencies as
arcs, and a second bipartite graph joining discharge ports to hatch covers.
Opening a port-cover edge pays the cover's fixed charge once, regardless of
how many boxes use it. For each position and port, the model charges a restow
when a later-discharge box occupies that position and either a predecessor
must discharge or its hatch must open. The union prevents double charging a
box blocked both by its stack and by its hatch. The graph is a search
objective: crane-path closure and the actual D15 projection are always
recomputed by native replay, and the ordinary full checker gates acceptance.

Each exact CP-SAT neighborhood permutes boxes within one common length,
height, E2-rank and E1-heavy class, changing at most 32 placements. OOG and DG
boxes stay fixed. Occupied slots and their height/rank categories stay fixed;
static eligibility, mass-share capacities, and concrete securing rows are
hard constraints. The securing guard is no worse than the passing incumbent
at the constructor's fixed reference motion (or 1 if below 1). Numerical
coefficients are rounded conservatively. Hard cargo-moment guards permit
0.10 m longitudinal and 0.02 m transverse movement and forbid an increase in
the constructor's vertical-moment proxy. Exact floating guards repeat these
rows. These reference rows do not certify native stability; the full checker
still decides. Eight groups at most are considered, each with at most eight
seconds of CP search, one worker, and a check reserve.

This is a restricted local-branching neighborhood inspired by Matteo
Fischetti and Andrea Lodi (2003), *Local branching*, Mathematical Programming
98, 23–47, [author publication list](https://www.dei.unipd.it/~fisch/) and
[DOI](https://doi.org/10.1007/s10107-003-0395-5). The Hamming-radius constraint
limits the number of concrete box-slot assignments that change; restricting
comparable classes makes support, heights and weight-order rules invariant.
It is not the paper's full branch-and-bound algorithm.

The stack-order motivation is Mordecai Avriel, Michal Penn and Naomi Shpirer
(2000), *Container ship stowage problem: Complexity and connection to the
coloring of circle graphs*, Discrete Applied Mathematics 103, 271–279,
[Technion publication record](https://cris.technion.ac.il/en/publications/container-ship-stowage-problem-complexity-and-connection-to-the-c/),
[DOI](https://doi.org/10.1016/S0166-218X(99)00245-0). We use its top-access
shift interpretation, not a claim that this constrained, multi-cover model
is solved by circle-graph colouring. No graph-based lower bound is claimed
by this polishing module.

The acceptance pattern and fixed-topology/native-replay idea were read in
`c4/c2-benders/repair.py`, `permutation.py`, and `warm_restows.py`; this module
is freshly implemented and reads no warm driver data. The concrete securing
coefficients come from the copied c5 `Assignment.stack_ratio` cache.
# Optional broader rank neighborhoods

`polish_wide.py` copies `polish.py` into a separate module, so it can be
integrated after an exploratory narrow run without mutating the running code.
It keeps the same current-run-only input, concrete ballast, hard securing,
capacity and moment rows, native replay, full-check acceptance and exact
incumbent retention. Its model and citations are in `POLISH.md`.

The original neighborhood preserved each position's E2 rank category. This
broader neighborhood lets rank categories exchange positions: only length,
height, heavy status and stack-closer status remain common to a group. Because
an upper heavy category can now move, the CP model directly requires each
position's assigned rank to be at most every physical predecessor's assigned
rank plus the fixed E2 tolerance. It includes transitive predecessors, rather
than checking adjacent positions alone: positive tolerance on every adjacency
could otherwise accumulate and violate E2's whole-stack minimum rule.

Each group has at most 64 boxes, interleaved by discharge port. Its Hamming
radius remains 32 changed box-slot assignments. The exact topology, height
categories and heavy statuses still stay fixed. A full check is still needed:
securing reference motions and cargo-moment proxies cannot certify native
stability or the final voyage conditions.

This is an optional wider local-branching family in the sense of Fischetti and
Lodi (2003), not a global optimality algorithm. It can be called after the
original `polish`, using that function's passing result as the incumbent.
Metadata must be saved separately by the caller if both are called; each
module writes its stage diagnostics to `metadata['polish']`.
# Optional fixed-charge assignment objective

`objective.py:Assignment` subclasses this folder's copied c5 `gap.Assignment`.
It does not replace `method.py`'s initial cold constructor. Changing an LP's
objective changes its chosen vertex, dive and eventual feasible stow; that can
lose a pass despite leaving the original rows in place. Keep the c5 incumbent
and independently check every alternative before accepting an improvement.

## The model in plain words

There are two small graphs on top of the existing box-to-slot assignment LP.
The first joins discharge ports to hatch covers. Using a hold cover incurs its
current opening and closing once; using that cover for cargo at a later port
incurs another opening and closing once. These are fixed charges, like opening
a warehouse before assigning any customer to it. The old objective charged
every box placed away from a guessed cover layout; the new objective chooses
which cover-port edges to open and charges each edge once.

For every box and cover, all candidate slots under that cover are grouped in
one link to the cover variable. This is stronger than linking each candidate
slot separately: one box split half-and-half between two tiers still activates
its cover fully. A capacity row also links assigned TEU to the physical number
of cells under the cover, the same capacity-cut idea used by c4/c2-layout.

The second graph joins a tier to each earlier discharge port that would require
lifting its box. A box is a blocker if it stays past that port and either a
lower box leaves there or its underlying hold cover opens there. One Boolean
variable charges that tier once per port even if both causes occur. Looking at
all lower tiers catches `[port 1, port 2, port 2, port 3]`: all three upper boxes
move at port 1 and the top box moves at port 2. An adjacent-transition penalty
misses some of this work. Only planned cargo and pure pages are represented;
background cargo, yard relocations and extra operational hatch closures remain
with replay and the full checker. This is a search objective, never a certified
map lower bound.

The existing vertical objective and moment rows are inherited. First-aboard
order is deliberately left to the existing cold `Sequencer` and actual replay:
a guessed fixed birth order can trade hatch gains for yard relocations, and a
free birth-order model needs the full current-operation precedence constraints
from c4/c2-hatch/bound_joint.py. No claim to optimize first-aboard order is made.

## LP and integer tail

The LP relaxes cover, edge and blocker variables into `[0, 1]`. The integer tail
keeps those variables Boolean and includes fixed-only activation rows. Reusing
c5's tail unchanged would be wrong here: that tail interprets every auxiliary
as independent soft slack and skips rows without free assignment variables.
It could forget that a previously fixed hold box opens a cover and underprice a
new deck blocker. A synthetic test covers that exact case.

The copied c5 securing and moment slacks remain soft by default for optional
cold construction. `hard_securing=True, hard_moments=True` sets their bounds to
zero in both LP and tail for an incumbent neighbourhood. Integer coefficients
round conservatively, and every returned integer-tail assignment is checked
against the original float rows. The full checker must still decide actual
securing at true heights and actual ballast; fixed-motion rows do not certify it.
No source file outside c6/c6-lifts was changed.

## Papers and relation to this implementation

- Cornuéjols, Nemhauser and Wolsey (1983), *The Uncapacitated Facility Location
  Problem*, Cornell technical report, later chapter in *Discrete Location Theory*
  (1990), pp. 119–171. [Cornell archive](https://ecommons.cornell.edu/entities/publication/0f45f770-313e-4ce4-87a7-102fc9e4b3f1).
  The opening-cost plus customer-assignment formulation motivates the
  disaggregated cover activation links. This implementation's TEU capacity
  cuts and cargo side rows are additional structure; no facility-location
  approximation theorem is claimed.
- Avriel, Penn and Shpirer (2000), *Container ship stowage problem: Complexity
  and connection to the coloring of circle graphs*, *Discrete Applied
  Mathematics* 103, 271–279, [DOI](https://doi.org/10.1016/S0166-218X(99)00245-0),
  [authors' university record](https://cris.technion.ac.il/en/publications/container-ship-stowage-problem-complexity-and-connection-to-the-c/).
  Their top-access conflict view motivates treating each earlier-port blocker
  as a graph conflict. This code uses explicit blocker witnesses rather than
  claiming to implement their circle-graph coloring algorithm.

## Verification and recommendation

Five synthetic tests in `test_objective.py` cover nonadjacent blockers,
cover/internal de-duplication, hold blockers, grouped fractional links and
activation of previously fixed cargo in the integer tail. These are meaningful
model checks, not a measured improvement on maps. The root executive owns CPU
slots and runs them; no numerical run was started by this subagent.

Recommendation: retain as an optional post-incumbent arm. Do not replace the
first cold c5 attempt without measured pass retention. It may provide useful
neighbourhood guidance, but the larger matrix and new vertex selection need
measurement on the six development maps before any frozen held-back run.

## Alternatives considered and initial frozen choice

The initial frozen choice is local branching among comparable boxes in
`polish.py`: keep the occupied positions, physical heights, heavy status and
weight ranks, then let at most 32 concrete boxes change position within one
class. This leaves support and height relationships intact, while hard securing,
stack-mass and moment guards limit physical changes. Every accepted move still
needs cheaper real replay and a fresh full-checker pass. The current-process
incumbent is retained on failure. This is an adaptation of Fischetti and Lodi
(2003), *Local branching*, not their entire branching strategy:
[primary publisher paper](https://doi.org/10.1007/s10107-003-0395-5).

The alternatives below were considered, rather than measured as independent
arms. Their selection rationale is an engineering assumption, not an experimental
ranking of the published methods.

- **Proximity search:** demand a lift improvement, then minimize the number of
  changes from the passing stow. This reverses local branching's emphasis:
  distance becomes the objective instead of a radius constraint. It could be a
  later arm, but a strict improvement cutoff is awkward when the compact graph
  objective omits some real replay work. Fischetti and Monaci (2014), *Proximity
  search for 0-1 mixed-integer convex programming*, *Journal of Heuristics* 20,
  709–731: [primary publisher paper](https://doi.org/10.1007/s10732-014-9266-x).
- **RINS:** solve the LP, fix assignment decisions on which the LP and incumbent
  agree, and optimize the remaining decisions. Agreement can identify a useful
  neighbourhood without choosing classes by hand. Here a new LP and uncertain
  agreement pattern add work; the smaller comparable-class model is the initial
  choice. RINS is not implemented in this frozen arm. Danna, Rothberg and Le Pape
  (2005), *Exploring relaxation induced neighborhoods to improve MIP solutions*,
  *Mathematical Programming* 102, 71–90:
  [primary publisher paper](https://doi.org/10.1007/s10107-004-0518-7).
- **Fixed-charge LNS:** remove several related cover/port groups, then rebuild
  them together so that an entire expensive graph edge can close. This is more
  powerful than exchanging ports among existing positions because it can change
  which covers are used now. Reconstructing stack heights also disturbs securing
  and side moments, so it needs a larger hard-row neighbourhood. The concrete
  precursor is `c4/c2-layout/lns.py`; Pisinger and Røpke (2010), *Large
  Neighborhood Search*, *Handbook of Metaheuristics*, pp. 399–420, supplies the
  general remove-and-repair framework, not a cargo fixed-charge theorem:
  [authors' DTU record and manuscript](https://orbit.dtu.dk/en/publications/large-neighborhood-search-2/).
- **Exact exchange from c4:** `c4/c2-benders/permutation.py` reassigns concrete
  boxes to existing positions with static and capacity guards; `warm_restows.py`
  also considers column moves. We reuse the fixed-position idea and add a port
  graph objective plus a local-branching radius. Exact refers to the discrete
  subproblem model; a time-limited solve does not certify the whole map optimal.

The optional objective in this document is consequently not the default: it
changes the initial LP vertex and rounding path, while the frozen choice starts
with a checked incumbent and admits only checked improvements. Root's combined
verification passed all 23 tests, including this module's five synthetic tests.
No numerical process was started for this documentation addition.
# Cold bound and graph model

`bound.py:lower_bound` reads only the supplied instance and source code. It
imports the unchanged C4 `c2-layout/bound_tiers.py` necessary physical-cell
relaxation, its audited outward physics envelopes, and C3
`c2-layout/bound_ports.py` Hall enumeration. No earlier plan, assignment,
ballast or bound-result file is read. All input lists use `catalog.load_set`.

The graph has discharge calls on one side and hatch covers on the other:
putting hold cargo under a cover selects an edge, pays a current opening and
closure for the used cover, and pays a projected opening and closure at each
incident discharge call. Deck cargo lasting beyond such a call forces a
restow, counted once even when its footprint spans covers. Physical cells and
their support predecessors form a second graph which couples this fixed-cost
covering problem to packing.

The reused C4 relaxation retains actual eligible physical-cell footprints,
cell capacity, no-gap support, stack mass/height capacity, hold length-class
separation, safe empty-bottom E6 witness implications, necessary displacement,
weather vertical moment and globally minimum-motion securing. It gives exact
ordinary minimum support-height floors, never nominal stack heights. It drops
E1/E2, DG/OOG pair interactions, exact securing, sight and finite-angle area,
and makes in-stack overstows free. The CP feasible incumbent is **not** a
certificate. Only the solver's lower bound, maximized with independently valid
analytic cover cuts and the Hall enumeration, enters the result.

## New securing spreading cut

At a deck root end frame the checker's transverse corner shear is
`(sum(transverse inertial force)+sum(wind force))/2`. Its combined two-axis
norm cannot be smaller than this transverse component, and its hard fitting
limit is `.85*210000 N`. Thus one root carries at most `357000 N`; one occupied
physical column has at most two roots and carries at most `714000 N` across
those roots. No-gap support makes all roots bottom roots. A long box may join
two columns, so arbitrary per-column attribution is unsafe. Instead, join all
columns linked by any eligible long-box footprint and sum over each connected
component. Every end-chain remains inside one component.

The new rows activate a physical column iff at least one allocation occupies
it. Sum downward-rounded minimum-motion force in each support component and
cap it by `ceil(714000*(1+1e-7))` times its active column count. Group
coefficients are minima over original members. Existing C4 force coefficients
already include an outward numerical guard and an optional minimum-end-share
factor at most one, making this total-force inequality weaker. Background
support is disjoint, guarded by the reused C4 routine. Omitting its forces
only relaxes the rows. These cuts can force deck cargo to spread; they do not
claim that a reference-GM securing polytope is globally necessary. In
particular, neither the constructor's `.95` margin nor its approximate
heights enter this certificate.

The original relaxation is restricted to empty closed arrival, all load-list
boxes aboard, no discharge now, forecasts or future loads, nonnegative
weights, and disjoint immutable background support. It rejects unsupported
cases. “Optimal” would mean fewest weighted lifts among complete plans passing
the unchanged full checker, counting later calls by D15's fixed-stow
projection without later loads. A relaxation optimum alone does not establish
an operational optimum; equality with a checked plan does.

## Circle graphs: explanation and limitation

Avriel, Penn and Shpirer connect multiport stack shifting to circle-graph
colouring. Each cargo interval has its loading and discharge events as chord
ends; crossing intervals cannot share a stack without shifting in the
classical model. Here all decision cargo is loaded at one call, no later
loads exist, and geometry, E1/E2, bookings and securing dominate which stacks
are usable. The unrestricted interval graph is therefore too weak to certify
the observed restows: sorting by discharge call alone ignores those hard
constraints. No circle-colouring bound is claimed. The cover bipartite graph
does retain a useful fixed-cost lower bound.

## Papers and proof provenance

- P. Hall (1935), *On Representatives of Subsets*, Journal of the London
  Mathematical Society 10, 26–30,
  https://doi.org/10.1112/jlms/s1-10.37.26. Port-subset cargo cannot exceed the
  neighbouring cover capacity; the reused two-port enumerator checks all
  three nonempty subsets and all cover masks.
- M. Avriel, M. Penn and N. Shpirer (2000), *Container ship stowage problem:
  Complexity and connection to the coloring of circle graphs*, Discrete
  Applied Mathematics 103, 271–279,
  https://doi.org/10.1016/S0166-218X(99)00245-0. Used for the graph explanation,
  with the limitation above; no approximation guarantee imported.
- C4 `c2-layout/physics_bound_notes.md` supplies the existing necessary-row
  proof and outward arithmetic. The active-root inequality above follows
  directly from unchanged `stowage.checker.lashing._chains` and the fitting
  shear term, rather than an external physical fit.

## Reproduction and validation

Use one numerical thread, the root-assigned CPU token, `nice -n 5`, and the
same scoped vessel cache as the experiment. Example (paths from repo root):

```
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab \
PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c6/c6-lifts/bound.py \
  --list experiments/mvp/frontier/lab/c6/c6-lifts/dev6.txt --budget-s 15 \
  --out experiments/mvp/frontier/lab/c6/c6-lifts/results/bounds-dev6.json
```

Tests cover support-graph components including shifted long boxes, force
capacity forcing occupation of a second component, long-box force counted
once, and the 37/14-cell Hall hand certificate. Parent will append measured
rows and checked-plan gaps to NOTES.md after frozen measurement. No claimed
improvement from these new rows precedes measurement.

## Freeze

Runtime frozen at 2026-11-01T19:55:33.975962+00:00 before accessing the held 22. All 31 focused tests passed again. Final development 6/6 full passes, 5 wins, 1 tie, 0 losses; mean lifts 110.5 -> 106.5 (3.62%); proposal+finish mean wall 85.56 -> 64.22 s, with different observed host load so no speedup claim. FROZEN.json records own and imported experiment source hashes. Further edits are reporting only.

## Frozen proof equality

Held-back `D192.medium-feeder_500-f60-high-s192200002`: fresh baseline 74 lifts, new checked plan 70. Parts: 46 forced relocations, 12 hatch now, 12 hatch later, 0 restows. Fresh certified bound = 70 (both physical relaxation and Hall wrapper). This proves fewest weighted lifts among complete plans passing the unchanged full checker on this map, counting later ports under the D15 fixed-stow/no-later-load projection. It does not prove any other map optimal. Evidence: results/candidate-all48/<id>.json and results/bounds-primary28/<id>.json.

The 70 equality has an analytic certificate independent of CP termination: 46 mandatory yard relocations plus `capacity_shipping_lower_bound=24`. The safe E6 witness disjunction forces all six common witness covers: putting all witness-family cargo in holds would require seven covers, while putting any such cargo on deck forces each of those six covers to have been used. Six covers cost 12 current and at least 12 later lifts. Thus 46+24=70, attained by the independently checked plan, even though the larger relaxation's CP status was `unknown`.

Budget interpretation: `--finalize-s 120` is the scorecard's per-candidate finish budget (README), with the same two tries used by c5's frozen arm. Both fresh arms use identical settings. Thus a failed map may spend more than 120 seconds in cumulative finishing across two candidates; actual propose_s/finish_s/wall_s are retained without truncation. Original c5 search also has bounded inner-call overhead, so CLI settings are not claimed to be hard process wall-time caps.

Concrete small graph: `random.small.40100011.0.8` (`experiments/mvp/corpus/val/small/feeder_800/random_yard/seed-040100011.json`) has 18 planned box vertices, 92 physical cell vertices in 20 stacks, and 2 cover vertices. Each discharge call is another vertex; a used port-cover edge pays its fixed hatch charge, while a later-leaving box above an earlier departure or an opening cover pays projected restow cost. Eligible box-slot edges are the static assignments retained by the page/assignment model, and a long-box edge consumes both physical cells. The polisher fixes occupied geometry, then changes labels on the positions; native replay supplies the actual lift cost. Objective data: `{"future_hatch_cover": 1.0, "future_restow": 1.0, "future_restow_lifts_per_box": 2, "hatch_cover": 1.0, "relocate": 1.0, "restow": 1.0}`. This example was read after source freeze and caused no tuning.

Concrete medium graph: `D192.medium-feeder_500-f60-high-s192200000` (`experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_500/f60-high/seed-192200000.json`) has 88 planned box vertices, 240 physical cell vertices in 64 stacks, and 6 cover vertices. Each discharge call is another vertex; a used port-cover edge pays its fixed hatch charge, while a later-leaving box above an earlier departure or an opening cover pays projected restow cost. Eligible box-slot edges are the static assignments retained by the page/assignment model, and a long-box edge consumes both physical cells. The polisher fixes occupied geometry, then changes labels on the positions; native replay supplies the actual lift cost. Objective data: `{"future_hatch_cover": 1.0, "future_restow": 1.0, "future_restow_lifts_per_box": 2, "hatch_cover": 1.0, "relocate": 1.0, "restow": 1.0}`. This example was read after source freeze and caused no tuning.

Proof provenance: the third TRAIN map's 70-lift certificate was already established in c3/c2-layout/bound_notes.md with a page plan. C6's result is that the new assignment-LP lane reaches that known bound cold; it is not a new first global proof. The current checker still rejects sailing with any open hatch (`stability.py`: “Open hatch invalidates the secured watertight buoyancy envelope”), supporting the current-cover closing charge.

Cold finisher audit: c4/c2-layout/ballast_opt.py:optimize constructs a new PhysicsEvaluator(instance, plan), reads its initial fractions from that current plan, initializes an empty trace, and only writes its out directory. The JSON plan read is in the standalone CLI, not optimize. Reusing the same diagnostic output path across paired arms therefore does not supply any earlier plan/ballast to either arm.

## Frozen pass-retention failure (do not promote whole method)

Held `D192.medium-feeder_800-f80-high-s192200100`: candidate found no full pass (237.43 s), fresh unchanged c5 passed at 193 lifts (275.24 s). Candidate never entered polishing. Its first GM-miss stow cost193 with ratio1.01133, followed by205/E9 and225/E6 failures; failure ranking returned the two geometry-broken candidates. Baseline finishing rescued a193-lift stow. Wall-clock-bounded tails/constructor candidate selection are not reproducible enough to promise paired pass retention. This is a measured pass lost, regardless of causal attribution. It fails the user's promotion gate. Do not tune the held map or rewrite frozen evidence. Complete all prescribed measurements; retain only the independently guarded post-pass component and certificates if their own evidence warrants it. The frozen method as a whole is not a pass-preserving replacement.

## Complete frozen primary28 result

Fresh unchanged c5:28/28 full passes; candidate:27/28. Development6:6/6 each; held22:22/22 baseline versus21/22 candidate. The one pre-polish pass loss above blocks promotion of the frozen whole method. No source or tuning changes follow this held-back result.

On27 mutual full passes:19wins,8ties,0lift losses; mean111.4815 ->107.7778 (-3.7037 lifts, -3.32%). All27 candidate construction costs equal their independent fresh baseline costs; within the same process polishing saves exactly100lifts with zero regressions. Mean primary attempted-map wall time41.96 ->64.36 seconds, so this is not a speedup. Narrow stage mean11.84s, wide12.42s;22 and11 accepted improvements respectively, with2 full-check rejects in each stage safely discarded.

Mean parts on27 mutual passes (baseline -> candidate): relocations72.148 ->72.148; hatch now14.074 ->14.074; hatch later15.333 ->15.407; restow now0 ->0; restow later9.926 ->6.148. Thus2 extra future hatch lifts buy102 fewer future restow lifts, net100 saved, under D15 fixed stow/no later loads.

All28 primary certificates were computed cold. Across27 new checked plans: mean bound83.037, upper107.778, gap24.741 lifts; mean per-map(upper-lower)/upper21.953%. One plan matches the known70 bound. The missed map has its certificate but no new checked upper bound. The method remains a negative result as a whole; conditional post-pass search and certificates are the parts worth keeping. Remaining12D185 maps and frozen quick_tiny/quick_small continue unchanged.


## Final completion and paired measurement

All132 scorecard rows (48+8+10 maps in each arm) and28 cold primary certificates are complete. The audit verifies input digests, complete assignments on every pass, source/config hashes, original c5 method bytes, and lower<=checked upper on every new passing primary plan. Own10 runtime sources,110 imported experiment sources, and the scorer/checker source digest are unchanged. All31 focused tests passed before freeze; no runtime change followed. The audit explicitly reports zero_pass_losses=false. No commit or shared-source edit was made.

| Set | Maps | T5 | T4 | T3 | T2 | T1 | T0 only | Not completed | Safe partial plans, mean % boxes aboard |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| all48 c5 | 48 | 28 | 0 | 0 | 0 | 0 | 0 | 20 | — |
| all48 c6 | 48 | 27 | 0 | 0 | 0 | 0 | 0 | 21 | — |
| primary28 c5 | 28 | 28 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| primary28 c6 | 28 | 27 | 0 | 0 | 0 | 0 | 0 | 1 | — |
| dev6 c5 | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| dev6 c6 | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| held22 c5 | 22 | 22 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| held22 c6 | 22 | 21 | 0 | 0 | 0 | 0 | 0 | 1 | — |
| quick_tiny c5 | 8 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| quick_tiny c6 | 8 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| quick_small c5 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 10 | — |
| quick_small c6 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 10 | — |

| Set | Passes c5 / c6 | Wins / ties / losses | Mean lifts c5 / c6 | Wall s c5 / c6 |
|---|---:|---:|---:|---:|
| all48/all | 28 / 27 | 19 / 8 / 0 | 111.48 / 107.78 | 143.80 / 161.07 |
| all48/dev6 | 6 / 6 | 5 / 1 / 0 | 110.50 / 106.50 | 34.48 / 62.56 |
| all48/held22 | 22 / 21 | 14 / 7 / 0 | 111.76 / 108.14 | 44.00 / 64.84 |
| all48/primary28 | 28 / 27 | 19 / 8 / 0 | 111.48 / 107.78 | 41.96 / 64.36 |
| all48/f60 | 11 / 11 | 7 / 4 / 0 | 97.18 / 94.45 | 34.00 / 50.87 |
| all48/f70 | 12 / 12 | 8 / 4 / 0 | 112.00 / 109.50 | 37.58 / 60.83 |
| all48/f80 | 5 / 4 | 4 / 0 / 0 | 149.25 / 139.25 | 214.35 / 238.21 |
| all48/D185 | 0 / 0 | 0 / 0 / 0 | — / — | 289.25 / 294.38 |
| quick_tiny/all | 8 / 8 | 0 / 8 / 0 | 7.38 / 7.38 | 16.45 / 20.82 |
| quick_small/all | 0 / 0 | 0 / 0 / 0 | — / — | 105.47 / 108.26 |

Certificate gaps: {"maps": 27, "at_bound": ["D192.medium-feeder_500-f60-high-s192200002"], "scope": "fewest weighted lifts among complete full-checker passing plans; D15 fixed stow, no later loads", "mean_lower_bound": 83.03703703703704, "mean_checked_lifts": 107.77777777777777, "mean_absolute_gap": 24.74074074074074, "mean_gap_over_upper": 0.21953050441255847, "mean_gap_over_lower": 0.2997872726686018, "mean_bound_wall_s": 21.97783422680934}

Complete replay and full-checker gates: {"all48": {"baseline": {"maps": 48, "complete_replay_legal": 47, "full_checker_passes": 28, "method_reports_replay_legal": 47}, "candidate": {"maps": 48, "complete_replay_legal": 47, "full_checker_passes": 27, "method_reports_replay_legal": 47}}, "quick_tiny": {"baseline": {"maps": 8, "complete_replay_legal": 8, "full_checker_passes": 8, "method_reports_replay_legal": 8}, "candidate": {"maps": 8, "complete_replay_legal": 8, "full_checker_passes": 8, "method_reports_replay_legal": 8}}, "quick_small": {"baseline": {"maps": 10, "complete_replay_legal": 10, "full_checker_passes": 0, "method_reports_replay_legal": 10}, "candidate": {"maps": 10, "complete_replay_legal": 10, "full_checker_passes": 0, "method_reports_replay_legal": 10}}}

Frozen source changes: []


## Completion table from the requested CLI

| Set | Maps | T5 | T4 | T3 | T2 | T1 | T0 only | Not completed | Safe partial plans, mean % boxes aboard |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| all48_c5 | 48 | 28 | 0 | 0 | 0 | 0 | 0 | 20 | — |
| all48_c6 | 48 | 27 | 0 | 0 | 0 | 0 | 0 | 21 | — |
| tiny_c5 | 8 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| tiny_c6 | 8 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| small_c5 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 10 | — |
| small_c6 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 10 | — |

Plain scorecard rows attest only T5 full passes; no peeling stage was run and no relaxed plan was retained. Consequently the other tier columns are zero and Not completed denotes absence of a retained completed plan at an attested level, not that every failed stow left cargo ashore. Both medium arms reached47/48 complete replay-legal stows; tiny8/8 and small10/10 each reached that gate. Only full passes count as passes.

## Verdict and next step

KEEP PART: the incumbent-preserving post-pass polisher and the certified bound. REJECT the frozen whole proposer as a c5 replacement: one held-back full pass was lost before polishing, despite all passing initial stows matching their fresh baseline and no accepted polishing regression. The optional objective.py arm is tested but unmeasured and remains off by default. The method is not a tiny/small replacement: tiny ties c5 at7.375 and small inherits0/10 full passes.

Next: preserve and finish all geometry-legal, ballast-repairable current-process candidates before discarding them; the GM-miss candidate must not be ranked below geometry-broken points merely by a securing-ratio heuristic. Use deterministic work limits for CP tails and a fresh independent evaluation after any fix. Then test cover-changing fixed-charge LNS, retaining a concrete full pass throughout. No post-freeze fix or held-set tuning was performed here.

Assumptions/limits: all later work is the D15 fixed-stow projection without later loads; bound scope is complete plans passing the unchanged full checker. Occupied topology remains fixed in the measured polishing stages. Wall-clock tails are host-load sensitive; alternating paired batches and saved load averages reduce but cannot eliminate that variability. nice -n5 was requested throughout but denied by the sandbox, as recorded earlier.

## Entry points and reproduction

Measured frozen entry: method.py:propose and method.py:finish. Fresh unchanged reference: baseline.py:propose and baseline.py:finish. Reusable guarded stage: polish.py:polish and polish_wide.py:polish. Cold certificate: bound.py:lower_bound / CLI. Paired maps/parts/gaps: RESULTS.json; compact tables: RESULTS.md and COMPLETION.md; audit: AUDIT.json; split/freeze: SPLIT.json and FROZEN.json. Each per-map scorecard row retains method_metadata separating construction, narrow polishing, wide polishing, and finishing.

sweep.py reproduces the alternating two-map schedule and resumes matching cached measurements. For a fresh repeat use new output directories with the scorecard, for example:

```bash
. experiments/mvp/frontier/lab/c6/c6-lifts/env.sh
C=experiments/mvp/frontier/lab/c6/c6-lifts
nice -n 5 $PY experiments/mvp/frontier/lab/score.py --set $C/all48.txt --method $C/method.py:propose --finish $C/method.py:finish --budget-s 240 --finalize-s 120 --tries 2 --workers 2 --seed 0 --out $C/results/repeat-candidate
# Repeat with baseline.py and a distinct fresh output directory; alternate paired batches for closest temporal load.
```
