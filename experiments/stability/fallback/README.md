Final measured results and remaining owner dependencies: [FINAL.md](FINAL.md).

# Wider departure tables and bounded exact fallback

The tank reference still integrates the declared hull-following tank sections.
It now clips all sections in NumPy batches and computes only the required first
moments for liquid centroids. Exact Green-theorem edge-event polynomials replace
repeated clipping of every height slab when building transfer envelopes and
cross curves. The envelope remains an analytically bounded upper estimate; its
existing explicit moment tolerance is retained. See IS Code B3.1.9.1 and the
plain-language derivation in `stability_tank_transfer.py`.

Production numerical work shares a 30-second monotonic budget, including any
near-criterion direct recheck. `STOWAGE_STABILITY_TIME_BUDGET_S` selects a positive
finite override. Polling occurs in hull residuals, liquid centroids, transfer
refinement and curve evaluation, and a final poll rejects an overdue result.
Expiry returns an incomplete part with a hard `checker.internal_error`, never a
physics pass. This is cooperative: one bounded integration kernel may overrun
the deadline. Offline direct qualification uses an explicit 120-second budget;
expired oracle results produce no qualification certificate.

The liquid/hull floating-equilibrium solver retains the same physical moment
balance. Six inexpensive fixed-point steps are followed, when needed, by a
coupled root with an independently checked centroid residual below 1e-7 m.
This fixes slowly contracting low-GM liquid iterations without relaxing their
convergence threshold.

| Table | Requested coverage |
|---|---|
| Hull buoyancy/KN | 33 immersion × 19 trim × 36 heel nodes; 0–70° heel by bilateral hull reflection, trim ±0.06Lpp, physical lightship-to-scantling mass gate |
| Every hull-section tank, including peaks | 257 Chebyshev fills including 0 and 1; 57 heels −70°…+70° at 2.5° spacing; thirteen trim angles −0.06…+0.06 rad |
| DB-M-P/S | Seventeen trim knots retain the prior ±.005/.015/.03 refinements; near-identical floating-point knots are coalesced |

ASSUMPTION: these are interpolation coverage bounds, not operating allowances.
The production list limit remains 2°; ±10° departure holdouts deliberately exceed
it and must retain regulatory violations. Queries outside the qualified domain
always use the bounded direct reference. No extrapolation is accepted.
Interpolation remains local tensor cubic. The liquid builder's own version is
now 2, because its preprocessing kernel changed; all its certificates must be
fresh. Wider KN grids also require their own measured certificates. Warm-up
rejects narrower certified grids and uses eight full loaded holdouts.

`kernel_timings.json` records nine fresh-cache direct measurements over the
three production ship geometries: centroids 2.6–8.3 ms and whole-fill envelopes
32–50 ms. The original isolated DB envelope took 3.46 s; the historical full
fallback checks took 938–1,372 s (`CONVERGENCE.md` §4). The first widened 1700 grid failed its conservative area target (0.326 L5),
so no certificate was written; this prompted the finer grid above. Whole-check
comparisons and refined-domain qualification are pending; kernel
measurements alone do not establish either result.

A fresh 500 FPT-P table, 145 angular planes ×129 fills, built in 2.565 s
(`table_pilot.json`). `warm_1700.log` records the admitted serial warm-up. The
ledger reserves 1 CPU/1 GB, no GPU; the other fleet owner is warming 500/1000.

The forced-direct listed-500 pilot exhausted its 30-second stability budget
(elapsed30.00985s); the full checker returned in36.139s. A separate120-second
reference budget completed the same full check in88.48s under cProfile
(stability80.43s). Profiling is instrumentation-heavy and is not a production
latency measurement. It identified38.28s in560envelopes,23.19s in actual liquid
CG and15.60s in hull clipping. A12-entry polynomial LRU could not retain the
16upright/tilted entries for eight consumable tanks and thrashed; increasing
that memo capacity changes no physics or table key. Further speed fixes and
production timing remain pending.

Further holdouts exposed two different endpoint issues. A coarse5° flooding
prepass could request55° although flooding was50.46°, and that later equilibrium
left the trim table. An adaptive, budget-polled bracket now finds an actual wet
opening within the valid domain, or propagates a real coverage failure. Other
conditions genuinely reached free trim−.04264L/.04632L at flooding, despite
operating departure trim1%L. Both KN and tank grids therefore extend to±6%, with
all compatible old raw nodes reused and fresh certificates required.

A strictly offline interpolation-measurement context observes but suppresses the
production near-criterion full-reference guard. Otherwise its direct recheck
would hide interpolation error by comparing the reference against itself.
Actual domain/numerical fallback invalidates the holdout. Every row records
whether the production guard would have fired; the production checker always
retains that guard. No qualification run is a voyage approval.

The exact hull edge kernel is3.3–4.2× faster than vertex compaction and agrees
with the original provider within1.46e-11m³ volume/4.3e-14m centroid. Exact
bilateral contour proofs enable windward reflection only for genuinely symmetric
solids and matching physical liquid moments. Reflection-aware tank-envelope
memoisation also compares full coordinates, weights, bounds and capacity without
rounding. An eight-tank direct centroid batch uses safeguarded exact-volume
roots, with nearby previous planes as starting guesses only; its seeded queries
measure2.5–4.1ms and agree within4.44e-14m. Volume residuals remain checked.
The forced-direct listed500 full check then completed in15.051s with a valid
stability part (`listed_500_direct_after_symmetry.json`), against938–1372s in
the historical review. The conditions are reconstructed against current public
synthetic fleet parameters, not claimed byte-identical to historical fleets.
