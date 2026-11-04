# D30 cache preparation and windage

KN, actual-transfer and upright-sounding entries now depend on their physical
inputs and the relevant builder's own version. CHECKER_VERSION, BALLAST_VERSION,
LINEAR_MODEL_VERSION, unrelated component versions, and fixed background loads
are absent from these physics keys. An unrelated checker change therefore does
not turn a qualified table into a direct-geometry miss.

KN inputs bind actual restored hull sections, the operational mass domain,
water density, and grid axes. Transfer inputs bind each tank's actual section
geometry/weights/extents/capacity, fill/heel/trim grid, and interpolation builder.
The vessel qualification additionally binds hull-form offset checksum/reference
dimensions, buoyant envelope/coamings, lightship and tanks. Prose, provenance,
background loads and downflooding opening locations are excluded. Openings affect
the flooding boundary rather than buoyancy or liquid-transfer integrals.

Own constants are STABILITY_TABLES_VERSION, FORMAT_VERSION (raw hull integrals),
STABILITY_LIQUID_TABLES_VERSION, STABILITY_SOUNDING_VERSION and
STABILITY_LIQUID_MANIFEST_VERSION. Bump the relevant builder when its numerical
behavior changes. Certificates record the input hash, builder version, date,
error statistics and exact numerical asset SHA. Grid misses still use the direct
reference. An explicit one-time D30 migration checks exact predecessor builder,
provider/geometry/density identity, intact certificate and preserved arrays; it
retains the old measurement lineage. Future builder versions cannot use this
metadata-only migration and must qualify afresh. No new physics error measurements
are claimed for merely readdressed identical arrays.

Manifest canonicalization is computed once with the checker's immutable parameter
payload/geometry. Changed bytes replace geometry and canonical metadata together.
This preserves disk identities while avoiding 43/67/80 ms of repeated traversal
for each check on 500/1000/1700.

## Command

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
  uv run python -m stowage.cache_warm --workers 3
```

`--ships feeder_500 feeder_1000` restricts ships; `--workers` accepts 1–8.
Workers load public fleet full/tiny/small/medium geometry, qualify/reuse the two
water-density KN tables and every shaped tank/sounding, then call the public
`stowage.linear.warm_cache(vessel, workers=1)`. The production six-cell tiny
geometry is also included: its actual generator selector retains four hold and
two deck cells. The temporary private selector import is documented in the module;
a future public export should replace it without changing the geometry.

Stability must run before linear calibration, whose exact reference requires
qualified method-.1 liquid tables. A missing public linear hook or failed physics
qualification is explicit incomplete status (exit 2), not apparent success.
Normal checks never build transfer grids. Atomic cache entries and per-family
linear persistence make interrupted warm-up resumable. JSON work summaries live
in `experiments/cache/stability/warmup/`; the command prints built/reused work.

Post-reboot complete warm-up succeeded in **205.51 s**, using three workers.
It reused all six KN tables, 60 transfer tables, 60 soundings, three vessel
certificates and all 15 measured linear calibrations. Eleven coefficient entries
were built; four were already present. No physical error calibration was repeated.
The 15 geometries are the four named fleet presets plus production tiny on each
of three ships. `post_reboot_warmup.json` records every public-hook result.

An identical repeat succeeded in **116.67 s**: all 156 persisted cache entries
were reused; three production-tiny layouts were derived from cached parents.
All 15 coefficient and calibration entries were reusable. This is explicit
idempotence evidence, not a claim that warm-up has zero validation overhead.
The public linear hook is now available; the earlier missing-hook/order failures
and interrupted pre-reboot run are retained only as historical logs.

The upstream vessel JSON cache still keys on global component versions. D30 has
been applied to this owner's KN/tank/sounding/qualification entries; the fleet
cache owner should independently remove its unrelated-version dependency.

## Windage

Only exposed background cargo requires a silhouette. The checker locates the
local weather deck/hatch or pedestal from the declared deck stack support,
falling back to hull depth outside retained stack footprints. Hold cargo has no
windage; supplied cargo envelopes crossing the deck are clipped at that deck.
On-deck missing geometry still fails with checker.missing_vessel_data.

ASSUMPTION: without an occupied envelope, the point CG below its local deck/hatch
surface denotes enclosed hold cargo; a point at/above it denotes exposed cargo.
All 3,348 unsilhouetted hold background points in the fifteen production
geometries pass this classification (`slice_windage.json`). Thirteen targeted
windage tests include on-deck failures, hold acceptance, deck crossing and local
hatch-height differences.

## Verification and timings

Verification on the final code:

- `uv run pytest -q tests/checker/test_stability*`: 506 stability tests pass.
- `uv run pytest -q tests/checker/test_stability* tests/test_cache_warm.py`:
  all 506 stability and 12 orchestration tests pass after the final copy optimization.
- Frozen-interface checks pass in the full run.
- `uv run pytest -q`: two solver failures, both repeated on isolated retry:

  - tests/solver/test_ballast_physics.py::test_loading_margins_cover_active_keys_only
  - tests/solver/test_ballast_physics.py::test_nonnegative_gm_does_not_hide_unavailable_equilibrium

The first uses a direct-equilibrium tolerance of 0.1 mm while the newly reusable
KN path differs by 0.337 mm. The second patches a direct geometry path that is now
bypassed by a valid cache hit. Their owner should make their intended direct
reference explicit. No solver/test files were edited.

Full-check benchmarks use the same synthetic homogeneous wet departures as D28,
with 20% ballast and 50% HFO. They are latency diagnostics, not approved voyages;
existing hard loading/stability violations are retained. All checker parts run,
both table paths are active, and direct liquid-query counts are zero. Five warm
calls follow a fresh-process cold call with disk caches retained. Background
linear calibration/load during measurements is recorded rather than hidden.
Cold full-check latency includes the other checker parts and can exceed 5 s;
near-limit or uncovered conditions deliberately retain direct rechecks.

Final serial measurements (disk caches retained, fresh process per ship):

| Feeder | Cold full check, s | Warm median, s | Five-call warm range, s |
|---|---:|---:|---:|
| 500 | 9.110 | 0.570 | 0.557–0.638 |
| 1000 | 11.103 | 0.627 | 0.611–0.679 |
| 1700 | 13.533 | 0.727 | 0.720–0.797 |

Every part ran in every timed check. Both table paths were active, with zero
direct liquid queries. The **~0.5 s full-check target remains unmet**: measured
warm medians are 0.57/0.63/0.73 s. These are already much faster than the reported
4–5 s direct fallback, but are not represented as achieving the target.

The separate final 1700 breakdown (`final_parts_feeder_1700.json`, five warm
checks) records 0.821 s total: stability 0.370 s, strength 0.097 s, visibility
0.099 s, lashing 0.165 s, and rules 0.033 s. Part isolation and move/ballast/replay
account for the rest. These other checker files are outside this owner's scope.
Concurrent review/linear warm-up workers were present during timing; recorded
load was approximately six, within the shared CPU reservation budget.

Profiling exposed an expensive parameter deep copy. The checker now reconstructs
an independent tree from the exact cached canonical JSON; on 1700 the copy probe
was 0.099 s for deepcopy versus 0.040 s for JSON, with equal values. Existing tests
verify mutation isolation and same-size/same-timestamp content invalidation.
This is bookkeeping only; it changes no physics, interpolation, table key or
qualification evidence. `profile_parts.py` and `benchmark.py` reproduce timings.

Earlier pre-reboot and parallel measurements remain as historical diagnostics.
`final_serial_feeder_*.json` and `post_reboot_*.log/json` are the final evidence.
Dry 1000 legitimately invokes a near-limit direct recheck, so no universal warm
latency guarantee is claimed. See `compute.md` for reservations and measured work.

Changed implementation files: checker/stability.py, stability_tables.py,
stability_liquid_tables.py, stability_liquid.py, stability_sounding.py and the new
stowage/cache_warm.py. Matching checker and orchestration tests, the explicit KN
evidence input in experiments/stability/method1_tables/qualify.py, and the
reproduction/measurement files in this directory complete the change. No shared
component version was bumped; no commit or staging action was taken.
