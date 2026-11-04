# Structural diagnosis: why plain treewidth DP is not the first repair solver

`graph.py:summarize(instance)` implements two unweighted graph diagnostics without
networkx. These diagnostics do **not** prove a lift lower bound or an optimum.
The implementation and analysis are original; no solver code was downloaded or
copied. Only approved validation/TRAIN paths were read via `lab/catalog.py`.
`lab/README.md` was read when it became available. Parent experiments measure
candidate plans with `lab/score.py`; this diagnostic produces no candidate plan.

## What the graphs represent

A box decision variable chooses a ship slot. A 20-foot slot occupies one physical
cell; 40/45-foot slots occupy adjacent cells in two ship stacks. A candidate slot
is included only when every occupied cell accepts that length. All other unary
rules (reefer, dangerous goods, weight, visibility, existing occupancy, etc.) are
relaxed, so candidate domains are deliberately supersets of real feasible domains.

The **physical incidence graph** has typed vertices for every container in the
instance, every geometrically available slot, ship stack, occupied yard stack,
and hatch cover. Edges join each slot to its supporting ship stacks, every ship
stack to its hatch cover where one exists, and every yard box to its initial yard
stack. A fixed onboard box joins its physical support stacks. A load box joins
every ship stack supporting some candidate slot. These final edges deliberately
coarsen the box-slot relation to box-stack incidence; keeping explicit slot
vertices documents the geometry without multiplying box-domain edges. Neither
yard ordering nor tier order is encoded by an undirected edge. Empty relocation
destination stacks and quay/crane states are not represented. This is a structural
picture of local interaction, not a factorization of the full constraint system.

The **occupancy primal graph** has one variable vertex per required load box.
Two box variables are adjacent when some pair of geometrically allowed assignments
could overlap a physical cell. Its constraints are pairwise non-overlap factors,
with the relaxed domains above. This graph says how many *box assignment variables*
a conventional elimination table may need to retain. Specialized domains could
remove edges, and auxiliary occupancy variables could change the graph; those are
separate formulations, not a stronger inference from this graph.

Global intact/departure/intermediate-state physics is omitted from the first two
graphs. With a single unreformulated factor checking all box placements, the full
box-variable primal is a clique of width `number_of_load_boxes - 1`. Factoring
mass and moment sums through auxiliary running totals may change that conclusion,
but exact tables must then retain those totals and the associated large domains.
Low incidence width alone does not guarantee a small dynamic program.

## What a physical lift means here

Edges have no lift weights. An actual objective is a cost on a transition between
physical states: one yard relocation costs `objective.relocate`, a same-call
restow lift costs `objective.restow`, and lifting a cover off or back on costs
`objective.hatch_cover`. Future projection adds weighted restow and cover lifts
at each later discharge call. Clearing one cover can require clearing its entire
deck area, not merely one slot. A future off/on restow is normally two lifts,
according to the instance's `future_restow_lifts_per_box`; multiple inversions do
not automatically imply distinct lifts. Productive loads/discharges cost zero in
the nonproductive objective. Static edges cannot recover these transition costs.

A correct exact dynamic-programming/decision-diagram state must preserve enough
information to distinguish stack contents and heights, yard accessibility and
relocation locations, cover status, future discharge interactions, and all rule
obligations relevant to continuations. Equal heights alone do not imply equal
future options or equal remaining cost.

## Measurements on the fixed map set

The first approved `quick_small` and `quick_medium` maps were inspected without
solving or finishing. Times are graph heuristic wall time on this shared machine,
not planner wall time; no lift improvement/pass claim is made by this diagnostic.

| Map | Loads | Candidate slots per box | Incidence V / E | Min-fill width upper bound | Time | Occupancy primal |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `random.small.40100011.0.8` | 18 | 46–92 | 210 / 596 | 20 | 0.095 s | K18, width 17, 0.0002 s |
| `D192.medium-feeder_500-f60-high-s192200000` | 88 | 90–240 | 733 / 6330 | 64 | 3.112 s | K88, width 87, 0.019 s |

The small map is
`experiments/mvp/corpus/val/small/feeder_800/random_yard/seed-040100011.json`;
the medium is
`experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_500/f60-high/seed-192200000.json`.
The stored small 0.9.0 row passes with **22 lifts: 14 relocations + 4 current cover
lifts + 4 future cover lifts, no restows**. Thus the contract's typical 3+4+4
example is not universal; on this selected map relocations dominate. The medium
has no stored 0.9.0 comparator; its earlier V6 selection certificate is distinct.

Min-fill eliminates a vertex after making its remaining neighbors a clique. The
largest remaining neighbor count is an **upper bound on that represented graph's
treewidth**, not a lower bound. The reported clique widths of the occupancy graphs
are exact for those graphs because every vertex pair is adjacent; they are not
an impossibility proof for other formulations. No claim is made that these
heuristic incidence widths are optimal. Known empty/path/cycle/clique graphs and
150 deterministic random graphs were checked against a direct pair-count min-fill
reference (same complete elimination order, width and total added edges).

## Mathematical sources and practical consequence

- Holger Dell, Christian Komusiewicz, Nimrod Talmon and Mathias Weller (2018),
  *The PACE 2017 Parameterized Algorithms and Computational Experiments Challenge:
  The Second Iteration*, [official paper](https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.IPEC.2017.30).
  The challenge assessed exact and heuristic decomposition construction on shared
  instances. Its implementation record motivates measuring width rather than
  assuming the vessel's visual block structure makes width small. The
  [official solver catalog](https://github.com/PACE-challenge/Treewidth) includes
  min-fill heuristics and more advanced decomposition methods. This implementation
  is plain greedy min-fill, not a reproduction of a PACE-winning solver.
- David Bergman, Andre A. Cire, Willem-Jan van Hoeve and J. N. Hooker (2016),
  *Discrete Optimization with Decision Diagrams*,
  [primary publication](https://pubsonline.informs.org/doi/abs/10.1287/ijoc.2015.0648).
  Their method uses relaxed diagrams for bounds and restricted diagrams for
  incumbents, and branches to restore exactness. This supports a state-merging
  approach to the loading sequence, but does not supply a cargo-specific valid
  merge rule. A bounded beam of genuine states is restricted search and by itself
  cannot certify a lower bound. A relaxed merge must retain every original
  continuation and never increase its minimum cost to justify a minimization bound.

For fixed variable-domain size D and bag size w+1, a straightforward table has
D^(w+1) assignments before problem-specific compression. These measured whole-map
box graphs are already dense, so generic box-variable tree decomposition does
not give a practical whole-map exact solver. Keep the diagnostic and the
restricted-neighborhood idea. Fixing most boxes and freeing k boxes reduces the
assignment primal to at most k variables. Enumerating a hatch or yard-stack
neighborhood can therefore use the same exact search engine with a manageable
boundary, but a hatch need not be independent: outside yard blockers, shared
covers, long-box supports and global physics remain coupled. Fix the boundary
explicitly and check every accepted completed plan with the full checker.
A neighborhood optimum only proves optimality within that neighborhood.
