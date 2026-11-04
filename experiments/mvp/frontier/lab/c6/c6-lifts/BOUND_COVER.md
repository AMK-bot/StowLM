# Compact cover bound with necessary tension

`bound_cover.py:lower_bound(instance, budget_s=60, seed=0)` first recomputes
the frozen `bound.py` certificate, sharing its fresh feasibility tables. It
then spends the remaining wall deadline on a compact cover-by-physical-tier
integer relaxation. It never reads old results or plans. The final certificate
is the maximum of the independently valid fresh old and new lower bounds.
Attribution must distinguish increased lower bounds from reduced primal lifts.

## Why a compact model

The frozen physical-cell model has tens of thousands of variables and often
returns only its analytic bound in fifteen seconds. The new model merges
physical columns sharing a cover and merges eligible boxes into downward mass
buckets of 2 tonnes. A variable counts members of one group assigned to a
cover/tier footprint. The full set of unary-eligible aggregate footprints is
part of each group's key. All actual choices remain available. Mass and
height capacity round cargo down and capacity up; allocation-specific securing
coefficients are minima over the original members. Tier occupancy cannot exceed
the total available physical cells or occupancy immediately below. Individual
column geometry, exact support heights, E1/E2 and bookings beyond unary
eligibility are relaxed. Hence this model is smaller but remains a relaxation.

It retains a current opening/closure charge for every used hold cover and a
projected opening/closure charge for every port-cover edge. A deck box lasting
beyond an incident discharge call pays its projected restow charge once per
call even when its footprint spans covers. In-stack overstows are free.

The compact model also retains the frozen proof's safe E6 empty-bottom witness
implications. The witness cover set is part of the grouping key. A deck
allocation of a witness-family box requires every validated witness cover to
contain hold cargo; if a cover has no hold allocation, that deck choice is
excluded. The frozen necessary minimum used-cover count is retained as well.
The existing restrictions on these witnesses remain unchanged: ordinary
non-DG/non-OOG cargo, no lateral OOG elsewhere, and no activated soft E6.

Both old proof arms and the compact model reuse one fresh in-process witness
computation for this exact instance and table object. The cache is local to
one API call, is never serialized, and is discarded before returning. Private
imported helper functions are restored in `finally`; the frozen source files
are unchanged. A test invokes the wrapper twice and establishes one witness
computation per call and restoration of both helpers, without reading data.

## Necessary base-tension inequality

Consider the checker's bottom joint of one deck end-chain. Write `M` for
the nonnegative transverse inertial plus wind overturning moment about its
root, `R` for the rod's horizontal force, `H` for its anchor height, and `G`
for the chain's per-corner gravity sum. The fitting constraint implies

`M - R*H - WIDTH*G <= (.85*250000)*WIDTH`.

This follows even when the dynamic moment changes sign: the checker uses its
absolute value and also adds the absolute eccentric-gravity moment, both of
which only strengthen its test. Its lashing rule also requires `R <=150000 N`.
The anchor is at the roof of the first two boxes, or of the single box when
the chain has only one. Thus the optimistic rod relief is at most150000 times
the sum of the first two box heights in that end-chain.

Sum this inequality over both end frames of every root. Every bottom box has
two root frames. Each first- or second-tier box contributes at most twice its
physical height to total anchor height. Every box's two raw longitudinal end
shares sum to one, while their absolute shares sum to at least one, even for
asymmetric OOG. Consequently gravity sums to `mass*g/2` per box and total
horizontal moment is bounded below using the whole box's minimum-motion force.
The new row, summed over a connected support component, is therefore

`sum(moment_floor[b] - mass[b]*g*WIDTH/2)`
` <= 2*(.85*250000)*WIDTH*bottom_box_count`
`    +300000*sum(physical_height[b] for b in tiers0,1)`.

Here a box's tier is the minimum physical-cell index of its footprint. This
also covers shifted long boxes. A native root must occupy only index-zero
cells: an absent predecessor at a higher index is rejected, and a long box
cannot have only one supported end. A second node in a root chain touches
that root's physical support column, so at least one of its cells has index
one. Thus every actual anchor box has minimum index below two. Granting rod
relief to *all* boxes whose minimum index is below two may grant extra relief
to other frames, which is optimistic and preserves the lower bound. Likewise
granting two root fitting allowances to every minimum-index-zero box can only
enlarge the true allowance when an irregular footprint is relaxed.

The component joins covers whenever any possible long-box footprint links
them. Every actual end-chain stays inside it. A box's root-relative lever is
bounded below by its minimum ordinary support height minus the largest deck
root-base height in the component, clipped at zero, plus its own actual
physical CG lever (`.45H` for inertia, `.5H` for wind). This handles unequal
support bases and shifted long chains without attributing force to an arbitrary
single cover. Minimum motion is the previously audited C4 outward envelope at
lashing GM tending to zero; stability GM and nominal constructor GM never
enter. Mass is the checker's `max(gross_mass, minimum_securing_mass)`.

All moments are computed with exact `Fraction` arithmetic on the supplied
binary values and rounded down to integer N*m. The force floor is guarded
downward. Allowed tension and rod terms are enlarged by1e-7 relative, safely
relaxing numerical comparisons. No positive securing margin is imposed.

The original frozen domain guards remain: empty closed arrival, complete
loading, no future loads/forecasts, nonnegative weights, ordinary one/two-cell
footprints, and immutable background support disjoint from decision stacks.
Background forces may be omitted because they can only consume additional
root capacity in disjoint components. This certificate concerns the fewest
weighted lifts among full-checker passing complete plans under D15's fixed
stow/no-later-load projection; a relaxation witness is not a passing plan.

## Verification and sources

Focused tests check outward integer rounding with mixed coefficient signs;
the inequality against two independent checker end-response solves with
unequal heights and OOG end-share asymmetry; and nonexclusion of normal columns
which satisfy the exact securing row generator. Parent additionally checks new
certificates against independently full-checked plans. Only
`BestObjectiveBound`, rounded downward, is reported, never a model incumbent.
Unexpected CP infeasibility raises an error and is not called a map proof.
The three focused tests passed in one serial process (exec session98027,
normal exit). One numerical thread was set; `nice -n 5` was attempted and
denied by the operating system. No development solve ran in the subagent.

This is an algebraic strengthening of the unchanged checker, building on
`c4/c2-layout/physics_bound_notes.md`. The cover-graph and port-capacity source
remains Hall (1935), *On Representatives of Subsets*,
https://doi.org/10.1112/jlms/s1-10.37.26. The distinction between the stack-order
circle graph and the cover fixed-charge graph is documented in BOUND.md, citing
Avriel, Penn and Shpirer (2000), https://doi.org/10.1016/S0166-218X(99)00245-0.
Neither paper supplies an unearned physical approximation guarantee.

Run with the same one-thread environment and root-assigned CPU token:

```
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c6/c6-lifts/bound_cover.py \
  --list experiments/mvp/frontier/lab/c6/c6-lifts/dev6.txt --budget-s 60 \
  --out experiments/mvp/frontier/lab/c6/c6-lifts/results/bounds-cover-dev6.json
```

No improvement is claimed until the parent records the cold paired certificate
and independently checked primal measurements. The sixty-second deadline
includes old-bound recomputation, setup and solve; deterministic setup cannot
be preempted, and an exhausted deadline returns the fresh old certificate.
