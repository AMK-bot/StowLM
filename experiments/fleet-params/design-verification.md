# Fleet design correction verification

Historical design-stage report: current layout/tanks/draught and verification are in [convergence-verification.md](convergence-verification.md).

2026-10-27. Scope: fleet parameters and their generator/acceptance tests; no production source changed by 1.6.

| Ship | Cells | Hold cells (%) | Displacement t | Implied DWT t | Reference DWT t | Lightship t | LCG / VCG m |
|---|---:|---:|---:|---:|---:|---:|---:|
| 500 | 372 | 156 (41.94%) | 9727.131 | 6827.131 | 6832.000 | 2900 | 49.6 / 6.624 |
| 1000 | 592 | 256 (43.24%) | 16211.273 | 11011.273 | 11150.000 | 5200 | 57.2 / 7.8 |
| 1700 | 1604 | 572 (35.66%) | 27985.050 | 19911.050 | 21413.000 | 8074 | 73.6 / 9.24 |

Ballast 3100/4100/7000m³ and HFO554/790/1500m³. Each ship has16 ballast,4 HFO and2 consumable tanks. Expanded tanks are explicit KCS-clipped section solids, including cargo/engine-room exclusions and20mm shell inset. Stored soundings use the production2.1 `TankGeometry` API with independent geometry/moment parity checks. Param tests also check finite heel/trim volume conservation.

APPROXIMATION: nominal-size capacity tolerance is25.6%/40.8%/5.7%. Tierwise hold fitting retains shorter stacks;500/1000 have three deck tiers to reach the requested40% hold share. Available capacity is the actual cell count, not the nominal class name.

APPROXIMATION: component lightship CG coefficients and longitudinal distribution remain unverified synthetic design inputs; mass confidenceM, CG confidenceL. All exact review values and uncertainty labels are retained.500 DWT anchor6832t is corroborated by Hafen Hamburg and MagicPort.

APPROXIMATION: the freeboard assertion uses an explicitly calibrated TypeB screen; raw500/1000 screens fail. The0.25m model-bias correction is not conservative or statutory approval. Exact review dimensions were retained. See `design-corrections-review.md` for raw/calibrated values.

Stack-limit audit: the checker divides40ft mass across two supporting20ft stacks. Whole40ft design ratings must therefore also be divided once per support. Existing halving is correct and retained; no checker edit.

## Verification

`uv run pytest -q tests/params tests/vessel` completed: **204 passed,6 skipped,6 failed** (216 collected, exit1). All60 parameter tests passed. Six skips were the optional Shapely-dependent hull-tank preprocessing tests (Shapely is generation-only and intentionally absent from the locked runtime). Executed full-vessel loading, KCS hull assembly and finite-angle section-tank integration tests passed. The six failures are exclusively stale hardcoded capacity and slice expectations in files outside the1.6 scope; no production geometry/API failure remains in this run.

Required2.2 test-owner updates (not edited by1.6):

- `tests/vessel/test_fleet.py`:440/840/1432 ->372/592/1604.
- `tests/vessel/test_fleet_slices.py`: ladders -> `[10,44,180,372]`, `[12,60,328,592]`, `[20,116,880,1604]`.
- Tiny slice assertions must subsequently expect4 stacks (2hold+2deck) and hatch hold membership `['00102H','00302H']`, rather than2deck-only stacks and no hold cells. The stale count assertion currently fails first.

Result/log: `design-verification.json`, `design-pytest.log`. Earlier runs are preserved in initial/pre-hook logs and are historical. The skipped optional builder tests were subsequently run with `uv run --with shapely pytest -q tests/vessel/test_tanks_hull_builder.py`: **6 passed**, exit0. No production dependency was added. No further full-suite rerun is justified until the outside-scope expectations change.

Compute: initial estimate1CPU/1GB/noGPU/7min, revised12min for new cold-hull/tank tests. Actual380.71s, child peakRSS0.535GB. Earlier completed runs175.54s/.509GB and246.56s/.522GB are recorded in fleet history. Reservation cleared.

Generator command:

```sh
UV_CACHE_DIR=/private/tmp/stowage-fleet-uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run --with shapely python experiments/fleet-params/generate.py
```

Requested test command (runner measures wall time and child peakRSS):

```sh
UV_CACHE_DIR=/private/tmp/stowage-fleet-uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/fleet-params/verify_design.py
# executes: uv run pytest -q tests/params tests/vessel
```

No complete loading-manual or full-checker acceptance is claimed by this parameter task.

## Proposed leader-only documentation

Plan1.6: design correction implemented with sourced/confidence-labelled parameters and production hull-section sounding API; parameter acceptance60/60. Combined tests require2.2 to update six stale capacity/slice expectations. Current physical capacity372/592/1604; class labels retain nominal500/1000/1700.

Decisions/evaluation: record the substantial500/1000 capacity shortfall and nonconservative calibrated freeboard screen explicitly; raw approximate TypeB screens still fail500/1000. Lightship CGs and internal arrangements remain labelled synthetic approximations. Loading-manual/full-checker feasibility must be evaluated downstream; it is not established by passing parameter tests.
