# Whole-map necessary physics bounds

Only this experiment directory is written. This module derives inequalities
from the unchanged checker; it never accepts a plan. The root executive owns
the one CPU-busy process while `c1/leader/BENCH-RUNNING` exists. No numerical
map job was started by this subagent. Read the full contract and lab README,
the requested strategy/C1/C2 reports, and the relevant checker providers.

The mathematical object is a fractional assignment of exact-mass container
classes to real bay/tier footprint signatures, coupled to a tank-mass vector.
Every legal assignment has such an image, but the image forgets individual
rows, support-height equality, DG, E6 and much of securing and hydrostatics.
Those omissions enlarge the feasible set. A feasible image is a target for a
constructor, never evidence of a legal stow.

## The two different GM values

`stability.check_stability` computes its GM at the zero-heel free-trim state,
with its stability liquid policy and virtual free-surface rise. The lashing
checker calls `visibility._loading_condition`, which iterates actual tilted
liquid centroids at loaded list and trim and uses another free-surface policy.
There is no established uniform discrepancy bound. In particular, imposing
the stability minimum 0.15 m on lashing GM would exclude unrefuted stows.

We therefore keep `GM_stability >= 0.15` (or the tighter instance minimum)
as a separate fact. Lashing requires `GM_lashing > 0` in `_motions` and also
rejects `GM_lashing > hull.max_gm_lashing_m`. Its outward branch domain is
the closed interval `[0, max_gm_lashing_m]`. A zero endpoint means the limit
from positive GM. Branches need not attach a fictitious GM equality to the
linear tank model. Their necessary securing inequalities can still refute a
branch, but cannot establish which GM an LP witness actually attains.

## Motion lower envelopes, with no draught shortcut

The checker's scantling block coefficient CB is independent of actual stow
and ballast. The same provider computes it once, with a pinned lashing source
SHA-256. Surge, sway, heave and pitch angle depend only on hull and CB.
The rotational centre is always at or below `depth/2`, for every positive
draught. Below that height rotational and gravity terms may cancel; the
safe bounds retain only `0.7*surge`, `sway`, and `heave`.

For a cargo CG lower bound `z >= depth/2`, the lever is nonnegative.
On a lashing-GM branch with lower endpoint g, the roll period satisfies

`T <= min(30, 2.3*pi*0.35*B/sqrt(G*g))`.

At g=0 use 30. The source's roll-angle formula decreases with T, and its
entire possible angle interval is proved to lie between 0 and 90 degrees.
Thus its angle and angular acceleration have lower endpoints at the period
upper bound. For radians x, `sin(x) >= x-x^3/6`; the implementation uses that
polynomial with exact rational arithmetic on binary source constants. The
positive lever, gravity component and angular acceleration then give a
transverse lower acceleration. Longitudinal acceleration retains its positive
gravity/pitch-angle component and permits pitch acceleration to approach zero.
No stability draft maximum is borrowed to limit the lashing pitch period.

Rational expressions and square-root enclosures round toward the required
side. A further `1-1e-10` force guard protects the translation to the source's
ordinary floating arithmetic. This is a source-algebra necessary envelope;
the complete checker itself remains a numerical polygon/equilibrium model,
not an interval-certified model of a physical ship.

## Necessary securing inequalities

The effective securing mass is `max(gross, 2500/3500)`; the source wind floor
and expanded OOG dimensions are retained. For a deck box, each end chain has
the nonnegative own transverse contribution

`abs(0.5 +/- dx/length)*(effective_mass*at + wind_t)`.

The source's longitudinal DAG recurrence sums nonnegative horizontal forces
and splits them by two at the ends. At either end the own longitudinal shear
per corner is therefore at least `(effective_mass*al+wind_l)/4`. The norm of
these two nonnegative own components gives a necessary fitting ratio; extra
boxes cannot reduce it. Hold fitting checks directly expose the own-box
`hypot(mass*at,mass*al)/4` load. Gross mass above the fixed 30,500 kg securing
rating is independently impossible.

We deliberately do not use an own-box compression test. Eccentric upper-box
reactions can become negative at an individual supporting end, and an own
box alone cannot establish the whole-graph positivity conditions used by
`c1/fx-finish/early_bound.py`.

At the bottom of every deck end chain, the source's fitting inequality gives
`sum(transverse inertial + transverse wind) <= 2*S`, where
`S=0.85*210000 N`. A physical twenty-foot column has at most two root end
chains. Therefore total necessary transverse force assigned to it is at most
`4*S = 714000 N`. Summing over the physical columns in each odd bay gives
the implemented bay inequality. A long box contributes one end to each of
its two physical columns. For asymmetric OOG, using
`2*min(abs(0.5-dx/L), abs(0.5+dx/L))/parts`
as its mass/wind coefficient is a lower share at either physical column.
The model takes minima over every member of a class that forgets horizontal
OOG asymmetry, preserving an image for every actual class member.

This aggregate bound permits chains to move between rows and overcredits
pure-long root capacity, so it is weaker than the exact securing check. It
does not enumerate or forbid page patterns.

## Mass, tanks, draught and weather KG

The total mass is exactly lightship plus FixedLoad aggregate masses plus load
list mass plus tank masses. Fixed cargo-box geometry is not added as another
mass. Every tank may independently take any mass between zero and its full
declared capacity; restrictions to actual consumables or operational pump
settings are relaxed. Each tank's vertical moment is at least its mass times
the declared solid's minimum z, valid for every tilted liquid centroid.
The source loading-manual displacement and signed trim bounds are retained.

The active weather-table applicability condition explicitly requires
`B/d < 3.5` and `-0.3 <= KG_virtual/d - 1 <= 0.5`. Since virtual KG includes
nonnegative FSC, every passing state satisfies

`KG_physical <= KG_virtual <= 1.5*d <= 1.5*min(hull_max_draft, port_max_draft)`.

This provides a whole-map linear necessary moment inequality when the cargo
moment and tank moments are replaced by their lower envelopes. Stability's
ordinary-height cargo provider is used for cargo KG; lashing's separate
expanded-height 0.45H CG is used only for securing coefficients. This
distinction matters for OOG. Closed draft lower endpoint `B/3.5` weakens the
strict source inequality and is outward-safe.

## Second-check geometric target

The source form factor simplifies exactly to

`C = Dprime*V^2/(bm^2*Awp^2*d) * sqrt(d/max(KG_virtual,d))*sqrt(100/L)`.

For any legal stability mean-draft/trim interval, each quadrature station's
waterline height is enclosed by its endpoint formula. Between polygon vertex
heights the waterline segment lengths are affine. Exact Fraction arithmetic
evaluates every limiting endpoint, providing a waterplane-area lower bound.
The ignored trim Jacobian is at least one. A bare-section width lower bound
is also a lower bound on the maximum breadth `bm` at half draught. Rational
clipping at the station's waterline-height upper bound gives an upright
volume upper bound. Coaming `Dprime` is independently recomputed by exact
rational union arithmetic. The KG square-root factor is at most one.

Together these quantities give `C <= C_upper` and necessary targets such as
`area_0_40 >= 0.016/C_upper`. These are targets only. The implementation has
no tight certified finite-angle free-trim/liquid-correction GZ upper envelope.
It cannot refute a map merely because a sampled hydrostatic curve misses a
target, and a tangent or reference-KM fit is never treated as outward-safe.
Visibility roof cuts are likewise omitted without a proof covering the full
trim/list/draught transform.

`righting_area_universal_upper` does provide a deliberately loose all-pose
upper envelope. The raw buoyancy-minus-CG projection onto any unit heeling
axis is no larger than the diagonal between the polygon buoyancy-coordinate
enclosure and the positive mass-point enclosure. Each liquid-transfer
projection has absolute value at most tank diagonal times liquid mass; using
the full bounding prism also encloses every consumable correction volume.
Divide the summed transfer moment by the minimum possible total mass and
add it to the raw lever upper bound. Integrate that constant over at most
pi/2 for total area, or 40 degrees for the 0–40 area. The checker's worst-side
TCG subtraction only lowers the curve. This bound covers arbitrary finite
angle trim and list, but is far too wide to represent a useful KN curve.

## Sources and reuse

The motion and positive-chain principles are adapted from the supplied
`c1/fx-finish/early_bound.py` argument and its REVIEW, and all constants are
from the unchanged checker. No checker or engine code was copied or patched.
Rational polygon clipping, width envelopes and coaming union calculations
were written for this experiment.

- R. E. Moore (1966), *Interval Analysis*, Prentice-Hall. The enclosure
  principle is used; [SIAM's author bibliography confirms the 1966 book](https://epubs.siam.org/doi/10.1137/1.9780898717716).
- G. P. McCormick (1976), *Computability of global solutions to factorable
  nonconvex programs: Part I—Convex underestimating problems*. The supplied
  order motivates spatial interval partitioning; no McCormick bilinear row
  is presently needed because this experiment retains coarser scalar outer
  bounds. [Original paper excerpt reproduced in academic course material](https://www.lamsade.dauphine.fr/poc/sites/default/files/minlp-compact-course.pdf).

Verification and map outcomes belong to the root executive's independent
runner/audit. Until those execute, this file makes no timing, pass, or
whole-map infeasibility claim.
