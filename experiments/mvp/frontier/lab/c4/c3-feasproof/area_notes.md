# Finite-angle area enclosure

Scope: only `area_bound.py` and this note are written by this subtask. The
full contract through Cycle 4, lab README, C3 feasibility NOTES, C2/C3 Benders
NOTES, accepted-witness BINDINGS, and native stability/curve/loading/hull
providers were read. The benchmark marker existed at admission. The root
executive owns the numerical token; this subagent has run no CPU-busy job.
The first D185 pass is a positive control, not a target to disprove. Its
arrival total positive GZ area is .291164308 against .291006173 m rad.

The object is a branch-and-bound cover of all finite-angle free-trim poses,
with exact polygon-integral interval bounds in every pose box. A pose is
retained until its mass or longitudinal-balance residual is refuted. The
bounding target is the actual `containership_area_0_flood_mrad`, not GM,
area-to-30, an empirical stability fit or a local derivative.

## Interface

`AreaOracle(instance, density_kg_m3=None, geometry=None, params=None)` prepares
the native polygon/quadrature inputs and tank solids' coordinate enclosures.
It does not run a loading equilibrium or full checker.

- `direct_integral_bounds(heel_rad, alpha_rad, offset_m)` returns intervals
  for volume and first moments `mx,my,mz`, plus its halfspace coefficients.
- `contract_offset(..., mass_kg=(lo,hi), steps=4, integral_provider=None)`
  discards whole offset half intervals using the mass-residual condition.
- `classify_pose(..., mass_kg, kg_m, lcg_m, tank_masses_kg=None, ...)`
  refutes a pose or returns a buoyancy/CG/GZ upper enclosure.
- `build_area_envelope(instance, mass_kg=..., kg_m=..., lcg_m=...,
  tank_masses_kg=..., ...)` covers the whole free-trim domain and sums
  conservative positive upper GZ work. It reports every work limit,
  unresolved pose count and governing pose.

KG and LCG are native stability **physical upright** loading-condition
coordinates, not virtual KG and not the securing provider's CG. Mass,
density and tank intervals are phase-specific. The root supplies the D57
departure/midpoint/arrival transformations; this module does not fabricate a
shared GM or approximate consumable rule. Optional upright draft and trim
windows are recorded, never silently imposed on finite-angle free trim.

## Every finite free-trim pose is covered

Let `phi` be heel and `alpha=atan((tf-ta)/L)` the finite-angle longitudinal
waterline slope angle. The checker permits all finite slopes; its default
upright loading-manual trim bound is not a bound on free-trim cross curves.
The closed interval `alpha in [-pi/2,pi/2]` outwardly covers those slopes,
including their limiting directions. The unnormalised waterplane normal is

`m=(-sin(alpha) cos(phi), -cos(alpha) sin(phi), cos(alpha) cos(phi))`.

Write its norm as

`N=sqrt(cos(phi)^2 + cos(alpha)^2 sin(phi)^2)`.

The wet halfspace is `m dot (x,y,z) <= offset`. Its offset domain is bounded
by the hull vertices' projections, including all interval coefficient
combinations. A positive-volume root cannot lie strictly outside that range.
The unit longitudinal axis is `(cos(alpha),0,sin(alpha))`. The transverse
axis is

`(-sin(alpha)cos(alpha)sin(phi), cos(phi), cos(alpha)^2 sin(phi))/N`.

When N's lower interval endpoint is zero, the code retains the full unit-axis
component enclosures instead of dividing by zero or losing limiting poses.
Alternating Taylor polynomials with exact Fraction arithmetic enclose sine
and cosine on the whole requested angle domain. Basic interval operations
and square roots round toward the required side.

## A certain-wet / possible-wet shell, not sampled clipping

The same positive longitudinal quadrature weights and polygon vertices used
by the native hull provider are retained. Each section polygon is split at
y=0 and z=0 once. Inside a sign quadrant, the minimum and maximum of
`m_y*y + m_z*z` over interval coefficients are linear functions of y and z.
Two ordinary halfplanes therefore give:

`certainly-wet polygon subset actual wet polygon subset possibly-wet polygon`.

The halfplanes also include the complete `offset - m_x*x` interval at the
fixed station. Every clip intersection, area and first moment is computed
with exact rational arithmetic on the native binary input coordinates.
No assertion that two sampled attitudes enclose all intermediate attitudes
is made. Signed first moments use the outer polygon for the adverse sign
and the inner polygon for the favourable sign. Their sums provide intervals
for volume and each first moment.

Those intervals are intersected with the admitted root residual band before
division to form buoyancy-centroid intervals. Direct-only centroids may be
intersected with the hull's coordinate envelope; a native cubic union is
never clipped back into the hull, because an interpolant can overshoot it.

Offset contraction queries the **entire lower or upper half interval**. If
the largest possible lower-half volume is too small, the whole lower half is
discarded; similarly for the smallest possible upper-half volume. This is
valid for a nonmonotone native cubic union as well as the monotone exact wet
volume. No interpolated derivative or heave tangent is assumed.

## Root residuals and moving tanks

Native direct and table free-trim solvers admit Euclidean residual norm at
most `2e-6`, with components relative volume error and longitudinal
projection divided by L. The mass band is therefore widened to
`[Mlo/rho*(1-2e-6), Mhi/rho*(1+2e-6)]`. A possible longitudinal root needs
projection within `+/-2e-6*L` (plus an explicit small arithmetic reserve).
Exact mass or moment equalities would incorrectly exclude accepted roots.

The root callback uses the actual tilted-liquid CG; the raw returned lever
still subtracts `[LCG_upright,0,KG_upright]`. The implementation preserves
that distinction. Every physical tank centroid stays inside its solid, so
actual callback X shift is enclosed by its mass fraction times X span.
Physical upright liquids minimise vertical moment, giving nonnegative
physical actual Z shift and an upper shift from the tank's Z span.

Write actual movement from the upright mass CG as `(deltaX,deltaZ)` and
longitudinal residual as R. Eliminating the uncertain longitudinal buoyancy
coordinate gives the raw source lever exactly as

`[cos(phi)*By + sin(phi)*(Bz-KG) - sin(alpha)sin(phi)*R`
` - sin(alpha)^2 sin(phi)*deltaZ`
` - sin(alpha)cos(alpha)sin(phi)*deltaX] / N`.

This equation is bounded interval-wise, including residual allowance and
possible adverse callback movement. It removes the ship-length-scale LCG
term that made the previous universal diagonal bound useless. It does not
identify stability GM with securing GM. The raw dot-product interval is also
computed, and the smaller of two valid upper bounds is retained.

## Signed liquid-transfer lower correction

The checker subtracts a liquid correction; some fixed-tank corrections under
combined trim and heel can be negative. They are not blindly dropped.

For the exact physical liquid problem, a tilted liquid centroid C minimises
the linear potential for its plane normal n, and upright C0 minimises Z.
Consequently `n dot (C-C0)<=0` and `deltaZ>=0`. The transverse axis obeys

`b = -cot(phi)/cos(alpha)*n + N/sin(phi)*(e_z-tan(alpha)*e_x)`.

For positive heel and cos(alpha)>0 this proves the signed lower bound

`(C-C0) dot b >= -N*abs(tan(alpha))/sin(phi)*tank_X_span`.

At zero trim the right side is zero, establishing nonnegativity in that
specific case. Near zero heel or extreme trim, the code uses the always-safe
negative bounding-box projection instead. The less adverse of these two
valid bounds is used per tank. Its mass/volume coefficient is conservatively
enclosed over the entire supplied tank interval, including the source's
98% pressed/slack and small-tank exemption possibilities.

The exact shaped-consumable whole-fill maximum includes empty/full zero
transfer and hence has lower bound zero. Rectangular consumable correction
at its selected maximum-FSM volume retains the signed enclosure. Native
table arithmetic is handled separately; no empirical qualification error is
treated as a uniform physical theorem.

## Actual checker interpolation and proof eligibility

Default acceptance can use cubic hull integral and liquid-moment tables.
Their empirical qualification certificates are not global interpolation
error bounds. A tight exact-polygon bound alone therefore cannot prove
impossibility for the unchanged default checker.

An optional `integral_provider(phi,alpha,offset,direct_bounds)` must return
the **union** of direct and all applicable native-table `volume,mx,my,mz`
intervals. It sets `native_union=True` to prevent an invalid convex-hull
clamp, and `checker_outward=True` only after its uniform interpolation and
floating-operation bounds are included. The root's separate Bernstein
provider supplies that part. Its native coordinates are

`trim=-L*tan(alpha)`,

`q_native=(offset + L/2*sin(alpha)*cos(phi))/cos(alpha)`.

Native table coverage is not a free-trim operating restriction: direct poses
outside it remain in the cover.

An optional `correction_lower_provider(phi,alpha,tanks,direct_lower)` returns
`lower_m`, `checker_outward`, and either a proven
`callback_physical_enclosed=True` or explicit native
`callback_delta_x_m`, `callback_delta_z_m`,
`callback_enclosure_outward=True`. These callback intervals are unioned with
physical movements. A native interpolant can violate a naive physical
minimum-Z bound. Negative native deltaZ is explicitly retained in the reduced
lever formula. Without a certified callback enclosure, longitudinal pruning
and the reduced lever are disabled for native unions. Raw intervals and all
uncertain poses remain.

`checker_outward` propagates through **rejected** boxes and offset
contractions too. An empty leaf set obtained with an uncertified direct-only
rejection is not promoted to a default-checker certificate. Native providers
must also state and cover their arithmetic reserve; this module does not
turn an arbitrary guessed reserve into a proof.

## The piecewise-linear area is enclosed

The source integrates its two-degree GZ knot interpolant, with extra
designated angles, flooding and vanishing roots. The module bounds whole
closed heel slabs rather than just their midpoint. A conservative neighbour
dilation over a complete two-degree span encloses both endpoints of every
possible source linear segment, including inserted roots and specials.
Thus the entire linear segment lies below the slab's dilated upper value.
The sum uses exact rational slab widths times nonnegative upper levers.

Integrating the positive part through 90 degrees relaxes both the actual
flooding endpoint and the first vanishing endpoint. It is an upper bound on
the source's total positive work even if there is initially negative GZ.
No false truncation is inferred merely from a negative upper slab before
the first positive source knot. A future improvement can certify flooding
or continuation/vanishing restrictions, but those are not assumed here.

Spatial splitting is bounded by explicit counts. Every unexpanded possible
box stays in the maximum; there is no optimistic timeout. A caller-supplied
narrow alpha domain is marked `guidance_subdomain`, with both global proof
flags false. The default partition covers all finite slopes. Upright draft
and trim constraints are never borrowed to shrink it.

### Exact-knot lane

`build_knot_area_envelope` retains the whole-slab method above and adds a
usually narrower alternative. It encloses GZ on tiny intervals around the
actual fixed two-degree knots and the 25/30/35/40/50/89.9-degree special/end
knots. It integrates the trapezoids joining their nonnegative upper values.
Clamping an upper endpoint to zero only enlarges the available positive
work and covers every inserted zero-valued vanishing root: a triangle ending
at an earlier zero cannot have greater area than the full-gap triangle to
the clamped next endpoint.

One arbitrary **positive** source knot can remain: the flooding endpoint f
inside a fixed-knot gap [a,b]. The actual final partial trapezoid is bounded
by `(b-a)*(Ua+Ugap)/2`; the full-gap baseline already credits
`(b-a)*(Ua+Ub)/2`. Hence a single extra bonus
`max_gap (b-a)*max(0,Ugap-Ub)/2` suffices. Ugap comes from the complete
whole-heel-slab pose envelope, with its own explicit finite work count.
The bonus is charged once, rather than charging heel uncertainty throughout
all 45 slabs. All later baseline trapezoids are nonnegative, so retaining
them only increases this upper bound after an actual earlier endpoint.
The native curve does not assess a positive tail beyond its fixed 89.9-degree
end: a sealed envelope without a resolved vanishing angle there fails its
coverage condition. No fabricated positive 89.9–90 tail is added.

The knot lane retains a small explicit linear-interpolation/knot-location
arithmetic reserve and deliberately returns `checker_outward=False` pending
a uniform direct-source arithmetic enclosure. Provider flags are reported
separately. It is a mathematically outward physical/PL envelope with complete
free-trim coverage, useful for guidance; a guessed reserve is not promoted
to a default-checker impossibility proof.

The executive installed `gmpy2` in the experiment-local C4/vendor folder to
accelerate exact rational arithmetic after the pure-Python Fraction pilot
proved expensive. `mpq(float)` preserves the exact binary input. The module
falls back to Fraction when that optional dependency is absent and records
the selected rational backend in its output. This does not change the
enclosure algebra or modify the project environment.

## Assumptions, residual limitations and verification

- Exact polygon/quadrature arithmetic describes the checker's mathematical
  hull model, not a continuously exact real ship surface. Native floating
  arithmetic and interpolation require the stated outer providers.
- Physical tank potential arguments require the direct fixed-volume solid
  clipping model with positive density. Native liquid moment interpolants
  require explicit union bounds and do not inherit those arguments silently.
- The current method forgets correlations among tank shifts, KG, LCG,
  buoyancy moments and the root pose. Full-slope tails and unresolved shells
  can therefore keep a broad upper bound even at a generous work count.
- Flooding/first-root restrictions are relaxed and the source angular
  interpolant receives neighbour dilation. Both enlarge available area.
- No infeasible page master, failed local ballast optimization, or sampled
  curve is used as an impossibility certificate.

The root must compare direct shell bounds against native direct integrals,
then cover the actual native tables, project all three phases of the passing
D185000/88/162 controls, and separately check any proposed contradiction.
This subtask has not run a numerical job or asserted a measured tightness,
runtime, map proof, or new full-checker pass. Those observations belong in
the root's retained results and final report.

`test_area_bound.py` supplies nine root-executed regression tests: exact
binary interval/backend semantics; random native rectangular-hull integral
inclusion; mass contraction; real native longitudinal-root retention and raw
lever inclusion; actual negative liquid-transfer inclusion; domain-coverage
and rejected-box flag propagation; and the one-bonus knot-area assembly.
They use toy native hull/tank geometry, not additional development maps.

Rational endpoint/clip helper ideas are adapted from this team's C3
`physics.py`; no mutable engine code is imported or copied. Mathematical
sources are R. E. Moore (1966), *Interval Analysis* (the enclosure principle;
[SIAM author bibliography](https://epubs.siam.org/doi/10.1137/1.9780898717716)),
and G. P. McCormick (1976), [Computability of global solutions to factorable
nonconvex programs: Part I—Convex underestimating problems](https://link.springer.com/article/10.1007/BF01580665)
(spatial outer bounding, not a reproduced implementation of the paywalled
algorithm). All hull/axis/liquid formulae and residual semantics above are
derived directly from the pinned native sources and are not theorem claims
attributed to those books/papers.
