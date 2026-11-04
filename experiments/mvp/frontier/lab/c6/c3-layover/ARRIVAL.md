# Arrival adapter

`arrival.py` adapts the C4 discharge and restow preparation without importing
its mutable method. `arrival_model.py` copies the physical page extraction
from C4 `pages_base.py` and the native-waterplane sight-roof construction from
C4 `pages_sight.py`; it implements only the interface needed by the assignment
LP. Native checker/Expander APIs are read-only dependencies.

The assignment graph contains every box needed at departure: new loads and
through cargo, including through boxes the due-discharge closure puts in the
quay buffer. Due cargo is absent. Each kept box has exactly one edge, its
actual arrival box index and slot index. That edge is fixed to one by the LP.
All physical page slots, tiers, stack mass limits and height limits remain
unchanged. Free edges cannot occupy a kept box's cells. Thus a fixed box's
ordinary securing-force, wind and moment coefficients enter the same rows as
new cargo; no extra aggregate approximation or double counting is introduced.

A finite branch list chooses keep, restow all lowest stack roots, restow deck
roots, or restow roots under one cover. Expander discharges due cargo first,
deck before hold and upper before lower, then expands the selected restow
roots. Upward blockers and deck boxes over a hold cover are removed by that
same real expansion. The metadata records actual restow-off moves. A full
assignment is sequenced with its kept supports present; unchanged placements
are skipped. Prefix and loading actions are then replayed against the
**original** Instance. Final cargo coverage and an empty quay buffer are
required. Lift ranking is the ordinary D11/D15 score of that original replay,
so branch freedom never gets a free removal/reload.

Heat bookings use actual box envelopes and native heat-source geometry and
clearance lookup. The unary mask retains a heat slot only if some attainable
lower-height sum could be safe. Full-column legality checks the chosen exact
height sum. There is no all-denied-mask restoration. Existing kept pairs remain
given occupancy in the LP, but full-column heat screening also checks them:
native special-rule heat breaches are hard, including unchanged Stage B cargo.
Only native core rules apply catalogue inheritance tolerance; this adapter does
not generalize that tolerance to heat. Ordinary full checking remains decisive.
The recorded cohort has no OOG or DG; the optional generic OOG relocation hook
returns the assignment unchanged to avoid moving a fixed pair accidentally.

`arrival_condition` computes native hydrostatics from the given input ship,
cargo and tank masses. It reads no prior plan, assignment or ballast recipe.
It supplies true GM, aft/forward/mean draught, heel and block coefficient for
the first securing reference. This is initialization, not a pass certificate.

Seven adapter tests passed: real due closure and input immutability; original
indices/full pages/fixed occupancy; complete all-restow item set; rejection of
missing cargo or moved fixed cargo; support-height heat geometry; exact
agreement with native arrival hydrostatics; and kept heat breaches remaining
subject to the native hard rule. All numerical threads were one;
`nice -n 5` was requested but macOS denied setpriority in the sandbox.

Two development snapshots, not solves:

| Map | Arrival GM m | Native mass kg | Departure cargo kg | Fixed/free after due closure | Full pages |
|---|---:|---:|---:|---:|---:|
| small/o30/331401024 | 1.985978 | 8,834,330.623 | 612,951.026 | 11/36 | 24 |
| medium/o30/331401048 | 1.899805 | 7,273,456.112 | 2,683,579.349 | 62/105 | 96 |

Both had zero free boxes with an empty unary mask. Their true mean draughts are
6.557405 m and 5.575968 m. No LP or full-pass claim follows from these snapshots.

Read-only audit: native `rules_special.special_violations` emits hard E8;
`rules._core.emit` alone applies Stage B catalogue inheritance. Both development
arrivals had zero core and special violations. `legal()` now heat-checks kept
prefixes too, so an unsafe fixed prefix requires a restow branch rather than
being treated as exempt. The LP conservatively enforces existing E1/E2 order
even where the core checker could tolerate an inherited breach. This narrows
the search; it cannot create a false full pass. No such breach appeared in the
two graph examples.
