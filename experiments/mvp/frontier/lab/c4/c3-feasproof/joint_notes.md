# C4 joint cargo/tank voyage windows

Read the full CONTRACT, including Cycle 4 corrections, C3 feasibility notes,
the C3 Benders pass/physical bindings, and the native loading/voyage source.
Only `joint_model.py` and this note are written by this delegated task. The
initial packing model was copied from C3 `c3-feasproof/packing.py`; no C3 or
native file is edited. No mutable engine or sealed data is imported. Numerical
work waits for the executive's shared compute token.

The important new evidence is that D185/185100000 **is legal**: C3's warm
114-lift plan passed the unchanged checker. Its departure stability GM is
1.594277 m, securing GM 1.410294 m; arrival values are 1.305045 and 1.192439.
Thus a shared GM variable would contradict the actual providers. The tight
margin is arrival total area (.291164308 versus .291006173 m rad), not GM.
This model keeps these providers distinct. Existing passing controls are
preselected passable development maps, not unseen data or a lift benchmark.

## The outward object

The fractional transportation graph sends exact-mass cargo classes to real
bay/deck/tier footprint signatures. It retains the copied necessary physical
cell, aggregate prefix, summed height/mass and unary physical placement rows.
Long boxes occupy two physical cells; their mass is split equally. A legal
stow has an image in this graph, but a fractional image need not be a stow.

Each allocation now has both a lower and an upper stability cargo-CG bound.
The lower bound is the copied base plus tier-ordinal times smallest ordinary
box height, with a downward guard. For the upper bound, the ordinary support
height cannot exceed the stack's physical envelope minus this box's physical
height. Add half its ordinary height, exactly matching stability's loading
provider. The average upper envelope over each slot's supporting cells is
valid even with the checker's unequal-support tolerance. Maximize over the
whole raw slot signature; ignoring unary eligibility in this upper-domain
union only makes the bound weaker. Cargo LCG is enclosed by the raw slot
cell-LCG averages. Directed conversion of rational products preserves the
endpoint direction.

One pair of cargo X/Z moment variables is shared across all voyage phases.
It lies between the allocation-weighted lower and upper moment coefficients.
The model therefore cannot choose a different cargo KG for departure and
arrival merely because different ballast is convenient.

## Shared tanks and exact D57 semantics

Unlike C3's whole-map model, **fixed tanks retain their exact onboard arrival
masses**. Native `full._ballast` permits the plan to set adjustable tanks only.
Every adjustable tank gets one shared departure mass variable between zero
and capacity. The default D57 consumable factors are exactly 1, .55 and .1;
ballast/nonconsumable masses do not change. This follows `full.py`'s three
sample identities and `model/voyage.py`'s proportional derivation. Midpoint
keeps departure environment/density, arrival uses the declared arrival density,
and only departure keeps current-port draft restrictions.

Explicit kg-consumption declarations make the common remaining fraction
depend on departure group totals. This entrypoint rejects that unsupported
bilinear case and requires an **unknown** report; it never substitutes .1.
Missing voyage evidence, forbidden ballast-at-sea declarations, nonempty
arrival/future-load scope, missing tank bounding geometry and invalid masses
likewise return errors for the executive to label unknown.

For each phase, tank X/Z moments lie between phase-scaled mass times the solid
tank bounding-box endpoints. Those bounds cover all upright or tilted liquid
centroids; they are not half-fill tangents. Shared cargo moment plus fixed
moment plus tank moment gives the phase's physical moment. Total phase mass
uses the same shared departure tank variables and exact D57 factors. A fixed
KG interval gives linear inequalities `KGlo*M <= Z <= KGhi*M`. The corresponding
LCG rows are analogous. Native loading-manual mass limits and weather's
`KGphysical <= KGvirtual <= 1.5*mean_draft` are necessary outer bounds.

Draft and trim windows remain provider-specific domain metadata for the area
envelope; no invented linear equilibrium identity couples them to cargo.
Finite-angle free trim cannot be limited to the upright trim interval. Optional
area-derived physical-KG caps are accepted only with an explicit outward
certification flag. Necessary securing rows use the lashing provider's motion
floor and its own GM lower endpoint. The global force sum deliberately avoids
unsafe per-bay end-chain assumptions when long boxes shift between odd bays.
OOG-varying classes take the smallest member coefficient.

## Strict scalar windows versus numerical guidance

`analytic_windows` computes classwise cargo-moment extrema before any LP solve.
For each phase, its KG/LCG ratio is enclosed over independent tank mass boxes.
For a fixed centroid endpoint, the fractional-linear extreme occurs at a
prefix of optional tank masses sorted by centroid: differentiating with
respect to one mass has the sign of `(tank centroid - current ratio)`.
Evaluating every prefix in exact rational arithmetic gives a certified scalar
enclosure of this deliberately larger box. Lower/upper centroid endpoints
give the two outward ratios. The calculation includes nonzero lower masses
of fixed tanks. It does not rely on a floating LP optimum or a dual bound.
It also exports a deck-mass lower bound from hold-cell deficit by charging
the cheapest optional deck mass per physical cell after mandatory deck cargo.

`export_windows` writes the executive's schema: phase physical KG/mass/LCG,
mean-draught/upright-trim intervals, shared tank bounds and cargo deck ranges.
Area proof flags default false; numerical LP infeasibility is never itself
a physical proof. `guidance_targets` is a separate field. A feasible joint
LP's phase KG values are **outer moment witnesses**, not achieved native KG.
This distinction persists even when the LP happens to be integral in boxes.

`project_plan` projects a replay-legal exact plan into every row and variable
bound. Cargo support moments are recomputed from the actual loaded boxes.
Native upright tank sounding supplies phase physical moments, or freshly
checked native phase mass/KG/LCG metrics can be supplied directly. This is a
validity check for the model; it does not replace the unchanged full checker.

The copied numerical packing rows remain an outward algebraic derivation
evaluated in ordinary doubles. A future infeasibility result additionally
requires directed coefficient/error treatment and an independently checked
contradiction. No numerical infeasibility certificate is claimed here.

Mathematical background is the inherited interval/relaxation approach of
R. E. Moore (1966), *Interval Analysis*, and G. P. McCormick (1976),
[Computability of global solutions to factorable nonconvex programs](https://doi.org/10.1007/BF01580665).
The sorted-prefix ratio argument above is derived directly for this model;
no unverified paper theorem or downloaded code is used.

Entry points: `build_joint_model`, `export_windows`, `project_plan`. The
executive owns all measured outputs, area-provider integration, target search,
unchanged full-checker passes, source hashes and final cohort reporting.

## Bounded-variable support certificates

The executive requested stronger global cargo-moment windows than the loose
classwise extrema. `certified_cargo_bounds` runs four short one-thread LPs:
minimum/maximum cargo Z and X. Only class-count, physical-cell, prefix and
optimistic aggregate mass/height rows survive. The latter capacities have
explicit upward numerical guards. Tank, GM, area, page-pool and local fitted
rows never enter this support calculation. All allocation variables have
finite bounds from zero to their class count; securing exclusions are reset.

For a minimization objective c*x, take any numerical LP row multipliers y
(nonpositive for <= rows, unrestricted for equality rows). Exact arithmetic
gives the universally valid lower bound

`y*b + sum_j min((c-y*A)[j]*lower[j], (c-y*A)[j]*upper[j])`.

The second term is the exact minimum of the leftover linear expression over
the bounded variable box. This handles **nonzero** dual residuals rather than
pretending floating multipliers satisfy y*A=c. Multipliers can be arbitrary;
only their inequality sign matters. `verify_support_certificate` recomputes
this formula as Fractions over every decoded binary coefficient and returns
the exact rational numerator/denominator and a directed floating bound. An
upper cargo bound is the negative of a lower bound on the negated objective.
The shared serialized matrix and all four certificates are retained for an
independent verifier. A timeout supplies no new bound.

`apply_cargo_bound_windows` replaces classwise moment extrema only when the
new support bounds strengthen them, recomputes the exact fractional-linear
tank ratio enclosures, and attaches those phase KG/LCG rows. Algebraic support
verification is exact; eligibility for a **physical** contradiction still
requires independent review of the matrix's outward source/roundoff mapping.
The result explicitly retains `physical_proof=False` and never promotes a
numerical LP infeasibility to physical infeasibility.

Initial smoke measurement, before these optional four LPs were added: the
known 88-box control and accepted D185/185100000 plan both remain feasible
in the new three-phase model. The 88-box model has 2,403 variables/212 rows,
build .988 s and solve .044 s; D185 has 2,870/229, build .401 s and solve
.054 s. Exact plan projection took .254/.675 s, with maximum row residual
8.9e-16/7.1e-14. All three scalar KG intervals contain the independently
checked control values. These are model validity checks, not new passes.
The compute token was returned immediately afterward. The executive owns
testing the subsequently added support-certificate functions.
