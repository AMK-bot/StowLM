# Mixed pages on multi-discharge TRAIN rotations

Entry point: `graph_method.py:propose`. This is cold construction; it does not
consume stored plans or another method's assignment. Only the shared runner's
unchanged full checker determines passes. Root `NOTES.md` and result rows carry
the measured table; this note describes the method and its proof scope.

## What the engine really does

Discharge count alone does **not** make circle colouring nontrivial. With one
load call and freely reorderable cargo, sorting each unconstrained stack by
decreasing discharge call removes crossings at 3, 4, 5, or any number of ports.
Here the nontrivial choices come from finite mass/tier/equipment capacity,
heavier-below and securing rules, yard retrieval precedence, and especially
deck cargo obstructing a cover needed by hold cargo at several calls. These
TRAIN cases add discharge calls; they do not add future load calls or claim a
complete multi-load operational rotation.

The frozen engine's page master explicitly omits its deck-over-hold restow
constraint when `len(self.ports) != 2`: copied `frozen_engine/scout/pages.py`,
`integer`, line 481. Its internal `over` also considers only `late=max(ports)`.
This is a genuine missing objective term for 3+ calls in that helper, although
final real replay still counts every restow and the engine's checker remains
sound. The engine's copied scout ladder primarily uses LP diving rather than
calling that integer helper, so this defect alone is not an explanation for
all observed engine outcomes. The 0.9 ALNS marginal estimate similarly prices
pairwise relationships; real replay is needed because two targets at one call
share a blocker and a single box can be blocked again at another call.

## Graph and lift definitions

Vertices are boxes, physical stacks, hatch covers, and discharge **call
indices**. An upward arc joins adjacent surviving boxes in each physical stack;
a long box joins two stacks. When a hold target leaves, its cover activates and
every surviving box on that cover's deck stacks joins the removal closure.
At call p the cost is two hatch lifts per opened cover, plus the configured
off/on restow weight once per distinct nontarget box in the closure. A blocker
can recur at p+1. Removing departed boxes and rebuilding the arcs matters: a
long box that left earlier no longer connects its two stacks.

A page pattern is a bottom-up sequence of one length class with its own
capacity and securing legality; **ports may mix**. The master covers each box
exactly once, uses each physical stack at most once, and has one Boolean
activation y[cover,call]. The y sequence generalises C2's one-port-per-cover
layout. For each selected deck pattern/call, internal stack blockers and hatch
blockers are unioned before charging; conjunction variables charge remaining
deck boxes only when both their page and the hold activation are selected.
This corrects both missing intermediate ports and within-call double charges.

Concrete generated small graph: `cohort/small.p3.s263310018.json`, instance
`random.small.263310018.0.2`, has 20 load-box vertices (10 SGSIN, 5 MYPKG,
5 VNSGN), 68 cells, 16 physical stacks and two covers. The other four yard
containers are strangers, outside the page cover constraints. For example,
cover HC-002-P connects hold stacks 00102H/00302H to six deck stacks
00102D/00104D/00106D/00302D/00304D/00306D. Choosing any SGSIN hold pattern
there activates call 1's cover node and all surviving call-2/3 boxes in those
six deck stacks become call-1 blocker vertices. A second activation at MYPKG
can charge a VNSGN deck box again. This is the multi-call hatch sequence that
a single port label per cover cannot represent.

Concrete generated medium graph: `cohort/medium.p4.s263310036.json`, instance
`random.medium.263310036.0.2`, has 144 load-box vertices, all 20 feet, 240 cells,
64 physical stacks and six covers: exactly 60% cell fill. Its four discharge
groups are SGSIN 36, MYPKG 37, VNSGN 38 and HKHKG 33. There are now up to
24 distinct (call,cover) activation vertices. One final-call deck box can be
a blocker at three distinct earlier hold activations, even when every
individual deck page is in noncrossing discharge order. The restow variable
therefore needs a call index as well as a box identity; a single late/early
Boolean cannot describe this graph.

The implementation reuses the frozen engine's copied `scout.pages.Model`,
`scout.lash_oracle.Fast` and `e9fix.install` through local `frozen_engine/`;
that snapshot retains their original provenance headers. No live `engine/`
module, checker, parameter or `src/` file is changed. The new master replaces
the inherited two-port formulation; physical page legality and rounded
knapsack LP generation remain the original conservative family.

Cold first-fit and LP diving supply anchors. Medium anchors supply the
reference GM/draught for securing-aware pattern filtering. Circle-MWIS prices
produce extra no-crossing pattern seeds using current LP dual weights, followed
by capacitated knapsack pricing. A second seed removes the furthest discharge
box whenever an overfull candidate page needs a spill; the master must cover
the removed box on another page. Precedence-safe loading compares a
pairwise endpoint-crossing preference with the original shallow-yard ordering;
the lower **real replay** cost wins. All candidate assignments are checked
through `Problem.score`, and the runner finishes and fully checks them.

Premeasurement development smoke exposed a zero-dual degeneracy: the tiny
phase-1 coverage LP was already feasible after first-fit, so its zero objective
gave no incentive to create alternatives. The initial tiny smoke passed at
17 lifts (8 projected restows), but is development evidence, not the final
measurement. The final method exhaustively enumerates all legal ordered
subsets on every page when there are at most six boxes (deadline capped, with
an explicit exhaustive/truncated flag). Larger maps additionally run phase-3
pricing for diverse moment/hold/stack patterns before the graph integer master.
This creates alternatives even when feasibility-only box prices are zero.

TRAIN development also exposed an inherited heat-geometry problem on tiny
seed 263310005. The scout's empty-stow E8 filter restores the whole native
static row when every empty probe refuses a heat-sensitive box; this lets
impossible heat patterns reach the ballast finisher. Conversely, keeping only
the empty-probe-safe slots can discard valid upper placements: special-rule
geometry computes actual floors from the heights of present lower boxes,
rather than nominal tier coordinates. Ballast cannot change that geometry.

The final graph subclass therefore keeps the unchanged native unary table and
defers E8 to complete page-prefix geometry. `Model.legal` calls unchanged
`placement_violations` for each heat-sensitive box with its actual lower
prefix supplied as a `ShipState`, and refuses **only** the exact rule ID
`expert.e8.away_from_heat`. Results cache by page and box sequence. The same
gate is included in `lash_ok`, because inherited whole-page balancing otherwise
moves columns without calling `legal/add`. No checker, catalogue, mask or
parameter function is patched for this rule.

Why the rejection is necessary within this page family: each pattern occupies
all used tiers from the bottom of a physical stack (or the same pair for a
long page); distinct selected pages do not share physical stacks. The
checker's E8 floor is stack base plus the exact heights in that lower prefix;
upper boxes and boxes on other pages cannot change it. Heat-source rectangles
and configured clearance are fixed vessel/rule geometry and do not depend on
ballast mass. Thus any E8 refusal on that prefix remains a refusal in every
completion using that same pattern. We do not claim completeness of the
underlying native static mask, legality of other rules, or applicability to
mixed-length patterns/onboard-cargo families that this constructor excludes.

Regression coverage uses the actual failing TRAIN map to reject its bad hold
pattern, plus a synthetic geometry isolation with the unchanged checker: an
empty upper probe fails, while its actual 2.591-metre lower support lifts the
same upper box beyond the required clearance and the pattern remains allowed.
Root archives earlier graph rows separately and reruns the graph cohort under
the final source hash; native 0.9.0 and engine as-is rows remain unchanged.

The first small-map graph measurement exposed another necessary prefinish
gate: low-lift page combinations can violate legacy E6 even though every
individual page satisfies its local rules. E6 is a **whole-departure** rule,
unlike the E8 prefix geometry above. A heavy deck box is permitted only when
no compatible empty hold opportunity exists anywhere. Another page can fill
that opportunity or make it incompatible via support, stack mass, or DG
rules, so banning an individual deck pattern would be unsound. The native
`T.static` intentionally defers E6 when the conditional deck fallback is
enabled; our coarse hold reward was only a heuristic.

The final graph method now runs unchanged `e6_departure_violations` on every
complete replay's final ship. Exact E6 failures are recorded as replay legal
but ineligible for ballast finishing; no ballast assignment can change this
fixed-cargo compatibility predicate. The scored replay may still provide an
oracle reference, while only E6-eligible plans enter the returned archive.
On an E6-invalid master result, the method independently checks its ORIGINAL
selected pattern assignment, before OOG repair or balancing, and adds a
no-good cut only if that original complete combination also fails E6. Each
cut excludes the whole combination, never its individual pages; cuts stay
within the same model/index space. Retries are capped at six within the
existing method deadline. Unknown/missing-data rule IDs are not silently
promoted to E6 proofs. Rule-freeze activation is honoured by the native query.

Regression coverage holds a deck pattern fixed while filling other hold pages
and verifies that E6 changes from refusal to acceptance. A separate master
test verifies that excluding one complete combination still permits its
shared constituent pages in another solution. Earlier graph measurements
are archived separately before the final graph-only rerun; the baseline and
engine remain the unchanged as-is arms.

## Exact bound and its limits

`pattern_events` gives the exact (call,box) blocker set for an isolated
fixed-coordinate pure-length stack. `closure_counts` extends it to a fixed
departure assignment, long-box bridges and hatch dependencies. `assignment_bound`
exports the exact top/hatch closure count, a conditional lower bound on the
checker's full D15 restows because crane crossing access is relaxed. It is
exact for that assignment when no additional crane-crossing blocker occurs.
Crane/refusal/direct-delivery legality still belongs to the unchanged checker.

Example: fixed stack calls [1,1,2,4] bottom-up force pairs (1,2), (1,4),
(2,4): three blocker events, six default lifts. Counting inversion pairs would
overcharge the duplicated port-1 targets. A deck box for call 4 above a cover
opened at calls 1 and 2 incurs four lifts even if its deck stack is sorted.
If that same box already blocks a deck target at call 1 it is still counted
only once at call 1. These are regression tests, not claims about measured maps.

The port sequence **alone** has global restow lower bound zero: sorted cargo
with enough appropriate pages is a witness. The master optimality/best-bound
fields refer only to the current, incomplete pattern pool and an objective
including heel/hold feasibility preferences; they are not global lift bounds.
Rounded knapsack pricing and pure-length pages can miss legitimate stows.
Exact master top/hatch union accounting additionally requires both supports
of every long page to share one hatch cover. The inherited builder checks
equal tier indices but does not enforce this condition on general inputs;
it stores only the first support's cover. A cross-cover long page could
therefore make the master surrogate undercount hatch/restow costs. A future
version must guard against those pages or represent cover sets and their
full dependency closure. The full real replay/checker and multi-stack
`assignment_bound` remain the authorities on such general inputs.

`cohort-cover-scope.json` verifies this experiment's entire 39-map scope using
JSON only, without importing stowage, solvers or checkers. Every input hash
matches the fixed manifest, and **no possible adjacent-odd-bay long footprint
in any cohort map spans different covers**. The saved-main snapshot contained
84 rows and 240 selected long placements with zero cross-cover exceptions;
33 rows were still pending. Thus the generic builder limitation does not
affect this cohort's possible or currently selected long placements.
D15 preserves coordinates after discharges and does not construct supported
future restow positions, so even exact D15 counts are not a minimum over
arbitrary executable future policies. The graph master and fixed-assignment
counts alone claim no physical-future or global-optimum certificate. The
separate exhaustive tiny relaxation in `tiny_bound.py` can certify global
optimality for the checker's D15 objective when a checked plan attains its bound.

## Primary sources and what was adopted

Sources were retrieved from primary publication/author repositories; downloaded
code was neither read nor run. Abstract-only sources are identified below.

* Avriel, Penn and Shpirer (2000), [Container ship stowage problem: complexity
  and connection to the coloring of circle graphs](https://doi.org/10.1016/S0166-218X(99)00245-0),
  [author institution abstract](https://cris.technion.ac.il/en/publications/container-ship-stowage-problem-complexity-and-connection-to-the-c/).
  Adopted interval/circle conflict picture; abstract read, no claim of copying
  their full algorithm.
* Tanaka and Matsui (2021), [New Formulation for Coloring Circle Graphs and
  its Application to Capacitated Stowage Stack Minimization](https://arxiv.org/abs/2102.00691).
  Abstract and author proceedings description establish capacitated colouring
  and LP motivation; our pattern master is independently written, not their
  polynomial ILP formulation.
* Bernhart and Kainen (1979), [The book thickness of a graph](https://doi.org/10.1016/0095-8956(79)90021-2).
  Page terminology only (publisher abstract read).
* Heath and Rosenberg (1992), [Laying Out Graphs Using Queues](https://doi.org/10.1137/0221055),
  [author-hosted paper](https://courses.cs.vt.edu/cs5974/Fall2022/Resources/HeathPapers/HeathRosenberg_SIAMJComput_1992.pdf).
  Stack/LIFO versus queue/FIFO distinction; do not mistake a no-nesting queue
  layout for a no-crossing book page.
* Gavril (1973), [Algorithms for a maximum clique and a maximum independent
  set of a circle graph](https://doi.org/10.1002/net.3230030305),
  [paper scan](https://www.math.ucdavis.edu/~deloera/MISC/LA-BIBLIO/trunk/Garvil2.pdf).
  Inspired exact noncrossing endpoint-interval weighted DP. `circle_mwis` is
  our independently derived recurrence, exhaustively checked on small chord
  sets; uncapacitated MWIS is a relaxation/seed, not exact physical pricing.
* Belady (1966), [A study of replacement algorithms for a virtual-storage
  computer](https://doi.org/10.1147/sj.52.0078),
  [original IBM paper scan](https://www.bitsavers.org/pdf/ibm/IBM_Systems_Journal/052/belady.pdf).
  Furthest-next-use inspires which box is removed from an overfull pricing
  seed and the ready-load tie break. Cache optimality does not transfer.
* Hack, Grund and Goos (2005), [Towards Register Allocation for Programs in
  SSA-Form](https://publikationen.bibliothek.kit.edu/1000004125/2838).
  Separate spill selection from assignment; the three-author technical
  report is the correct source. The older C1 link `1000007166` is Hack's
  later solo dissertation. Our graph is not shown chordal, so no SSA colouring
  theorem or polynomial global stow algorithm is claimed.
* Kindermann, Klute and Terziadis (2024), [PACE 2024: One-Sided Crossing
  Minimization](https://doi.org/10.4230/LIPIcs.IPEC.2024.26), and Boehmer,
  George, Hauser and Palarus (2024), [Arcee solver description](https://kimonboehmer.net/files/papers/pace24.pdf).
  Adopted the pairwise crossing-cost idea as a ready-load preference, with
  fixed discharge order and precedence-restricted free loading order. PACE's
  benchmark objective is pairwise geometric crossings, whereas D15 charges
  unique blockers per call; pairwise crossings are not substituted for lifts.

## Verification

`test_graph.py` checks weighted circle MWIS against exhaustive subsets on
small random chord representations, arbitrary-port sorted loading, repeated
blocker events, hatch/stack union, removal of discharged long-box bridges, and
the exact multi-call master cost on forced three-port patterns. Execution and
full-checker measurement are recorded by the root runner under the team CPU
limit. No tests open sealed data or `data/raw/rcsppsuite/`.

The root executive ran the seven tests above under the sole CPU slot before
starting measurement: **7 passed**. Test execution disabled bytecode and pytest
cache writes and used one numerical thread. This validates graph accounting
and pricing regressions; full checker performance remains in the run rows.

After the pool-diversity and E8 changes, the complete updated suite passed
**10 tests** in a 1.55-second process under the sole CPU slot, with bytecode and
pytest cache disabled and all numerical thread limits set to one. This final
suite includes exhaustive tiny-order coverage and both E8 geometry regressions.

After the completed-stow E6 gate and safe no-good retries, the final updated
suite passed **12 tests** under the same single-thread, no-bytecode/cache
settings. Full-checker smoke and final source hashes are maintained by root.
