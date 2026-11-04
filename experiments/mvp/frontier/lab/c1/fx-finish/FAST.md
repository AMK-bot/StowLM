The accelerated worker keeps the original V4 candidate search, exact loading
probes, full-checker gate, and owned process watchdog. Its candidate evaluator
is copied from `src/stowage/solver/ballast_v4_evaluator.py`; its only evaluation
change is to visit each public part across all three voyage phases before
moving to the next part: lashing, visibility, stability, strength. The profiled
medium timeout rejected every completed public candidate at departure securing,
so securing precedes visibility as well as the expensive curves. A completed
unchanged public report still supplies each rejection.

The main new numerical kernel is an exact integral for rectangular slack tanks.
The original tank provider solves the liquid plane with 64 volume bisections,
each constructing a convex hull and summing tetrahedra. If the liquid plane
cuts all four vertical edges, it is `z = d + p*x + q*y` in centred box
coordinates. For half-widths `(a,b,c)` and depth `H = d+c`, integration gives
`V = 4*a*b*H`, `CGx = p*a²/(3H)`, `CGy = q*b²/(3H)`, and
`CGz = -c + H/2 + (p²*a²+q²*b²)/(6H)`. These are polynomial integrals of the
actual plane, without a small-angle assumption. The original surface routine
calculates free-surface area and inertia from the four actual corner
intersections. If the plane meets a top/bottom edge, or an input/type is
unsupported, the original provider runs. A strict interior margin protects the
classification at geometric corner events. General box cuts use an exact
inclusion-exclusion volume polynomial in projection coordinates. The original
64 bisections remain, followed by one original convex-hull centroid integration
and the original surface routine. A conservative subtraction-conditioning guard
and an independently recomputed volume residual of at most `1e-10*capacity`
(stricter than the original `1e-9`) return to the original provider if unreliable.

Exact input caches retain completed liquid results, cargo geometry and loading
conditions during public search parts. No float rounding is used. Keys bind
all actual inputs; loading keys include complete instance and ship JSON, while
rectangular-liquid keys include every frozen tank bound, volume, and angle.
These hooks are physically uninstalled outside `Evaluator._part`, including
both V4's complete checker and every independent checker; numerical
qualification sees the original helper identities. Every helper is restored on
exit, including exceptions. The final checker therefore redoes the original
64-iteration tank roots. Its outputs alone determine acceptance. Inside
`ballast_physics` only, a before/after deadline context replaces a Python event
profiling hook; existing per-tank and per-iteration clock checks remain, and the
original owned parent watchdog still bounds opaque calls. No checker budget
helper is replaced. This affects scheduling cost, not any numerical value.

Entry point: `fast_finish.py:finish`, compatible with `lab/score.py --finish`.
It calls Frontier's usual `_finish` wrapper under the acceleration hook, so the
same internal and independent checking costs remain in paired comparisons.
The optional `fast_finish.py:finish_inherited` lane checks the inherited medium
recipe with the unchanged full checker first; a failure falls through to the
same search under its original absolute deadline. This lane is separate because
its extra failed check can consume time on hard candidates. Both entry points
retain worker counters in `fx_finish_diagnostics` metadata.
The module must remain importable on `PYTHONPATH` because the native watchdog
pickles its worker function. `fast_finish.py --selftest` independently compares
the slice kernel with the original tank provider, exercises endpoint and
clipped-edge fallback, and verifies guard/restoration behavior. Syntax checking
passed. The rectangular selftest passed 40 interior and 38 general cuts, with
202 endpoint/corner fallback exercises. Maximum errors were `1.8e-15 m` in
centroid, `2.3e-12 m` in level, `5.1e-11 m²` in area and `3.7e-13 m4` in
inertia. Matched kernel times were 3.47/0.094 s (36.8×) and 3.15/0.215 s
(14.7×). These are kernel measurements, not finishing speedups. The actual
profiled medium uses section tanks, so this kernel alone cannot meet its target.

`shaped_liquid.py` contains separately opt-in section-tank kernels. Its
`polynomial_liquid` reuses the unchanged production exact height-polynomial
builder to solve liquid level, then independently checks volume and computes
centroid using original polygon integration and the original tolerance.
`batched_surface` evaluates the same boundary intersections, even/odd segment
pairing, inclined-plane Jacobian, and first/second moments in NumPy batches.
Prismatic tilted tanks, microscopic fills and unreliable roots use original
fallback. Its `context()` is for public visibility/lashing search parts only.

The optional `gather_context()` uses static copies of
`SectionTankGeometry._integrate` and `HullGeometry._solver_submerged`, changing
only `np.take_along_axis(points, indices[...,None], axis=1)` to the exactly
equivalent `points[np.arange(len(points))[:,None], indices]`. The original
profile attributed 85.9 s self time to 39,217 generic gather calls. Arithmetic,
root solving, contours, integration weights and numerical checks stay the same.
`gather_selftest()` checks bit-identical tank and hull outputs; `selftest()`
compares the section polynomial/surface kernel with original liquid conditions.
The 40 gather pairs were bit-identical but achieved only 0.83x the original
speed; 24 section-polynomial pairs agreed numerically but achieved 0.40x.
Both options remain disabled. Full-checker medium evidence is below.

No external paper was needed for the polynomial integral. Existing V4's
successive-linear search retains its documented Conn, Gould and Toint (2000)
trust-region method. No interpolation or small-angle equation is introduced.

## Retained fixed-stow certificate (v3)

`fast_proof.py` runs source-pinned `early_bound.securing_bound` before numerical
search. The inherited-first lane runs the same bound before spending a full
check on a doomed candidate. A positive result is fixed-stow infeasibility,
never permission to accept a plan.

At an elevated cargo CG, the rotation axis cannot exceed half the hull depth,
giving a ballast-independent lower transverse acceleration. Apply its minimum
forces to the ordinary securing spring chain, retaining wind, both stiffness
cases and all limits. The anchor displacement increases with every equivalent
force, so minimum rod pull above its limit excludes every tank fill. The code
independently verifies the rod force using positive closed-form compliance
coefficients and the original matrix solve, checks residuals, and rounds the
bound down. Detailed governing forces and source hashes are retained.

The TRAIN feeder800 timeout stow has minimum rod ratio 1.479983449 against 1.
Native V4 hit its owned watchdog at 120.916 s; v3 inherited-first returned
`infeasible_proven` in 2.510 s with the same 120 s budget (48.17x). This proves
only this stow impossible; it does not prove the map infeasible or create a pass.

The inherited-first certified TRAIN feeder500 comparison preserved 131 lifts
and passed independently, with finish-only time 82.019→34.798 s (2.36x, v3),
followed by a fresh passing 32.034-second checker call. This is
`finish_inherited`, the selected quick entry.
Accepted plans still pass every ordinary checker part and independent replay.

Validation: 128 rod/compliance cases, 78 rectangular pairs and endpoint/restore
checks, 40 bit-identical gather pairs, 24 shaped-polynomial pairs, and 25 shared
lab tests. The slower shaped kernels stay disabled. `NOTES.md` holds final
paired quick results and scope limitations.
