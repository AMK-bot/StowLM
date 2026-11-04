# Proof scope audit: Cycle 2 hatch bounds

Read-only logic audit on 1 November 2026. No validation inputs, benchmark
results, tests, or solvers were opened/run for this audit. The complete Cycle 3
contract and the Cycle 2 bound implementations and proof notes were read.
No implementation was copied or modified. References below describe the source
currently in the workspace; old numerical certificates retain their own source
digests and activation mode.

## The intended family is broader than load-once search

`bound.py` describes a relaxation of checker-passing, complete, current-call
MovePlans with empty decision-cargo arrival, no discharge list, no jointly
planned later calls, and no forecasts. Its packing variables describe departure
positions, not intermediate placements. It discards current ship-restow costs
and most load-order restrictions. Therefore current staging, direct ship shifts,
and quay off/on restows do not by themselves invalidate its packing bound.

`bound_joint.py` additionally prices necessary current moves using first-aboard
ranks. Its rank argument also permits arbitrary current restows:

- An original yard export above another export must either first board earlier
  or have undergone a yard relocation. Loading the upper export temporarily
  aboard satisfies the earlier-first-aboard alternative.
- If a final upper/deck export has never undergone a ship restow, its first
  placement is its final placement. Final lower/hold exports must therefore
  first board before it; otherwise physical top access or the covered hold
  would be blocked. A ship-restow flag charges only one lift per box, even if
  actual off/on operations cost two or the box is moved repeatedly.
- Export yard relocations, ship-restow lifts, and the existing non-export-yard
  relocation bound are disjoint move categories. Adding these lower bounds
  does not double-count a physical move.

The first-aboard implications are necessary, rather than a characterization of
executable sequences. They do not prove that a relaxation witness has an order.
By contrast, `repair.py`'s conservative ChainPacking candidate model and local
search are restricted candidate families, not unrestricted lower bounds.

“Empty arrival” here means **empty decision-cargo placements**, not an empty
physical vessel. Non-decision fixed background cargo may be extensive and is
preserved unchanged. Do not introduce an empty-background requirement: it is
unnecessary and would misdescribe the supported family.

The allowed decision positions are the declared `vessel.cells` and
`vessel.stacks` catalogs. The vessel validator rejects intersecting fixed and
decision cell/stack identifiers (`model/vessel.py`, near line 978). The crane
replay's decision destination catalog is likewise built from `vessel.cells`
and `vessel.stacks`; its separate fixed-occupancy/crossing view does not make
fixed cells load destinations. Departure `_core` validates decision placements
against `_geometry_index` of the decision catalogs. The broader
`_physical_geometry_index` documents explicitly that its complete-stow view
“never makes fixed cells available as decision positions.”

Thus a suitable guard is validated disjoint decision/fixed catalogs, immutable
fixed background, and complete empty **decision** arrival, with all required
decision exports loaded. Retaining that declared-catalog domain is not an
extra load-once or pure-page restriction. The securing oracle may still inspect
the entire combined physical graph, including all fixed background boxes;
their presence does not invalidate a local component certificate. If future
schema/replay semantics permit shared or newly selectable fixed support cells,
the packing and local-cut mapping must be reviewed again rather than silently
claiming that enlarged domain.

## E6 is a final-state rule in the current checker

The proposed staging counterexample does not bypass the current rule:
`checker/full.py:_check` runs `check_static_rules` on replay's departure state;
`checker/rules.py:check_static_rules` calls `e6_departure_violations`.
`checker/rules_weight.py:e6_departure_violations` rejects a legacy heavy deck
box whenever that departure has a compatible empty hold opportunity.
`placement_violations` explicitly defers E6 while building a partial stow.
The move engine documentation likewise assigns weight/E6 stowage rules to the
departure checker, rather than each physical crane move.

Temporarily filling a hold, loading heavy deck cargo, and then emptying that
hold consequently fails departure E6 if the resulting opportunity is compatible.
Under rule-freeze-v1 E6 is soft; both bound implementations correctly disable
these legacy E6 cuts there.

The existing E6 cut is deliberately narrower than “heavy means hold.” It uses
an ordinary heavy candidate without DG, OOG, or booking requests; checks a
compatible bottom footprint on the empty decision ship; and disables these cuts
if any cargo has lateral/longitudinal OOG. If every bottom cell in that
footprint is empty at departure, prefix support empties its entire support
column(s). No added cargo in those columns can have consumed its stack capacity,
changed its supports, or introduced an affected-stack booking condition. The
ordinary candidate has no heat request; another cargo's away-from-heat request
does not turn this ordinary candidate into a heat source. Vessel heat geometry
is fixed. DG pair restrictions do not apply to a non-DG candidate. Lateral OOG,
which could block an empty neighbouring footprint, is explicitly excluded.
These facts support the existing bottom-cell implication for arbitrary current
plans, not just monotone load-once plans.

Do not extend this reasoning to every empty upper hold slot, or to an E6
candidate with heat/DG/OOG requests. Compatibility there can depend on other
placements and their actual heights. Empty-footprint counting alone is unsafe;
`e6_completion_possible` documents this explicitly.

## The objective assumption is D15

The certificate minimizes the checker's D11 weighted count, whose future part
is D15's abstract departure-coordinate projection: no future loads, first
subsequent matching POD call, surviving boxes retaining their coordinates, and
the configured off/on lifts per blocking box. Physical-column closure joins
both supports of a long box and charges a blocker once per call. This matches
the legacy projection; additional activated crossing requirements are relaxed.

It does not minimize over arbitrary executable future plans. Surviving boxes
can lose physical support after discharge under D15's coordinate convention;
neither the projection nor these bounds constructs a future repositioning or
ballast policy. A permanent future shift could have a different objective in a
different operational model. “Globally D11 optimal under the specified D15
projection” is the appropriate claim; “minimum real voyage crane lifts” is not.

## General-purpose static-mask caveat

A proof needs `T.static` to contain every departure placement allowed by the
checker, rather than merely every placement admitted by a particular search
mask. Ordinary footprint, reefer-plug, booking, mass validity, envelope, and
current reach restrictions are final-state necessary conditions. Dropping DG
pairs, heat clearance, OOG conflicts, securing loads, and physics is a valid
relaxation; it does not restrict the feasible family.

However, the current *legacy* DG branch in `feasibility/tables.py:build_tables`
calls `imdg_placement_violations` with an otherwise empty ship. In the presence
of SW1/SW2 placement geometry, `rules_imdg.py:_placement_envelope` returns
unknown for an unsupported upper slot. `_stowage` can then emit a hard missing
assessment because a ship argument was supplied. The same box/slot could have
known, admissible geometry once actual lower supports are included. Therefore
using this legacy table without qualification is not a demonstrated necessary
screen for all possible DG-geometry inputs. The activated branch already uses
an optimistic vertical-domain screen, which addresses this particular issue.

This is a structural generalization caveat, not evidence that any particular
one of the seven Cycle 2 map equalities is false. This audit deliberately did
not inspect benchmark inputs to establish which map guards hold. Re-certifying
the seven needs their recorded source/rule mode and a guard audit, or a fresh
bound using a demonstrably optimistic static domain. Calling the result merely
“load-once optimal” would not repair this static-domain issue: even a load-once
plan can support an upper DG box before placing it.

## Safe oracle cuts and recommended labels

An exact full-departure-assignment no-good is valid when an authoritative
ballast-independent final-state checker reports a hard cargo-rule failure for
that complete assignment. Preserve container identities, slots, actual support
heights, rule activation, and fixed background context. A complete-assignment
cut can be weak, but different current orders or ballast cannot change that
same final cargo-rule verdict. A numerical checker error, timeout, replay
failure, or failed particular order is not such a certificate.

Smaller pair/local cuts require their own necessity proof. A heat clearance
failure for an upper box can change when support heights change, so retaining
only its box-slot literal can be unsound. E6 is especially nonmonotone: adding
cargo can fill an opportunity or make it incompatible. Retain all relevant
support and absence/occupancy literals, or use a full assignment no-good.

Record certificate scope explicitly: current family (`arbitrary current
MovePlan` or a named restricted family), arrival/forecast guards, objective
(`D11_with_D15_projection`), activation mode, table/checker source hashes,
static-domain eligibility guards, and full-checker upper-bound evidence.
Equality to a valid relaxation bound is a full-family optimum only after these
guards hold. It does not follow merely from a CP-SAT OPTIMAL status.

Recommended future tests, not run here: a staged hold/deck plan rejected by
final E6; a physical empty-bottom E6 witness with unrelated heat-request cargo;
an upper-tier SW1/SW2 box whose supported geometry passes despite an empty-ship
unknown; and tiny exhaustive arbitrary-current-restow MovePlans compared with
both bounds. Include direct shifts and off/on staging, both activation modes,
repeated POD visits, zero/fractional weights, and nonstandard support heights.

## Lifting the reviewed securing rod contradiction to a column context

The supplied `results/early-securing25.json` records a reviewed
`max_lashing_ratio` lower bound of about 1.222403 on slots
0020682/0020684/0020686/0020688. This is a fixed-stow, all-ballast rod-force
contradiction, not an ordinary failed ballast search. The oracle records its
positive chain-compliance coefficients, actual heights and CG fractions,
minimum forces, fixed winds, original and independently recovered rod force,
residual guards, gear limits, and pinned lashing source hash.

It can support a smaller cargo-context no-good under the following guards:

1. Fix all box identities and exact slots in every physical column touched by
   the witness; close the column set through every spanning box until no box
   connects the fixed columns to an unfixed support column. Include all tiers,
   not only the four slots printed in the witness.
2. Require every remaining cell of those columns to stay empty. Merely fixing
   the four boxes would allow a different chain above or below them. Such an
   extension has not been certified by the existing fixed-chain calculation.
3. Hold the vessel, fixed background cargo, gear model and hull geometry fixed.
   The exact component fixes the support parents, actual floors, CG/height
   fractions, effective masses, expanded wind areas and end shares. It thereby
   fixes the grounded end-chain and its rod stiffness/inclination.
4. Consume only a reviewed rod witness: the oracle must supply its proof,
   successful guarded calculation, finite ratio above `1+1e-8`, nonempty chain
   detail, matching pinned lashing source, and no active frozen overrides.
   Preserve the independent compliance/residual checks and error-as-unknown
   behavior. Record the oracle/helper/hull source provenance as well.

The locality justification is specific to this synthetic checker. Its lashing
module explicitly credits no adjacent-stack wind shelter and omits transverse
side-wall coupling. `_chains` follows only physical supporting end frames;
the rod solve is local to a grounded chain. The oracle's `_MinimumMotions`
uses hull-only acceleration floors uniform over the positive-GM/draught
conditions in which the securing checker can pass. Moving arbitrary cargo on
disjoint columns may change actual equilibrium and GM, but cannot reduce those
floors. A nonpositive/invalid loading condition would fail independently.
Consequently every alternative preserving the complete component still has
the same rod lower bound, regardless of its other pages or ballast.

This is the same locality principle used by Cycle 2
`c2-benders/benders.py:_cut`: whole pure-page patterns occupy complete disjoint
physical support stacks. A full-assignment CP model can express the required
context without limiting every other page to be pure. The resulting local cut
can therefore be globally valid even though that earlier generated-page
master was restricted. Mixed/spanning geometry must be handled by the explicit
column closure, not assumed away.

Do not lift an arbitrary full-checker securing violation, a local ballast LP
failure, GM threshold, corner ratio, or an unguarded floating solve using this
argument. Other metrics can depend on larger load paths or require different
monotonicity guards. Do not infer that adding/removing boxes outside the
preserved component leaves the actual full-checker ratio unchanged: only the
certified local lower bound is invariant. These numerical safeguards are not
interval-arithmetic formal verification; that limitation remains explicit.

Additional future regression checks, not run in this audit: vary disjoint page
contents and ballast while preserving a certified component; verify the same
local bound; then deliberately change a fixed empty cell or spanning support
and ensure the cut no longer excludes that assignment. Test unknown/source
mismatch/override cases produce no cut.

## Integer improvement-cutoff certificates

For a finite integer target U and an actual D11 objective that is nonnegative
and integer-valued, add the complete scaled relaxation objective, including
its constant, `<= scale*(U-1)`. Downward coefficient rounding is compatible
with this test: every actual plan of cost at most U-1 maps to a relaxation
point of no greater cost. A proven CP-SAT INFEASIBLE result for this cutoff
therefore establishes an actual lower bound of U. It does not establish that
the unrestricted master or the physical map is infeasible. Label the result
`cutoff_infeasible`, with the tested target, rather than whole-map infeasible.

Feasibility of the supplied historical target U is not needed for that lower
inference. Calling U an optimum additionally needs a fresh checker-passing plan
at U. UNKNOWN, timeout, MODEL_INVALID, missing oracle evidence, or an invalid
earlier cut provides no cutoff certificate. Non-export mandatory costs retained
outside the CP objective may be omitted from the cutoff safely but weakly;
subtracting them from its right side requires the same established disjoint
lower-bound accounting used by the returned total.

The generic API must validate finite nonnegative integer lift weights and a
finite integer U, without silently truncating them. `ObjectiveWeights` currently
declares its lift weights as floats without nonnegative/finite field bounds,
so schema validation alone does not supply this premise. The configured
future-restow lifts per box must also be integral. A mathematically justified
alternative objective lattice could relax these guards, but is not implicit.
Use exact scaled integer arithmetic and preserve solver overflow/error checks.

The improvement family still includes arbitrary current staging/restows.
In particular a single direct `restow_shift` costs one current restow lift;
the joint model's per-box flag correctly charges one, not an assumed off/on
pair of two. An eight-hatch-plus-one-shift plan must remain represented when
testing a threshold of nine. No load-once-only conclusion should be attached
to that cutoff unless an explicit additional restriction was introduced.

## OOG-aware ordinary E6 bottom-opportunity implication

Reviewed `strengthening.py:add_e6_bottom_oog` against the current legacy
`compatible_empty_hold_slots` and `placement_violations` implementations.
Its implication is algebraically a necessary condition under the declared
decision-catalog/background guards:

`ordinary heavy box on deck => occupied bottom witness cell OR a selected
lateral-OOG placement whose conservative envelope domain may block the witness`.

If all candidate bottom footprint cells are empty, prefix occupancy empties
their entire physical columns. For an ordinary non-DG, non-OOG candidate with
no booking request, the empty-ship placement probe then establishes the local
hold compatibility except for remote lateral-OOG envelope interference.
Unrelated DG does not impose segregation on this non-DG candidate; local
weight/support/booking interactions have no other occupants. The vessel and
immutable background context do not change. The cut credits possible OOG
blockers rather than asserting that every credited blocker actually blocks,
so overly wide geometric domains weaken the relaxation safely.

The E6 query hypothetically removes this candidate from its old deck position.
For an OOG placement at physical ordinal k, at most one nominal lower box per
deck support column disappears. Each hypothetical floor is therefore at least
`base + max(0,k-1)*hmin` on deck, or `base + k*hmin` in a hold; the code uses
the maximum of these per-support lower bounds for a long box. Crediting a
possible removal in every deck support, even when b was elsewhere, only lowers
the bound further. The maximum floor is at most `max(base+k*hmax)`. Here hmin
and hmax are extrema of **nominal** box heights: this matches
`rules_special.special_violations`, which accumulates nominal lower-box heights
for its envelope floors. The OOG owner's own overheight is included in its top.
Thus the interval also contains the envelope used after hypothetical removal,
including the case where a long OOG box has unequal remaining support floors
and the special checker takes their maximum.

The horizontal footprint uses the same ISO dimensions, asymmetric overhangs,
mean physical-cell centres, interior-overlap test and resolved clearance as
legacy E9. The vertical test is a superset of strict actual overlap, expanded
by the stated epsilon. Every OOG placement that actually invalidates this
ordinary bottom opportunity is therefore credited on the right-hand side.
The code correctly declines the cut when the clearance policy is unknown.
Current numerical epsilon guards are not directed interval arithmetic; retain
that numerical qualification and do not extrapolate them to unbounded numeric
coordinate magnitudes without an error bound.

The rule-freeze early return is essential: activated E9 also reserves killed
neighbour cells, which is not the legacy envelope-only argument above.
Likewise do not generalize to upper empty opportunities, candidates with
DG/OOG/booking requests, mutable/shared fixed support catalogs, or narrower
floor intervals without a new derivation. This review ran no solver or test.
