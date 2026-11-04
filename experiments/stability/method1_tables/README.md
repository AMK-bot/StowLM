# Actual liquid-transfer tables: production method .1

Production now selects IS Code Part B 3.1.9.1, the actual moment of liquid transfer,
using precomputed tank cross curves. Method .2 remains selectable with
`STOWAGE_STABILITY_FREE_SURFACE_METHOD=2`. Choosing .1 is a Code-permitted booklet
choice; interpolating its numerical tables is an **APPROXIMATION**, qualified
below against the direct geometry. The frozen checker signature is unchanged.

**Status:** all three tank-component and complete random-loaded qualifications,
and all three timed production departures, pass. Error accounting includes both
leeward and windward GZ curves; the final audit recalculated retained curves
without rerunning geometry or changing measured timings.
No shared component version was bumped by this owner.

## What is tabulated

Each hull-following tank stores volume times its three liquid-centroid
coordinates on a fill × heel × trim grid. Shape-aware section polygons and their
recorded longitudinal integration weights supply the offline values. Interpolating
first moments avoids a centroid singularity at zero fill. Local four-point tensor
Lagrange interpolation gives actual centroids; the consumable whole-fill envelope
examines endpoints and all stationary points of its piecewise cubic moment spline.
It does not substitute half fill for a shaped tank's most adverse fill.

The ordinary grid is 129 Chebyshev fill fractions from 0 to 1, 25 heel nodes from −60°
to 60° at 5° spacing, and 5 trim nodes from−0.04 to 0.04 rad. DB-M-P/S use 31 heel nodes
(additional±2.5°,±7.5°,±12.5°) and 13 trim nodes (additional±0.005,±0.010,±0.015,
±0.030 rad). Independent holdouts identified longitudinal-centroid interpolation
error in the coarser DB-M grids; refining those two tanks reduced the area error.
Upright mass centres and GM free-surface inertia retain the exact precomputed
section-sounding polynomials. Method .1's moving centroid enters longitudinal
equilibrium; the transverse transfer correction is subtracted **once** from the
raw GZ, which retains baseline upright KG.

Caches live under `experiments/cache/stability/`. Numerical NPZ digests bind
per-tank certificates; a vessel manifest additionally binds all tank identities,
the complete vessel parameter hash, component versions, geometry providers and
physics consumers. Unqualified, missing, corrupt or mismatched caches are rejected.
Production never builds liquid grids. Uncovered fill/heel/trim queries use direct
method .1 without extrapolation; adaptively integrated prismatic tanks at nonzero
trim also retain the direct provider. The direct-hull/tank reference is selectable
with `STOWAGE_STABILITY_DIRECT=1`; `STOWAGE_STABILITY_LIQUID_DIRECT=1` disables only
liquid interpolation. Warm timing targets apply inside the qualified grid.

## Qualification

IACS UR L5 tolerances used here are GZ `max(5%, 0.05 m)`, area
`max(5%, 0.0012 m·rad)`, and GM `max(1%, 0.05 m)`. The acceptance target is **0.2 of
those tolerances**. A qualification fraction is numerical error divided by the
applicable tolerance, not an allowance added to a statutory criterion.

Each vessel has 16 random loaded moment profiles: all 20 hull-following tanks have
independent fills in 0.005–0.995, both heel sides are exercised, and trim varies
within ±0.035 rad. Displacement is 90% of upright design displacement, with nonnegative
remaining cargo mass. Absolute per-tank transverse error projections are summed
and integrated; this component test uses L5's stricter absolute floors. Full XYZ
errors and the former lightship-normalized3D norm remain explicit diagnostics,
not transverse-GZ acceptance criteria. Four additional random heel/trim conditions
compare all four HFO worst-fill envelopes with the bounded direct reference.
Its summed conservative oracle excess is at most 0.1 mm GZ.

Only DB-M tables changed during refinement. The four expensive HFO envelope
measurements were retained with source-file SHA lineage after checking unchanged
raw vessel parameters, HFO NPZ digests and physical metadata, direct-oracle source,
displacement, tolerances, IDs and finite results. The current HFO table result was
also reevaluated at every retained attitude. These are reused measurements,
**not remeasurements**. Any failed reuse check triggers direct remeasurement.

| Vessel | Component max GZ error, m | Component area bound, m·rad | GZ / L5 | Area / L5 | GM / L5 |
|---|---:|---:|---:|---:|---:|
|500|0.001002|0.00016737|0.02003|0.13948|4.60e−14|
|1000|0.001098|0.00011712|0.02196|0.09760|1.69e−14|
|1700|0.001224|0.00016330|0.02448|0.13608|2.87e−14|

Four additional complete random ballast-loaded conditions per vessel change all
ballast fills independently within 0.10–0.38; consumables are empty, and uniform
cargo mass preserves 90% design displacement. They compare the complete fast
checker curves against **direct hull and direct tanks**, testing actual free-trim
feedback, flooding endpoints, criterion/weather areas and GM together. Component
HFO envelope tests and the wet departures cover consumables separately.

| Vessel | Full random GZ / L5 | Full random area / L5 | Full random GM / L5 | Vessel manifest GZ / area / GM fractions |
|---|---:|---:|---:|---|
|500|0.02703|0.04890|0.00195|0.06928 /0.15960 /0.00557|
|1000|0.01883|0.01114|0.01958|0.07801 /0.14714 /0.01958|
|1700|0.03032|0.00384|0.00630|0.10381 /0.15246 /0.00712|

Manifest fractions take the larger of the measured complete-condition error and
the component liquid fraction plus the independently measured KN fraction.
These are empirical qualification results across the recorded holdouts, not a
uniform mathematical bound over every possible loading condition. KN evidence
is in `kn_validation_current.json`; original measured fractions and exact
version-only rekey lineage are retained. All completed qualifications meet 0.2.

## Homogeneous wet departures and timings

These diagnostics use `load_vessel(name, with_hull=True)`: all hold cells and the
lowest two deck tiers carry equal-mass standard 20 ft boxes; every ballast tank is
20% full, every HFO tank 50%, and diesel/fresh-water tanks are empty. Cargo mass is
adjusted to preserve 90% upright design displacement. They are synthetic
diagnostics, **not approved voyage conditions**.

Cold means the first checker call after clearing checker process caches, with
qualified disk caches retained. Warm numbers are five successive calls.
Vessel/instance assembly and one-time offline table construction are excluded.
All three departures use KN and liquid tables, with zero direct liquid
queries or partial liquid fallback.

| Vessel | Cold, s | Warm median, s | Warm range, s | B 2.3 passed | GM≥0.15m | Weather ratio≥1 |
|---|---:|---:|---:|---:|---|---|
|500|1.3005|0.2693|0.2510–0.2845|4/7|PASS0.7954m|PASS2.5874|
|1000|1.3199|0.3154|0.3009–0.3361|6/7|PASS1.1198m|PASS3.3333|
|1700|2.3916|0.3361|0.3201–0.3477|7/7|PASS2.2372m|PASS4.9030|

All checks meet ≤0.5 s warm and ≤5 s cold with caches. Under D21, these
Lpp > 100 m ships use B 2.3 instead of A 2.2.1–3; initial GM A 2.2.4 and weather A 2.3
remain active. Each B criterion below shows measured/required and its result.

| B 2.3 criterion |500|1000|1700|
|---|---|---|---|
|Downflooding angle≥30°|PASS|PASS|PASS|
|Area0–30°, m·rad|0.13833 /0.10125 PASS|0.18324 /0.09731 PASS|0.37081 /0.12529 PASS|
|Area0–40°, m·rad|0.18710 /0.18001 PASS|0.26412 /0.17299 PASS|0.61611 /0.22273 PASS|
|Area30–40°, m·rad|0.04877 /0.06750 FAIL|0.08087 /0.06487 PASS|0.24530 /0.08353 PASS|
|GZ at/above30°, m|0.38688 /0.37126 PASS|0.56422 /0.35680 PASS|1.43132 /0.45939 PASS|
|Maximum GZ, m|0.41814 /0.47251 FAIL|0.58381 /0.45411 PASS|1.43132 /0.58468 PASS|
|Area to flooding/vanishing, m·rad|0.19087 /0.32626 FAIL|0.28769 /0.31355 FAIL|0.80585 /0.40371 PASS|

The 500 condition fails three B criteria. The 1000 condition also retains its trim,
aft-draught and port-draught loading violations. Passing stability subcriteria
does not override these hard constraints. The 1700 condition retains a trim
violation despite passing all seven B criteria, GM and weather.

The wet reference curves are the retained direct-tank method .1 measurements in
`../method2/departure_feeder_*.json`, with the same small-tank omission IDs and
qualified KN geometry. Complete raw parameter hashes and physical departure
controls match. Since shared metadata changed whole-instance serialization,
each benchmark freshly reruns method .2 and matches displacement, KG, draught,
trim, GM, list, windage area/lever, both GZ curves and flooding angle to the
archive within 1e−7 **before clearing caches for the timed cold check**. Completed
sanity checks match exactly. The retained direct .1 curves are not presented as
new direct measurements. Their comparison isolates liquid-table changes; the
random full-direct cases above independently measure combined hull/liquid error.

| Vessel | Wet max ΔGZ, m | Wet max GZ / L5 | Wet max area / L5 | Wet GM / L5 |
|---|---:|---:|---:|---:|
|500|0.000575|0.01150|0.09274|0.00087|
|1000|0.000337|0.00674|0.01800|0.00172|
|1700|0.000491|0.00983|0.01029|0.00092|

![Feeder500 GZ curves and interpolation residual](gz_feeder_500.svg)

## Windage, assumptions and reproducibility

Fixed-load `silhouette` boxes join the same lateral union as explicit cargo and
vessel windage. Waterline clipping and union area/first moment update both windage
area and lever without counting overlapping rows twice. A slice's positive cargo
background without silhouettes fails closed with `checker.missing_vessel_data`:
its point CG cannot establish that its whole extent is below deck. Explicit
below-deck silhouettes are clipped normally. These full-vessel departures have
no fixed background loads, so their windage results do not measure a slice case.

**ASSUMPTIONS:** ballast fills stay fixed at sea; each carried consumable type's
whole usable filling range is considered, with all its tanks potentially slack
simultaneously. Existing conservative small-tank exemptions use upright inertia
over lightship displacement. Tank masses and physical mass moments are retained
when a regulatory correction is exempted. The upstream hull/tank geometry,
lightship and declared windage approximations remain those in fleet parameters;
the table path does not remove those limitations. Downflooding uses the real
declared openings. Grid misses and near-limit direct rechecks can exceed the
warm timing target.

Physics files: `stability.py`, `stability_loading.py`, `stability_curve.py`,
`stability_sounding.py`, `stability_liquid.py`, `stability_liquid_batch.py`,
`stability_liquid_tables.py`, and `stability_tables.py` under
`src/stowage/checker/`. Direct reference providers remain unchanged. Tests mirror
these modules in `tests/checker/test_stability*`. Experiment scripts here are
`build.py`, `refine.py`, `qualify_tanks.py`, `qualify.py`, `benchmark.py`, and
`refresh_kn.py`, `refresh_error_accounting.py`, and `test_error_accounting.py`. JSON/log files preserve exact parameters, versions,
source hashes, tables, cases and timings. No commit or shared version bump.

Compute: one CPU-only umbrella reservation of 4 cores/4 GB, estimated 45 minutes;
the reservation completed in roughly 38 minutes versus the 45-minute estimate. Offline table
construction/refinement is a one-time preprocessing cost, separate from cold
checker timings. The owner records final measured reservation time in the fleet
ledger/history; no GPU work was used.

Representative commands, with `UV_CACHE_DIR=$PWD/.uv-cache` and single-threaded
BLAS environment variables:

```sh
uv run python experiments/stability/method1_tables/qualify_tanks.py --vessel feeder_500 --samples 16 --envelope-samples 4 --reuse-envelopes experiments/stability/method1_tables/tanks_feeder_500.json --certify --output experiments/stability/method1_tables/tanks_final_feeder_500.json
uv run python experiments/stability/method1_tables/qualify.py --vessel feeder_500 --samples 4 --tank-evidence experiments/stability/method1_tables/tanks_final_feeder_500.json --certify --output experiments/stability/method1_tables/loaded_feeder_500.json
uv run python experiments/stability/method1_tables/benchmark.py --vessel feeder_500 --output experiments/stability/method1_tables/benchmark_feeder_500.json
uv run pytest -q experiments/stability/method1_tables/test_error_accounting.py
uv run pytest -q tests/checker/test_stability*
uv run pytest -q
```

Verification: `uv run pytest -q tests/checker/test_stability*` passes all 492 tests;
experiment error accounting passes 21/21 tests.
The final combined accounting and `tests/test_frozen_interfaces.py` command also
exits successfully; frozen signatures are unchanged.
The full-suite run reports 12 failures outside stability:
ten legacy tuple-style fixed loads in visibility/lashing tests, one missing IMDG
CTU classification, and one solver tuple-field mismatch. All 12 recur in
`full_failures_retry.log`. Do not interpret this as a passing full suite.
