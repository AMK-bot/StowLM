# Loaded trim/list enclosure: tested diagnostic, not certified

This follow-up writes only `trim_bound.py`, `test_trim_bound.py` and this note.
It does not change any frozen cohort component, earlier bound or result.
This initially remained code-only while root owned the sole CPU process.
After explicit admission when BENCH-RUNNING cleared, seven tests and one capped
high88 diagnostic ran. **No trim inequality is admitted to a reported bound**:
the direct floating-clipping perturbation proof remains unreviewed. The measured
certified bounds remain65/90/70/51. `certificate_eligible=False` is explicit,
and the CP adapter rejects such an envelope.

## Which equilibrium the loading rule constrains

`stability._check_stability` applies signed trim, absolute list and both draft
limits to `floating`, the actual loaded listed equilibrium. With production
Method1, `floating_equilibrium` iterates each tank's tilted liquid centroid;
Method2 uses the upright virtual-KG point. The later zero-heel free-trim solve
for the GZ curve is a different state. Applying the loading trim interval to
every GZ heel would be invalid. This module uses only the loaded equilibrium;
it provides no finite-angle righting-area inequality and never substitutes GM
for `containership_area_0_flood_mrad`.

The physical object is a collection of hull sections at x coordinates with
positive quadrature weights. Every legal listed plane cuts each section. The
whole-ship buoyancy centroid is their volume-weighted mean. Bounding all those
cuts outward gives a necessary range of buoyancy arms, which the native moment
equations turn into necessary whole-ship CG arms. Tank coordinate boxes then
convert the CG ranges to linear inequalities in cargo assignment counts and
tank masses.

## Direct geometric enclosure

For signed trim `t = ta-tf`, lengthL and listphi, the checker's unnormalised
waterplane implies

`z <= tf + (1-x/L)*t + y*tan(phi)`.

Use the actual signed loading trim interval, forward-draft minimum and both
end-draft maxima, with explicit outward comparison guards. For `0<=phi<0.5`,
the rational inequality `abs(tan(phi)) <= phi/(1-phi²/2)` bounds its entire
interval. Each section therefore has a horizontal lower and upper waterline.
Exact rational clipping of every input polygon at those two heights bounds
the volume at that station. The coordinate/weight inputs are their exact
binary floating values, without sampled pose fitting.

The actual displacement equation restricts the sum of station volumes too.
`centroid_range` solves the resulting fractional box problem exactly. For a
fixed total volume, put additional volume at the lowest-x stations first to
minimise longitudinal moment, and highest-x first to maximise it. Between
cumulative-capacity breakpoints the moment/volume ratio is linear-fractional,
so its extrema occur at breakpoints or the total-volume interval endpoints.
The routine checks exactly those candidates. This preserves more information
than independently dividing first-moment bounds by an unrelated mass bound.

The vertical buoyancy arm is enclosed by the actual polygon/waterline extent.
For a bilaterally symmetric section, the horizontal unlisted cut has zero
first transverse moment at every base-waterline height. At transverse army,
opposite sides differ in water height by at most `2*T*y`, whereT bounds
`abs(tan(phi))`. A vertical strip of one simple polygon has occupancy at most1;
N polygons have occupancy at mostN. Consequently the first-moment magnitude
is at most

`integral[0,Y] y*(2*T*y*N) dy = (2/3)*T*N*Y³`.

Sum this bound with the positive quadrature weights and divide by the minimum
displacement volume. Full exact contour-multiset reflection is checked by the
native `_bilateral_sections`; without it, use the whole polygon transverse
extent. No assumed hull symmetry is inserted.

Numerical translation is deliberately widened: section volume receives the
larger of `1e-8 * max(1,weight) * edge_count * coordinate_scale²` and
`gamma(128*edge_count+64) * 256*edge_count*max(1,weight)*coordinate_scale²`,
where `gamma(n)=n*u/(1-n*u)`, `u=2^-53`. The centroid
receives the resulting worst-coordinate moment allowance, including tiny
negative numerical section areas. Small-list/small-trim, coordinate-scale and
edge-count gates reject cases outside this source-algebra guard's scope.
The native loaded equilibrium's scaled residual tolerance2e-6 and liquid
fixed-point tolerance1e-7 receive larger explicit guards. As with C3 physics,
this is an outward mathematical enclosure of the supplied numerical source,
not an independently certified model of an actual ship. Its floating guard
still requires independent review before this implementation enters a reported
bound. A full proof must connect native rounded vertex distances/intersection
fractions to geometric strip perturbations near edges almost parallel to the
waterline, including omitted closing-edge cross terms. A large constant,
operation-count allowance or sampled control alone is not that proof. No
sample-based hydrostatic error certificate is asserted.

## Native cached hulls need a different proof

`TableGeometry._values` is **tensor cubic Lagrange interpolation**, using four
nodes along each of immersion, trim and absolute heel. Some weights are
negative. Thus interpolation need not lie between its node values. For example
the fourth basis on nodes0,1,2,3 is `x(x-1)(x-2)/6`, which equals-1/16 atx=1.5
despite its nodal values being0,0,0,1. The native qualification certificate
contains independent error samples; those samples are not a uniform outward
error proof. A direct-polygon envelope alone therefore cannot silently cover
every checker-passing tabulated state.

For the active table, partition the legal query range at every grid knot so
the chosen four-node stencil stays fixed. Convert each exact cubic nodal basis
to the Bernstein basis on that subinterval. Its tensor product has64 Bernstein
coefficients. Bernstein basis values are nonnegative and sum to1, so the full
polynomial's range lies between the smallest and largest transformed
coefficient. This convex-hull step is applied **after transforming the entire
signed polynomial**, never to the original interpolation nodes.

V, Mx, My and Mz are transformed together. Center Mx aboutL/2 and Mz aboutD/2
before bounding the ratio, using the matching coefficient ofV; otherwise large
almost-cancelling first moments make the interval unnecessarily wide. Intersect
the polynomial volume range with the required displacement range. Negative heel
reflects My while V/Mx/Mz remain unchanged. Union the active-table enclosure
with the direct one, because native near-limit/failure dispatch can choose
either provider.

Rational arithmetic treats stored node bytes as exact. The ordinary floating
evaluation guard is based on the **sum of absolute nodal terms multiplied by
bounded basis weights**, not the small cancelled final value. Scale/spacing
guards reject non-fleet extreme tables. The guard is1e-10 of that absolute
sum, plus explicit ratio slack; this is substantially wider than the ordinary
cubic/einsum rounding operation count on the admitted input scale. Source and
actual active-table SHA hashes are returned. Recompute if either native source
or active qualified table changes; a cache added later can change dispatch.

## Converting centroid ranges to cargo/tank moment rows

Let `delta=t/L`. The native longitudinal axis is proportional to `[1,0,-delta]`.
Its equation gives

`CGx = CBx + delta*(CGz-CBz)`, up to the admitted root residual.

Enclose the product by all interval endpoints. Loaded physical CGz lies in the
positive mass-point envelope of lightship, fixed loads, cargo stack tops and
tank solids. Method2's virtual KG is separately bounded by the hard weather
applicability condition, so both providers are covered without identifying
their GM values. Combining the longitudinal and transverse equations gives

`CGy = CBy + tan(phi)*(CBz-CGz)`

with residual terms enlarged by the native length/beam normalisations. This
also uses all interval endpoints; no local stiffness or fitted KM is assumed.

For an upper CGx boundU, every true tank arm is at least its solid's xmin, so

`cargo_Xmoment + sum((xmin[t]-U)*tank_mass[t]) <= U*constant_mass-fixed_Xmoment`.

For lower boundL use the xmax side and reverse the inequality. The two CGy
rows use ymin/ymax similarly. Cargo arm extrema over each supporting stack and
coefficient minima over every group member further relax these inequalities.
Tank masses map to ceil kg with the same signed-coefficient outward guard
proved and tested in `physics_bound_notes.md`. Independent moment tank variables
may be separate from the earlier weather model's variables; this loses coupling
but preserves every actual plan. No requirement that ballast use a particular
operational pattern enters a bound.

## Interface, verification status and next admission

`necessary_envelope(instance)` returns JSON CGX/CGY ranges, buoyancy ranges,
provider/source/cache provenance. `attach_moment_rows(model,instance,T,
allocations,envelope_result=None)` accepts the existing physics allocation
tuple `(count, members, physical_stack_signature, optional_support_floor)` and
adds four inequalities. Supplying an envelope asserts matching instance/input
provenance; uncertified envelopes and native source/table byte changes are rejected, and callers must
also reject a changed active-table dispatch snapshot. It does not certify an
assignment or any operational plan. The CLI uses the shared approved-list gate.

Seven tests **passed in0.69s**: independent discrete-volume centroid controls,
signed Lagrange overshoot, a known tensor polynomial, a synthetic cubic table
whose centroid escapes node extrema, an analytic rectangular hull, complete
work-limit refusal, and rejection of uncertified rows by the CP adapter. The
initial five-test run also passed; the suite was rerun after adding the two
safety controls. Pytest's cache provider was disabled.

With root's explicit second CPU token after BENCH-RUNNING cleared, owned
session71011 measured fixed high88 in **4.987s**, using **40 active-table boxes**
below its64-box admission cap. `trim_envelope_results.json` records native source
hashes, actual table path/SHA and input hash. Diagnostic ranges are
**CGX46.681–58.673m, CGY±1.872m**, with the loose vertical mass-point envelope
0.15–24.88m. These wide intervals remain `uncertified_source_algebra`; they
do not close a lift gap and never entered a full CP-bound search. Token release
followed normal exit; no numerical process remains. Threads were one, bytecode
disabled, vessel cache scoped, and the nice request denied as before.

The tensor loop admits the entire query domain only if the number of q/trim/heel
boxes is at most `max_table_blocks` (default256, measured64). Excess work raises
`EnvelopeWorkLimit` and the public envelope returns `status='unknown'`, without
admitting a cut. It never silently truncates boxes. Runtime scales roughly as
`boxes * 4fields * 3axes * 256` rational multiplications; the40-box check was
tractable. Native rounding proof and retained-state mapping review must precede
any new separately named bound variant. No frozen cohort source is altered.

Reference principle: R. E. Moore (1966), *Interval Analysis*,
[SIAM author bibliography](https://epubs.siam.org/doi/10.1137/1.9780898717716).
The Bernstein enclosure and fractional centroid derivations above are direct
algebra; no downloaded algorithm/code or sampled physical fit is used.
