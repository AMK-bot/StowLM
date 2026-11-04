# Intact stability numerical verification

## Current production: KCS offsets (D20)

Use [the production KCS departure assessment](kcs_departures.md) and its
[complete metrics](kcs_departures.json) for the current three feeders.
`kcs_departures.py` calls `load_vessel(name, with_hull=True)` and validates
homogeneous departures with no artificial CG balancing. The remaining
departure illustration below is an archived pre-KCS condition and is not a
production performance or pass/fail claim.

The checker no longer reads analytic bilge-radius parameters. Weather
waterline length includes the actual offset-loft overhang; sharp-bilge credit
is derived from section geometry, otherwise k=1 is conservative. The KCS
curve uses declared progressive-flooding openings and its first vanishing
stability root. Total-area work stops at vanishing stability, while the separate 0–40 and
30–40 areas retain their required interval to min(40°, flooding). The curve
also retains independent windward roll coverage through 35 degrees (the A2.3.5-applicable roll bound).
No constant-GZ tail to 90 degrees is fabricated. A sealed-envelope fallback
remains labelled for inputs without openings, but production feeders now
have explicit synthetic opening coordinates.

Large-angle solves, when actually required, use scaled immersion rather than
a diverging mean-draught intercept. They retain physical KG, free trim and
the same mass/moment residual tolerance; three KCS hulls are independently
checked through 89.99 degrees against the original polygon clipper. Duplicate
contour vertices are removed before collinearity reduction, preserving real
KCS corners. The deck edge uses its actual breadth at deck height.


Reproduce on one CPU thread:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run pytest -q tests/checker/test_stability*
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/stability/departure.py
```

The 19 focused tests pass. Known-solid checks independently verify a rectangular
80 × 16 × 12 m hull: displacement = ρLBT, KB = T/2, BMt = B²/(12T), and the
wall-sided formula GZ = sin φ [GM + ½ BMt tan² φ] before deck immersion or
bottom emergence. They cover fresh water, nonzero heel and trim, the direct
geometry implementation and the checker's batched polygon integration.
The batched hull is compared with the direct clipping implementation at 0°, 5°,
30° and 60°, under trim and in fresh water. The nine feeder equilibria span
lightship displacement, design draught and scantling draught for all three
production parameter sets. Their CG is chosen directly above geometric B to
provide an independent, known equilibrium; they are not proposed manifests.

IACS UR L5 Rev.4 §5.2 Table 1 (`data/raw/iacs/ur-l5rev4.txt`, PDF p8) gives
comparison tolerances: displacement 2%; draught and GM the greater of 1% or
0.05 m; GZ the greater of 5% or 0.05 m; GZ areas the greater of 5% or
0.0012 m·rad; equilibrium angles 1°. Those compare independently established
results; they are not regulatory allowances. The known-solid tests use much
tighter numerical tolerances. They do not establish independent real-vessel
approval (the §5 external validation cases remain task 4.1).

## Departure illustration

![Free-trim departure GZ curve](feeder_500_departure_gz.svg)

The synthetic feeder_500 condition has 288 standard-height 20-foot boxes aboard
a 504-cell vessel: every hold cell and the bottom two deck tiers. Cargo mass
varies linearly along x to balance lightship LCG. Draught is 90% of design
(6.354 m), displacement 11,835,517 kg, KG 6.449 m and solid GM 1.250 m. Tanks
are explicitly empty, so FSC is zero; closed covers are permanent equipment
already included in lightship. Cargo masses range from 20,453 to 29,767 kg.

APPROXIMATION: this is an illustrative synthetic departure distribution, not
an approved or empirically typical cargo manifest. It exercises the full vessel
geometry. The fixture builder supplies the instance shell; its vessel, cargo,
onboard state, yard and port draught limit are replaced and the complete
instance is validated. The HullModel uses production operating limits and one
exact hydrostatic row; empty cross-curve/strength tables are unused by this
stability checker. All production tank models are present and declared dry.

The CSV and SVG show 13 direct geometric free-trim samples through 60°, using
the physical KG in longitudinal equilibrium. The connecting segments are a
visual sampling choice. The actual checker separately evaluates its finer
curve through near 90° and reports every failed criterion. This departure
**fails intact stability**, despite its positive initial GM: its B 2.3
lever/area minima are not met. The weather criterion
passes. `departure_metrics.json` records all criterion metrics and violations.
No full plan checker has been run; do not treat this demonstration as shippable.

## Physics review corrections (D21)

For Lpp > 100 m, Part B 2.3 replaces Part A 2.2.1–2.2.3. Initial GM
(A 2.2.4) and weather (A 2.3) remain active. Numeric metrics explicitly select
the criteria: `criteria_part_b_2_3_active`,
`criteria_part_a_2_2_1_to_3_active`, `criteria_initial_gm_active`, and
`criteria_weather_active`. All three production feeders select B 2.3.

The current illustration fails three B 2.3 checks; it is not rejected for its
early GZ peak under the superseded A criteria. The weather criterion passes.
The total credited B area is about 0.1363 m·rad, ending at the vanishing
stability angle of about 37.35 degrees rather than integrating negative
post-capsize work to 90 degrees. The CSV, SVG and JSON were regenerated.

ASSUMPTION / interpretation for leader decisions: B 2.3 total available area
ends at min(downflooding angle, first vanishing stability angle). The latter
is root-refined on the physical free-trim GZ and inserted into the quadrature.
Part A/B 30- and 40-degree areas retain their clause-specific limits.
Declared downflooding openings are intersected with the actual heeled,
free-trim waterplane. Until fleet opening records arrive, the sealed envelope
retains its labelled 90-degree endpoint assumption. This archived implementation extended the final 0.01 degree by a constant
lever; the current KCS checker removes that extension and stops at its
physical assessment endpoints. No post-capsize negative area is credited to B.

Maximum-GZ peaks near 25 degrees receive a bounded search on corrected,
free-trim hull GZ. The standalone criteria helper uses a labelled local
quadratic refinement if no physical refined peak is supplied. Weather area a
uses the complete windward curve, independently of the leeward flooding angle;
area b ends at min(flooding, 50 degrees, second gust intercept). Deck-edge
immersion is solved using heel sinkage and free trim; the standalone criteria
helper labels its upright fallback as an approximation.

ASSUMPTION: ballast remains at fixed fill throughout the voyage; ShipState has
no at-sea transfer schedule. Consumables conservatively span their full usable
ranges and all tanks of each carried type may be slack simultaneously. A
future voyage-transfer representation must include B 3.1.5 transitory stages.
ASSUMPTION: L_WL equals Lpp for the current synthetic hull family, so the
weather length substitution is exact for that family. APPROXIMATION: worst-side
TCG-corrected GZ areas start at geometric upright rather than equilibrium list;
this conservatively retains negative work below the list angle.

Exactly-half and adjacent floating-point tank fills now clamp the symmetric
CDF endpoint before root solving. Numerical errors in loading, equilibrium,
curve, opening and deck-edge phases produce hard `checker.internal_error`
violations, never a regulatory displacement verdict. Explicit displacement
policy violations still use `iscode.loading.displacement`.

## Performance and numerical verification

The two-degree curve includes the 25-degree boundary, direct peak refinement
near that boundary, and the actual vanishing-stability zero. A one-degree
repeat meets IACS area tolerances in the integration tests. Batching retains
the same section polygons and exact clipped moments, excluding dry polygons;
it does not fit reference hydrostatic tables. Timings exclude vessel assembly.

Current one-thread departure checker timings: 1.296 / 1.286 / 1.273 seconds. The subsecond target remains open.

The earlier full-occupancy/ten-half-full-tank stress timings in
`full_feeder_timing.json` are historical, before this review revision; run
`benchmark_full.py` with the same single-thread environment to refresh them.

Compute: one CPU/BLAS thread per job, no GPU/cloud, small arrays. The initial
unbatched illustration estimate was about 20 seconds and measured 27.3 seconds;
the balanced unbatched repeat took 17.5 seconds. Review regression runs remain
single-threaded; exact large-angle liquid cases dominate runtime.
