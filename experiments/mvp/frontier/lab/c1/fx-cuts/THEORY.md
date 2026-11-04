# Graph model and bounds for fx-cuts

Read-only audit of the fixed `lab/sets/quick_small.txt` (10 maps) and
`quick_medium.txt` (3 maps), using `lab/catalog.py`'s positive provenance gate.
No sealed data was read. All 13 audited maps have no initial onboard boxes and
all hatch covers initially closed. No solver, checker, or baseline source was
edited. This file supplies theory and structural evidence to `NOTES.md`;
the parent executive supplies fresh finished-plan/checker comparisons.

## What the graph means

A vertex represents one load-list box, with its length/TEU, mass, POD call and
static eligibility. Its label is a physical area `(hatch cover, hold/deck)`.
For a packing witness the label must be refined to an actual slot, since TEU
alone misses 40-foot paired cells, column length modes, support and other rules.
The coarsest useful graph is therefore a graph of box-area choices with shared
resources, rather than an ordinary uncapacitated box similarity graph.

For a fixed completed stow, introduce an activation vertex `u[h]` if hatch `h`
must be opened at this call, and `y[h,p]` if its hold contains a box due at later
call `p`. Their raw hatch cost on the audited empty/closed maps is at least
`2 sum_h u[h] + 2 sum_(h,p) y[h,p]`. The future part is exactly the counting
rule in `src/stowage/moves/_projection.py`; current work can exceed the lower
bound if the load order reopens covers. Each `y` is the OR of all relevant
hold-box choices. A hatch is therefore a shared hyperedge/activation, costing
two lifts once per relevant call, not two per pair of boxes.

A deck box that survives call `p` is a blocker if **any** hold target opens its
cover then. It costs the configured future restow count (default two). Likewise,
the upward dependency closure of a stack counts each blocking box only once at
a call, even if several targets need it removed. Pairwise sums over all
conflicting box pairs can overcount this objective. Physical spanning boxes,
crane crossing closures, and later refusal rules still require ordinary replay.
Yard stack adjacency gives top-before-bottom precedence arcs, with a relocation
penalty for digging out a blocked target; exact yard cost depends on load order
and buffering and must also be replayed.

Two real examples from the audit:

| Map | Box vertices and POD counts | TEU by POD | Area labels / covers | Potential cross-POD pairs | Cover/POD activation nodes |
|---|---|---|---|---|---|
| `random.small.40100011.0.8` | 18: MYPKG 8, SGSIN 10 | 13, 16 | 4 / 2 | 80 | 4 |
| `random.medium.185100000.0.2` | 121: MYPKG 44, SGSIN 77 | 70, 123 | 12 / 6 | 3,388 | 12 |

The small example has 7 twenty-foot and 11 forty-foot boxes. Each cover has a
6-cell hold and 40-cell deck. The medium example has 49 twenty-foot and 72
forty-foot boxes. Its covers have hold capacities `[4,4,18,18,8,8]` and deck
capacities `[30,30,30,30,30,30]`: 193 requested TEU in 240 cells (80.4%). Its
three fore-to-aft paired-side stations have capacities `[68,96,76]`. A split
`SGSIN -> first two stations, MYPKG -> last station` fits the TEU arithmetic
(123 <= 164 and 70 <= 76), but is not a packing witness. This demonstrates
precisely why passing an area capacity screen cannot certify construction.
The pair counts in the table are potential interactions, not actual restows.

## Which cut algorithms apply

For binary labels, an exact ordinary cut requires every pair table to satisfy
`theta(0,0)+theta(1,1) <= theta(0,1)+theta(1,0)`.
A nonnegative split penalty `w [label_i != label_j]` satisfies this condition
and encourages same-POD clustering. A collision penalty
`w [label_i == label_j]` violates it for `w > 0` (left side `2w`, right side
zero). Reversing one binary variable can repair some edges but cannot repair
every inconsistent cycle. Real slot competition, directed overstow, shared
hatch activations and hard size capacities cannot be declared a submodular
pairwise energy merely by naming them graph cuts.

QPBO/roof duality can fix part of an optimum of its **encoded binary energy**;
it need not label all boxes. Its persistency certificate does not automatically
apply to the full checker problem when capacity, packing or physics was omitted.
Alpha-expansion guarantees also need the relevant metric energy assumptions;
arbitrary restow tables and capacities do not inherit them. A balanced
hypergraph/CP model with exact small column packing is the closer model here.
An OR hatch activation can be modeled explicitly with auxiliary booleans;
counting pairwise edges instead changes the cost.

Primary sources consulted, with their role:

- Vladimir Kolmogorov and Ramin Zabih (2004),
  [What Energy Functions Can Be Minimized via Graph Cuts?](https://www.cs.cornell.edu/~rdz/Papers/KZ-PAMI04.pdf).
  Characterizes graph-representable binary terms; supplies the submodularity
  test above. It justifies exact cuts only for the applicable energy class.
- Yuri Boykov and Vladimir Kolmogorov (2004),
  [An Experimental Comparison of Min-Cut/Max-Flow Algorithms for Energy Minimization in Vision](https://discovery.ucl.ac.uk/id/eprint/13383/).
  Practical evidence for fast max-flow methods on appropriate cut graphs;
  it does not remove the stowage capacity or packing constraints.
- Carsten Rother, Vladimir Kolmogorov, Victor Lempitsky and Martin Szummer (2007),
  [Optimizing Binary MRFs via Extended Roof Duality](https://alumni.media.mit.edu/~szummer/papers/Rother-etal-QPBO-cvpr07.pdf).
  QPBO, probing and energy improvement for nonsubmodular binary problems;
  useful for partial solutions, with guarantees scoped to the modeled energy.
- George Karypis and Vipin Kumar (1998),
  [Multilevel k-way Partitioning Scheme for Irregular Graphs](https://doi.org/10.1006/jpdc.1997.1404).
  Coarsen, partition, then refine; supports a fast grouping heuristic but does
  not make balanced partitioning or physical placement exact.
- Tobias Heuer, Nikolai Maas and Sebastian Schlag (2021),
  [Multilevel Hypergraph Partitioning with Vertex Weights Revisited](https://arxiv.org/abs/2102.01378).
  Studies tight weight balance and heavy-vertex preassignment. This is directly
  relevant to mixed-size boxes, where equal vertex counts are poor capacity
  surrogates. No downloaded code was run.

## A valid necessary-capacity bound

`theory.py:hatch_relaxation` groups boxes with identical `(TEU, future call,
statically eligible areas)`, assigns their counts to areas, constrains area cell
capacity, and charges the `u`/`y` activations above. It ignores all dynamic
support, weight, DG, visibility, stability, yard and load-order restrictions;
this enlarges the feasible set. It also ignores onboard cargo and its costs,
which is another relaxation. Thus its minimum is a lower bound on **raw hatch
lifts**, provided the shared static eligibility tables are necessary screens.
All nine passing stored small stows were checked to use only static-eligible
slots, and each bound was below its actual hatch count. This consistency check
does not replace that modeling premise. A slot spanning multiple area labels
returns the safe bound zero instead of silently excluding a possible stow.

Initially off covers are explicitly tracked: each must be replaced once;
an initially closed cover used for loading needs off/on. On a time limit the
function reports CP-SAT's integer-rounded dual bound, never an incumbent as a
lower bound. The following audits all solved the relaxed model to optimality;
their CP-SAT times were below 0.06 s/map with one worker (instance/Problem setup
takes longer). These are bound computations, not passing plans.

| Fixed quick-small order | Hatch lower bound | Stored hatch now + later | Stored total D11 |
|---|---:|---:|---:|
| `random.small.40100011.0.8` | 0 | 8 | 22 |
| `random.small.40100023.0.8` | 4 | 8 | 22 |
| `random.small.40100036.0.2` | 4 | 8 | 11 |
| `random.small.41100002.0.2` | 4 | 8 | 12 |
| `random.small.41100018.0.2` | 4 | 6 | 10 |
| `sim.small.40100010` | 4 | 4 | 4 |
| `sim.small.40100025` | 0 | 8 | 26 |
| `sim.small.40100034` | 6 | no stored pass | no stored pass |
| `sim.small.40100046` | 4 | 8 | 9 |
| `sim.small.41100007` | 4 | 8 | 9 |

| Quick-medium map | Boxes / TEU | Cover count | Hatch lower bound |
|---|---:|---:|---:|
| `D192.medium-feeder_500-f60-high-s192200000` | 88 / 143 | 6 | 4 |
| `D192.medium-feeder_800-f60-low-s192200101` | 162 / 272 | 8 | 6 |
| `random.medium.185100000.0.2` | 121 / 193 | 6 | 6 |

The low bounds leave substantial packing/physics gaps. They do not establish
that an eight-hatch-lift port-blocked plan can be improved legally.

## An independent four-lift certificate on one small map

`sim.small.40100010` admits a proof independent of the static table. The
load-list box `QZXU0000098` is a forty-foot reefer due at VNSGN (future call 2).
There are **zero deck cells with a reefer plug**, and eight hold cells with
plugs. Any legal all-loaded stow must therefore put this reefer under a cover.
All covers start closed. Accessing that hold requires at least one cover off
and on now (two lifts); the D15 future projection requires off and on at the
reefer's POD (two more). All objective weights are one and all other lift
counts are nonnegative, so every legal complete stow has D11 >= 4.

The stored Frontier plan places it at hold slot `0020104` and has exactly
`hatch_now=2, hatch_later=2, relocations=restow_now=restow_later=0`, total four.
The parent's fresh `quick-v2-30` scorer run also passed this map through the full
checker at four. `optimum_certificate.json`, written by the parent's
`certify.py`, binds the geometric evidence to that result row and its hash.
The lower bound and the freshly checked plan therefore establish an optimum of
four on this one map. The elementary geometric bound is implemented as
`elementary_reefer_certificate`; the bound alone does not certify a plan.

The certificate's block/cover POD audit records mixed-port deck blocks and only
VNSGN in the used hold, with `one_port_per_used_block=false`. Thus an optimum
can mix ports within a deck block. This does not prove that a plan with one POD
per used block cannot also attain four; that existence question remains open.

There is **no general proof that small-map optimum equals port blocking**.
Two ports and two covers alone are insufficient. If both ports can use only
deck, hatch cost can be zero. If necessary hold cargo for both ports fits under
one cover, it can cost two lifts now plus four later (six), below using two
exclusive covers (eight). These examples explain missing hypotheses, not
feasible counterexamples under every rule on the audited maps. Port blocking
attains the hatch minimum only after proving the necessary number of accessed
covers and cover/POD incidences; it still need not minimize yard relocations.

## Size capacity is not a matroid

Even the necessary TEU capacity system fails the matroid exchange axiom. In a
two-cell capacity take one forty-foot box `long` of size 2 and two twenty-foot
boxes `short_a, short_b` of size 1. Sets `A={long}` and
`B={short_a,short_b}` are both capacity-feasible and `|A| < |B|`, but neither
element of `B - A` can be added to `A`. `matroid_counterexample()` asserts this
directly. Every audited small/medium manifest contains both 20- and 40-foot
boxes. The result refutes the proposed general mixed-size capacity/matroid
argument; it is not a statement that these three boxes alone satisfy every
checker rule in a chosen full ship. Uniform unit-size capacity can form a
uniform/partition matroid; mixed TEU sizes remove that simple greedy proof.

## What share of the stored lifts is available to move?

Across the nine stored passing quick-small maps, total D11 is 125 (mean
13.8889): current hatch 32, later hatch 34, relocations 43, later restows 16,
current restows zero. Hatch lifts are **52.8%**, relocations **34.4%**, and later
restows **12.8%** of this paired stored total. The sixteen later restows occur
on `sim.small.40100025`; its whole current/later hatch total is eight.
The failed map `sim.small.40100034` is excluded from these means and shares.
These are fixed `lab/baseline.json` replay components with stored checker
verdicts, not new experimental passes and not the pooled test/unseen statistic.
There is no stored 0.9.0 comparator for these quick-medium maps.

Reproduction (from repository root; each command uses one numerical process):

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 .venv/bin/python experiments/mvp/frontier/lab/c1/fx-cuts/theory.py --set quick_small
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 .venv/bin/python experiments/mvp/frontier/lab/c1/fx-cuts/theory.py --set quick_medium
```

Verification performed: both complete audits ran; matroid assertions passed;
all 13 capacity relaxations returned OPTIMAL; the nine stored passing small
assignments passed the static membership check and their hatch counts exceeded
or equaled the bound. No finisher or full-checker work was launched by this
theory audit. Keep the exact activation model, the independent reefer certificate
and the negative matroid result. Treat cut clustering as a constructor heuristic
until a full-checker comparison establishes a benefit.
