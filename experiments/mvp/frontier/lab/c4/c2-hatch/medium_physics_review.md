# Read/code-only review: global departure physics cuts

No numerical job or validation-result read was performed for this review.
`lab/c3/c3-feasproof/packing_notes.md`, `physics_notes.md`, `physics.py`,
`audit_review.md` and the current stability/loading/checker sources were read.

## A valid row worth testing

The weather-table applicability flag in `stability_criteria.py` is mandatory:
its policy has no scalar override. It requires `KGvirtual/d <= 1.5`.
`stability_loading.loading_condition` uses nonnegative free-surface correction,
so physical upright `KG <= KGvirtual`. Both end draughts obey the independent
hull and port upper limits, within the checker's tiny numerical acceptance.
Thus `KGphysical <= 1.5 * maximum_mean_draught` is necessary. This is the C3
feasibility experiment's argument; it does not identify securing's different GM
or cargo-CG provider with stability's.

For each box/slot assignment variable, use a **lower** stability cargo moment:
mass times (average physical support-stack base + ordinal times minimum ordinary
decision-box height + half own ordinary height). Every legal supported empty
decision-arrival stow has at least that many supporting boxes; ordinary heights
are used here because stability deliberately omits OOG expansion in cargo CG.
Widen for floating evaluation and round all integer CP coefficients downward.

Tank variables can be eliminated without guessing a ballast vector. Let K be
the necessary upper physical KG, M0 lightship+fixed loads+all load boxes, and Z0
the lightship+fixed-load vertical moment. Tank i has capacity Ci and a valid
geometric lower centroid zi (raw solid's minimum z, never a local sounding).
Every legal ballast state obeys

`cargo_vertical_moment <= K*M0 - Z0 + sum_i (K-zi)*mi`.

Relax every tank to `0 <= mi <= Ci` (including consumables), and retain only
the maximum-displacement upper bound `sum mi <= Dmax-M0`. Maximizing that linear
right side is fractional knapsack: discard negative benefits, sort benefits
`K-zi` descending and fill until the mass allowance ends. Its exact rational
maximum yields one global assignment row. Ignoring minimum displacement,
operational pump limits, symmetry and arrival tank restrictions enlarges the
admissible set. The original C3 feasibility model instead retained separate
continuous tank variables; this eliminated form is compatible with CP-SAT.

Before running, source-pinned guards should cover the weather criterion,
loading-condition CG arithmetic, draft/displacement policy and tank geometry.
Use outward capacity/displacement tolerances and independently project all three
known passing TRAIN plans into the row. Full-checker admission remains separate.
The numeric proposal is a source-derived necessary inequality; it is not itself
a claim of tighter measured bounds, nor a formal proof of the complete hull
numerics. A source mismatch must omit the optional cut, not change the checker.

## Why no purported global LCG/TCG/trim rows

The production `stowage.linear` model and ballast successive-LP rows have local
validity bands and empirical linearization error, explicitly not uniform bounds
over all fills and stows. Cargo moment targets used by pages are also heuristic.
They cannot become global proof cuts by reusing their fitted coefficients.
Actual equilibrium includes trim/list, tilted liquid transfer, changing hull
waterplane and background mass. An upright hull tangent or fixed ballast centroid
has no established outward direction over that whole domain. C3 feasibility
explicitly omitted such rows for the same reason. Deriving tight all-pose hull
and liquid moment envelopes is a separate substantive task; a CP model's
INFEASIBLE status would not make those guessed rows sound.

The K row may still be weak on the low-mass 88-box map: fixed background and
independent low tank-centroid envelopes supply considerable moment room. That is
a qualitative expectation only; no numeric strength has yet been measured.
The unchanged joint120 runner remains the primary next numerical experiment.

Compact code is now prepared, still unrun: `medium_physics.py` implements exact
rational eliminated-ballast benefit and an integer necessary row; its source
snapshot is `medium-physics-sources.json`. `physics_bound.py` builds the unchanged
joint model and adds only that row, without changing `certified.lower_bound` or
any of its eight dependencies. It omits the lazy final-state cuts, so it remains
an optimistic lower bound even if weaker than the primary exact-order runner.
One metre-microguard is applied to cargo/tank coordinates and the draught upper
envelope; one kilogram widens tank capacities/displacement, and one kg m widens
the RHS before outward integer rounding. Integer overflow is checked. Missing
or changed evidence skips the row rather than restricting the base model.

`medium_physics_tests.py` queues independent exhaustive integer tank fillings,
a fractional optimum/negative-benefit example, and projection of all three
copied checker-passing TRAIN plans. These tests must run before measured use;
they are evidence against implementation mistakes, not a proof of every hull
numeric. Root's source/scope review is pending before any numerical run.

All resulting equality claims must retain the scope: **fewest lifts among plans
the checker passes, counting later ports by our D15 projection (fixed stow, no
later loads)**. The optional final-state row does not restrict current moves to
load-once plans.

After independent review, guard scope is narrowed: the hull-offset stem must
equal `vessel.slice_of or vessel.id` and be exactly `feeder_500`. The canonical
parameter bytes are pinned separately, and tank records now come from
authoritative `stability._context`. Source pins additionally cover hull
sections/tanks, hydrostatics, soundings, free-surface/liquid helpers, stability
geometry and fleet loading.

Fixed margins are qualified to a checked finite numeric domain: at most 1,000
summed load/tank terms; total modeled mass at most 1e9kg; individual masses at
most 1e8kg; absolute vertical coordinates at most 1,000m; ordinary heights at
most 10m; support ordinals at most 100. Outside that predeclared domain the
optional row is omitted. This is a source-pinned canonical-fleet numerical
qualification, not an interval-certified all-fill sounding/hull-roundoff proof.
Exact rational arithmetic covers the row projection/elimination itself.

The independent reviewer conditionally approved this guarded numerical
experiment before execution, subject to synthetic/projection tests and retaining
the qualification above. See `reviewer_NOTES.md` for the source audit.
