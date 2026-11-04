# KCS cross-curve interpolation validation

The measurements in this directory precede the hull-following tank migration.
Current fleet requalification and loaded-condition results are in
[../hull_tanks/report.md](../hull_tanks/report.md). The earlier dry departure
timings do not describe the cost of whole-fill shaped-liquid envelopes.

The direct polygon calculation remains the reference. The accelerated geometry
interpolates displaced volume and its three first moments with local tensor
product cubic Lagrange polynomials (four neighbouring nodes per axis), then
solves the original mass and longitudinal-moment equations. The axes are scaled
immersion (mean draught times cosine of heel), trim and heel. Each table also
stores the corresponding baseline-G free-trim KN and LCG, so its parametric
coordinates represent displacement × LCG × heel cross curves. Physical KG stays
in the free-trim balance; it is not silently replaced by the baseline-G trim.

`validate.py` uses the production KCS offsets from `load_ship_params(name)` and
the same physical loading moments in direct and interpolated calculations. It
loads vessel identities with `with_hull=False` to avoid rebuilding unrelated
HullModel tables; the full-check timing cases explicitly use
`load_vessel(name, with_hull=True)`. Random
upright draught, trim, KG, TCG and off-knot heel angles are seeded independently
of the table nodes. It compares GZ, four signed angular areas (0–30°, 0–40°,
30–40°, 0–50°), and initial GM. It separately checks nonzero-TCG floating
attitudes at a lower KG that ensures stable initial equilibrium. Areas use the
same angular quadrature in both branches: this isolates interpolation error
from angular integration error, which has separate direct-method tests.

The acceptance target is a maximum error no larger than one fifth of the
IACS UR L5 rev4 §5.2 Table 1 allowance:

| Quantity | L5 allowance | Interpolation target |
|---|---|---|
| GZ | max(0.05 m, 5% of reference) | max(0.01 m, 1%) |
| Area | max(0.0012 m rad, 5%) | max(0.00024 m rad, 1%) |
| GM | max(0.05 m, 1%) | max(0.01 m, 0.2%) |

Broad holdout states additionally span all three geometric grid axes, with
displacement between production lightship and upright scantling displacement.
The runtime table enforces those same mass bounds. Below-lightship,
above-scantling, dry and fully submerged states are counted explicitly; they
require the direct method. A curve whose free-trim path leaves the grid is
recorded as an outside-grid fallback case. Sampling continues until the stated
number of complete inside-grid curves has been obtained.

`max_l5_fraction` and `p99_l5_fraction` in the JSON are the absolute error
divided by the applicable L5 allowance at each individual sample. The target
is 0.2. These are validation tolerances, never statutory criterion allowances.
The independent tolerance-arithmetic tests cover both the absolute and relative
branches and reject empty, nonfinite or mismatched data.

APPROXIMATION: cubic interpolation replaces direct polygon integration inside
the cache grid. The measured errors are empirical evidence over the reported
random test set, not a mathematical bound over every possible loading state.
Outside-grid cases restart with the direct method. Missing, stale, unqualified
or corrupted caches do likewise. A certificate binds measured errors to the
cache bytes; changing vessel parameters, density, component versions or relevant
geometry/table source invalidates that cache. Near statutory boundaries the
checker rechecks directly.

ASSUMPTION: dry homogeneous departures are diagnostic timing loads, not approved
voyages. They use all holds plus the lowest two deck tiers, identical 20ft
boxes, explicit zero tanks and 90% design displacement, as defined by
`../kcs_departures.py`. They retain production lightship, openings, fixed loads
and centres without cargo balancing.

Reproduce with a single CPU thread, from the repository root:

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
  uv run python experiments/stability/interpolation/validate.py \
  --build --samples 16 --qualify --timings
```

`--build` is an explicit offline operation; ordinary checks never build tables.
A new density has its own cache key and validation certificate but reuses
identical density-independent geometry integrations. Completed immersion planes
are resumable. No GPU or cloud calls are used. Set all three thread variables:
this Mac uses Apple Accelerate, whose thread limit is `VECLIB_MAXIMUM_THREADS`.
The initial reference validation set OpenBLAS/OMP limits only; its actual native
thread count was not measured because process inspection was denied. This does
not change its numerical error evidence. Timings should use the full command
above and report contention on the shared machine.

Final measured results are in [report.md](report.md) and [results.json](results.json).
There are 96 complete random curves (2,112 GZ samples, 384 areas and 96 GM
comparisons), 1,536 additional broad GZ holdouts, and 96 nonzero-TCG equilibrium
comparisons across three vessels and two densities. All six table certificates
meet the one-fifth-L5 target without an in-domain failure. Seventy-two sampled
curve paths outside the cache coverage are explicitly recorded as fallback
conditions, and additional mass-domain/dry/full exclusions are counted.

The final fresh-process timing run set all three thread variables and used the
standard full production loader. Five warm checks per vessel had median/max
times of 0.212/0.220 s (500), 0.237/0.245 s (1000), and 0.253/0.263 s (1700).
All retain the same violation IDs as their direct references. The 1000 condition
lies close to its forward-draught limit; its initial floating attitude is
refined directly while retaining interpolated cross curves. No whole-curve
fallback was needed in these timing cases. Initial uncapped timings are retained
under `superseded_initial_timings` in each per-vessel JSON.

Refresh timings independently, without repeating offline error sampling:

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
  uv run python experiments/stability/interpolation/time_departures.py
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/stability/interpolation/summarize.py
```

`pilot*`, `outlier_diagnostic.json` and `prequalification_timings.json` document
development diagnostics, including deliberately unqualified/out-of-domain
states. They are not the final acceptance evidence.
