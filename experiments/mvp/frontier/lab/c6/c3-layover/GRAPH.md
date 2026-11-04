# Two cold arrival assignment graphs

These are the first predeclared development seed in the small/30% and
medium/30% cells. Counts come from the given Instance and a fresh native
Expander, without reading any previous plan or ballast recipe. Unary edge
counts below include heat screening but no optional sight restriction.

| Object | small/o30/331401024 | medium/o30/331401048 |
|---|---:|---:|
| Physical cells | 68 | 240 |
| Physical stacks | 16 | 64 |
| Hatch covers | 2 | 6 |
| Arrival boxes | 20 | 72 |
| Due now | 1 | 1 |
| Through boxes initially aboard | 19 | 71 |
| New bookings | 28 | 96 |
| Forced through restow-offs for due access | 8 | 9 |
| Fixed boxes after due closure | 11 | 62 |
| Free box vertices after due closure | 36 | 105 |
| All departure box vertices | 47 | 167 |
| Fixed cargo mass, kg | 47,641.118 | 515,451.962 |
| Full physical pages (20-foot and long alternatives) | 24 | 96 |
| Page-tier slot alternatives, counting long alternatives | 102 | 360 |
| Eligible fixed edges | 11 | 62 |
| Eligible free edges | 2,048 | 18,500 |
| All eligible box-to-slot edges | 2,059 | 18,562 |

The left vertices are departure boxes. Right vertices are page-tier slots;
cell vertices prevent overlapping slot alternatives. A long slot consumes
two cells, although this cohort uses only 20-foot boxes. Stack rows express
support, weight order, rating, height, sight and securing. Hatch-cover nodes
supply port-layout prices and explain why an apparently unrelated deck box
must move before a hold box can discharge. The departure assignment graph
excludes the due box, but its required actions remain in the plan prefix.

Initially both small covers carry seven arrival deck boxes. The due closure
removes all seven deck boxes on `HC-002-S` plus one through stack blocker,
opens that cover once and discharges the due box. Seven deck boxes remain on
`HC-002-P`. The medium arrival has deck blockers 6/6/6/6/8/8 on
`HC-002-S`, `HC-002-P`, `HC-010-S`, `HC-010-P`, `HC-022-S`, `HC-022-P`.
The due closure removes eight deck boxes from `HC-022-S` and one through stack
blocker; the other five covers remain blocked. Both prefixes contain one
cover-off and one discharge in addition to the reported restow-offs.

Every kept box has one fixed edge at its actual native box/slot indices.
For a securing row r its contribution is
`A[r,k]*mass + Bt[r,k]*transverse_wind + Bl[r,k]*longitudinal_wind`.
Its LP variable is one, so the contribution is mathematically a constant
on that row; fixed boxes also consume their true tier and mass capacity.
Mass moments work identically: `mass*x`, `mass*y`, and actual
`mass*(support_height + height/2)` enter normally, once. Free cargo fills the
remaining capacity. This preserves relieving negative securing coefficients
at low tiers; it does not replace them by a positive knapsack weight.

A keep-or-restow branch deletes selected fixed-edge restrictions only after
actual Expander removal. Clearing a lower box removes upward and cover
blockers too. The original replay prices every off/on operation and cover
lift in D11, including projected later-port D15 lifts. A restow branch cannot
pretend its newly free cargo was ashore at arrival.

Given-state native arrival GM is 1.985978 m on the small map and 1.899805 m
on the medium map. Mean draught is 6.557405 m and 5.575968 m respectively.
These true states initialize securing motion; they are not guessed from an
empty ship. Native arrival static and special-rule diagnostics had zero
violations on both examples. Full departure checking remains necessary.
