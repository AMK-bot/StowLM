# Page neighbourhood component

`lns.py` is new code against the existing scout/layout `Model` protocol. It
imports no mutable `engine/` module, changes no checker rule, and uses the
existing page generator for pricing. The inherited Gilmore–Gomory pattern
master and the restricted integer-master idea documented by
`c2/c2-cgscale/NOTES.md` motivate this component. No new external algorithm is
claimed.

## Interface

`seed_assignment(model, assignment)` inserts a complete incumbent and returns
its pattern indices. The incumbent must be a pure-page packing with no tier
holes, complete box coverage and no repeated physical stack. Individual page
legality and the model's securing oracle are checked. This can reject an
otherwise checker-legal incumbent if it exceeds this model's conservative
deck caps or fitted securing gate; that is a search restriction.

`improve(model, chosen, free_covers, closed_hold=(), seconds=15, pricing_s=4,
seed=0)` fixes every incumbent pattern outside selected covers. It frees the
boxes and physical stacks on the selected covers, seeds transferred incumbent
patterns (plus prefixes/suffixes), runs bounded existing page pricing, then
solves the integer restricted master with one CP-SAT worker. A cover in
`closed_hold` cannot use any hold page, while its deck pages remain available.
Old pure-port layout labels are released during generation and restored in a
`finally` block. This lets the neighbourhood use mixed ports when capacity
requires them. Optional `hold_full` forces each selected hold pattern full;
optional `heel_slack_tm` and `lcg_slack_tm` limit moment deterioration relative
to the incumbent. These are heuristic physics guards, never full checking.
`hold_only` optionally names concrete box indices forbidden from every deck
column, including fixed outside columns. An inconsistent outside fixing thus
causes pool infeasibility rather than relaxing that restriction. This can help
construct E6-compatible candidates but is a heuristic restriction, not a
necessary inequality and not a strengthening of a certified map bound.
`forbidden_pairs` supplies exact column-index nogoods: two distinct columns
cannot coexist, or a repeated index disables that one column. A pair with an
absent column is irrelevant. The root can derive these from the actual E9
failure and exact incumbent pattern columns; the module does not weaken or
replace the checker. These are search constraints within the restricted pool,
not whole-map lower-bound cuts.

Before pricing, up to three seeded `Model.first_fit` calls repack the moving
boxes on the free physical stacks with the old layout labels released. All
new patterns enter through the existing legality and securing gates. Failed
calls retain their checked partial patterns. A complete packing supplies the
integer master hint together with unchanged outside patterns; forced closures
otherwise make the old incumbent hint infeasible. `hold_only` and `hold_full`
restrictions are screened before selecting that replacement hint. Full-map
moment constraints and replay/physics are still checked at their normal gates.

The return dictionary has `chosen=None` on failure. Otherwise it includes
`chosen`, `assignment`, `objective_lifts`, `bound_restricted_pool`, and timing.
Callers must replay/order the resulting assignment, run the full checker, and
keep only fully checked improvements. It deliberately may return a higher
cost candidate when a forced closure needs one.

## Objective and bounds

On empty arrival, using a hold cover costs two current hatch lifts. Each
distinct destination served in that cover's hold costs two later hatch lifts.
The Boolean edge variable is the OR of its chosen hold patterns. For each
deck pattern and earlier call, a surviving box is restowed when either an
earlier box is below it in the same page or that call opens its cover. The
code charges the union: boxes already counted internally incur no second
charge when the cover also opens. The product of a chosen pattern and its
cover edge is encoded by a Boolean AND. This also works for more than two
ports. Objective input weights are scaled by exact decimal fractions.

This objective is exact for the pure-page vertical and hatch dependencies of
D15. It **excludes yard relocations, current restows, and additional
crane-crossing closure**. Those are measured by real replay. No assumption
that relocation cost stays fixed is required for accepting a candidate.

`bound_restricted_pool` is only the solver's bound for this fixed neighbourhood
and its generated pool. Missing patterns, fitted mass/height constraints,
pure-length pages, fixed outside pages and optional moment rows all restrict
the feasible set. Its value is **not a rigorous whole-map lower bound** and
must not be included in a certified gap. The inherited LP pricing objective
also is not used as a rigorous bound.

## Verification

Synthetic tests cover a hold closure retaining deck use, unchanged outside
pages, duplicate physical stacks, hatch-plus-internal restow deduplication,
multiport cover charges, per-call restows, exact decimal objective weights,
deadline handling and restoration after a pricing exception. Additional tests
cover `hold_only` (hold retention and infeasibility when its only hold closes)
and diversified first-fit seeds/hints on a forced-closure neighbourhood. All
**10 tests passed** in 0.66 seconds with one numerical thread and a
root-authorized exclusive compute slot while `BENCH-RUNNING` exists; pytest's
cache provider was disabled. Real-map replay/full-check results belong in the
root experiment's `NOTES.md`.

An eleventh regression covers distinct-column nogoods, single-column bans and
pairs with absent indices. Root subsequently ran all eleven tests successfully
with the original page and bound tests, cache provider disabled.
