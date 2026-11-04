# Port order inside the securing assignment LP

`port_order.PortAssignment` subclasses the local frozen C5 `gap.Assignment`.
The securing, support, occupancy, OOG and moment rows remain in the initial LP;
this extension replaces its adjacent-tier/port-label cost with a per-call
top/hatch dependency model. It is a cold constructor hook, not C3's page
constructor and not a stored-plan improvement method. No shared source,
checker, rule tier or parameter is edited. The independent closure evaluator
adapts the plain C3 `graph_model.py` closure, with original provenance here.

## Rows, in plain words

The original variable x[b,s] puts box b in reported slot s. A long reported
slot touches two physical stacks but still represents one box. For each future
call p and possible slot s, let A[s,p] be the sum of x variables at s whose
destination is p or later, and T[s,p] the sum whose destination is exactly p.
These are expressions in x, not extra variables. A is the box surviving at
that slot immediately before p; T is a productive discharge at p.

There is one variable R[s,p] saying the surviving box must be removed, whether
productively or temporarily. The rows are:

```
0 <= R[s,p] <= A[s,p]
R[s,p] >= T[s,p]
R[upper,p] >= A[upper,p] + R[lower,p] - 1
R[deck,p]  >= A[deck,p]  + Open[cover,p] - 1
```

The third row exists for every physically shared-stack lower/upper slot pair,
including nonadjacent tiers: intermediate supports may have departed. The last
row exists for **every** hatch cover supporting that deck footprint. Open is
the OR of all same-call hold targets touching that cover; a long hold box can
therefore open two covers. Used[cover] is the OR of its future Open variables.
Both OR directions are present, so an opening also requires a real target.
All new auxiliaries are between zero and one in the LP.

The added weighted cost is exactly the native configured weights applied to:

```
2 * sum Used[cover]             current hatch lifts
2 * sum Open[cover,p]           later hatch lifts
lifts_per_box * sum (R - T)     later restow lifts
```

The negative target term is added to the existing x coefficient. R >= T makes
the restow sum nonnegative, including fractional points. It removes productive
discharges from the removal count; it does not reward omitting cargo. The
original box-coverage equality and large uncovered-box penalty remain.

## Why integral accounting is exact in its stated scope

Fix any complete integral assignment. Each A and T is now zero or one by
physical-cell occupancy. Productive targets and opened-cover deck cargo seed
the required set. Every upward implication then adds its higher box. A long
box has **one** R shared by both stacks: adding it on one side can propagate
to boxes above its other side, repeatedly until no new blocker remains. The
least fixed point is exactly the top/hatch removal closure of that stow.

The rows require every member of this least set to have R=1, and the least
set itself satisfies every row. Nonnegative lift weights therefore minimise
to precisely its weighted count; zero weights can leave nonminimal auxiliary
values, but cannot change that weighted optimum. One long box is charged once
per call, even when it blocks two stacks or two covers, and can be charged
again at another earlier call. Departed long boxes have A=0 at later calls,
so they cease to bridge their former stacks. There is no single-cover
assumption: cover sets come from all actual `T.slot_stacks` supports.

`exact_closure` independently rebuilds the surviving physical stack graph at
each call and evaluates this same least set. `exact_port_account` applies it
to an internal box-index/slot-index assignment and uses native objective
weights. It is a diagnostic alongside real `Problem.score`, never a substitute.

## The valid circle contribution

Three or more discharge calls alone do **not** force overstows: at one loading
call, freely sorting each unconstrained column in reverse discharge order
gives zero. The explicit global restow bound from the port sequence alone is
therefore zero. No positive free-order colouring bound is invented.

There is, however, a valid **weight-order-conditioned** circle conflict. Order
left chord endpoints by decreasing native legacy E2 class rank (same-rank
ties by decreasing discharge call), and right endpoints by discharge call
(reverse same-call ties). Two chords cross when the earlier-loaded rank order
and discharge order oppose a no-restow stack. We use only cliques whose every
rank gap exceeds the actual E2 class tolerance and whose PODs increase strictly.
For any two boxes in such a clique, E2 forces the heavier class below, while
its destination is earlier. Thus they cannot share a zero-restow column.
No fixed global loading order is imposed: their ordering is forced only when
they share the same physical stack under hard E2.

A longest-chain dynamic program finds certified such cliques. Their size is
a lower bound on the number of nonrestowing stack colours **within this
hard-E2 family**. It is not a positive global lift bound. For each physical
stack j and certified clique K we add:

```
sum assignment-to-j of boxes in K <= 1 + sum restow events on j
```

If m clique boxes share j, the earliest one forces each of the other m-1
later-destination boxes to be removed at its call, so the inequality is valid.
Additional blockers only weaken it. Long boxes occur once in each physical
stack row they occupy; their objective charge still uses their single
reported-slot R. The rows can strengthen a fractional OR relaxation: a two-box
crossing clique split 50/50 across two tiers admits zero restow in the plain
OR LP but needs at least one blocker event with the clique row. Its valid
integral assignment keeps the same one-event cost.

This certificate uses E2 rank and tolerance, not arbitrary raw-mass sorting.
It must be disabled when T4 is peeled (`circle_rows=False`). It is also
automatically disabled when rule-freeze switches native E2 from classes to
gross kilograms, because the copied legacy `T.rank` arrays then no longer
describe the active native weight-order theorem. This extension does not
claim a gross-kg circle certificate or chordality of the stowage graph.

## Integer tail and integration

The C5 integer tail treats all added columns as unbounded generic soft slacks
and skips rows without live unfixed assignment variables. That is unsuitable
for closure implications. This subclass instead creates Boolean R/Open/Used,
retains fixed-only and auxiliary-to-auxiliary rows, and keeps original
securing/moment soft slacks in thousandths. Conservative coefficient rounding
matches the copied tail's policy. Coverage remains exact for all unfixed boxes.

The hook is `PortAssignment(model, motions, settings, layout=...)`; layout is
ignored, and the original adjacent-restow/port-layout weights are set to zero.
`port_metadata` records graph sizes, clique scope and the zero port-sequence
bound. The initial LP already contains these rows. The wrapper must preserve
the priced tier order when exporting; C5's subsequent port/mass resort can
destroy the objective that was just optimised. Subsequent securing exchange
and OOG repair can also change that assignment, so final lifts always come
from the real replay and the unchanged full checker.

## Limits that remain

* Empty arrival, initially closed covers and no future loading calls are
  required by an explicit constructor guard. The current-cover formula is
  then two lifts per used hold cover. POD resolves to the first subsequent
  matching call, as in the native projector, including repeated visits.
* This is D15's fixed-coordinate **top/hatch** objective. Future crane-crossing
  closure can add blockers or hard violations; it remains relaxed here.
  D15 itself does not construct supported future replacement positions or a
  physically executable minimum restow policy.
  `rule_freeze_v1_enabled` and `exact_for_active_projection` are recorded in
  solver/account metadata. With the modern freeze active, crane closure is
  omitted and this is explicitly a top/hatch lower bound, not an exact full
  projection claim. The fixed cohort asserts the existing legacy activation
  is false; no rule setting is overridden to achieve this condition. In that
  bound parameter regime the native projector uses this same top/hatch closure.
* The securing LP has motion/height approximations, soft securing/moment rows,
  possible edge sampling, conservative page support/length restrictions, and
  other masks. Its objective value is not a certified global lift bound.
  Its original local page restrictions can exclude valid mixed-support stows;
  the new accounting handles every actual footprint admitted to that family.
* Yard relocations and current order-dependent work are not represented by
  these future-port rows. The loading sequence and actual total cost are
  resolved through unchanged real replay. Integer-tail coefficient rounding
  and soft-row discretisation do not establish a global optimality certificate.
* Tier completion remains D241/D242: every box must be aboard, full passes
  mean nothing peeled, and these conditional E2 rows certify no claim about
  a plan that breaks the underlying T4 rule. Rules are never peeled for lifts.

## Verification and source context

The 11-test suite passed under one numerical thread and pytest cache/bytecode
disabled. It compares fixed-integral LP objective and the independent closure
on all 162 combinations of three destinations and three-box permutations
using nonunit objective weights. Tests also cover shared blockers, repeated
events, a long bridge across two covers, a hold target opening both covers,
bridge removal after discharge, brute-force clique sizes, a fractional
strengthening example, fixed-only/auxiliary integer-tail contradiction, and
agreement with the retained C3 exact closure. Nice 5 was requested; this
sandbox refused its priority change, so the test ran at inherited priority.
These are model tests. Only the root runner's completed plans and unchanged
full checker establish actual passes and the completion-by-tier table.

The graph interpretation follows Avriel, Penn and Shpirer (2000),
[circle graphs and ship stowage](https://doi.org/10.1016/S0166-218X(99)00245-0),
and Tanaka and Matsui (2021),
[capacitated circle colouring](https://arxiv.org/abs/2102.00691).
The inherited securing LP and its fixed-motion row construction are copied
unchanged from C5, documented in its own notes; no approximation guarantee
from generalized assignment carries to these many physics rows. Our clique
inequality and closure linearisation are independently written here, rather
than attributed as a reproduction of either paper's full algorithm.
