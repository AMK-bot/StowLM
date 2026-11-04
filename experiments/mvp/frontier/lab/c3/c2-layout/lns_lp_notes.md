# Fixed-charge page LP variant

`lns_lp.py` copies the frozen C3 `lns.py` so the running f60 measurement is
unaffected. It adds an actual fixed-charge page LP **after** coverage pricing
has supplied a pool, before the existing fixed-charge integer master. Its
`improve` interface adds only `fixed_charge_lp_s=2.0`; all original options
remain available. No shared or production file is imported as mutable code.

The LP has a variable in [0,1] for each concrete page pattern, each used hold
cover, each hold-cover/destination edge and each cross-hatch blocker product.
Every box is covered exactly once; each physical stack is used at most once;
outside columns are fixed to one. A selected hold column requires its used
cover and all destination edges. The edge variable also implies its cover.
Cover and edge variables are at most the sum of their contributing columns.
These are the usual OR convex-hull inequalities. Internal page restows cost
the column fraction times their exact internal count. For a remaining deck
blocker, r >= x + edge - 1, r <= x and r <= edge relax the Boolean product.
Only boxes not already internally blocked at that call enter this term, so
internal/hatch blockers are not double counted. Hatch and future restow
weights are the same exact-decimal-scaled coefficients used by the IP.

The LP includes `hold_only`, `hold_full`, and exact `forbidden_pairs`
restrictions. Optional heel and longitudinal moment guards may be omitted;
the IP still enforces those requested rows. The LP solver has one numerical
thread and a maximum two-second time limit within the neighbourhood's total
deadline. Successful LP solutions provide near-zero/near-one column hints
to CP-SAT; an unavailable LP solution falls back to the greedy/incumbent hint.
The returned metadata identifies the LP status, restricted-pool objective,
elapsed seconds and number of integral hints.

This is a relaxation **only of this fixed neighbourhood's restricted page
pool**. Fixed outside columns, pure-page geometry, fitted securing gates,
missing columns and optional search restrictions prevent its objective being
a whole-map lower bound. It is never added to forced relocations as a
certified map bound. No full-family fixed-charge pricing termination proof
is claimed. Yard relocations, additional crane-crossing closures and full
physics remain measured by real replay and the unchanged full checker.

Four synthetic tests were added for integral hatch charges and LP-to-IP
hints, restow union charges, a strictly fractional covering example, and
hold-only/nogood/deadline handling. All four passed in root's authorized
numeric slot, with the pytest cache provider disabled and one numerical thread.
