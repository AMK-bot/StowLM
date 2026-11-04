# Linear follow-up: latency and qualification

Observed 2026-10-27T15:22:14.438292-04:00. Production qualification is **blocked**, not passed.

| Ship | Cells / tanks | Booking boxes | Mean build | Maximum | Peak RSS |
|---|---:|---:|---:|---:|---:|
| feeder_500 | 516 / 26 | 516 | 0.255 s | 0.266 s | 285.3 MiB |
| feeder_1000 | 816 / 26 | 816 | 0.339 s | 0.347 s | 338.4 MiB |
| feeder_1700 | 1604 / 26 | 1604 | 0.436 s | 0.464 s | 397.7 MiB |

Three distinct background-load instances per ship; each timing includes instance band selection and the public linear model preparation/assembly path, with real reference-file discovery and SHA checks. Calibration is supplied only by a clearly marked in-memory historical oracle; every model has `production_usable=False`. The fixture uses current cells, tanks, lightship, operating limits and production strength stations. Inherited historical KN/Bonjean rows are unused. These measurements establish construction cost, not physical accuracy or full feasibility-engine latency. Fresh coefficient rebuilds match cached public numerical fields on all three ships (rtol1e-10/atol1e-7).

Reproduce timing (one thread): `uv run python experiments/linear-model/followup-latency/profile_current.py --ship feeder_500 --booking-count -1 --variants 3` (repeat ships). Raw records/pstats are adjacent. No 192-state recalibration or cold KN build was run.

Preparation now reuses immutable private TOML snapshots, canonical physical projections, and parameter-only reference manifest naming; reads original parameter/offset/table bytes before reuse. Exact polygon-polynomial upright displacement keeps the original Gauss weights; roots match direct geometry within3.84e-13m. Constant consumable-tank weight curves have their own D30 builder/content key. Height inventories sort once per region. Background loads remain instance constants, excluded from coefficient/calibration keys. `build_cache` prewarms declared departure consumable fills.

| Current ship | Light | Medium | Full |
|---|---|---|---|
| feeder_500 | blocked | blocked | blocked |
| feeder_1000 | blocked | blocked | blocked |
| feeder_1700 | blocked | blocked | blocked |

Current 3.1 warm-up has not produced passing whole-vessel/component evidence. The latest recorded feeder_500 attempt failed after311.688s: `vessel certificate requires passing loaded cases and tank-component evidence`. A successful15.05s direct pilot is not fleet qualification. See `reference-readiness.json`; upstream owners continue warm-up. Therefore a duplicate full warm-up / nine-case linear sweep was not launched, and no old error table/certificate was relabelled as current. The earlier1,728-condition zero-unsafe-GM result remains historical.

**1700 LIST:** yes, the policy itself exhausted the band. Historical family lower error0.03201497rad ×1.1=0.03521647rad exceeds the2° limit0.03490659rad. Interior angle gaps at low stiffness are too broad to serve as a useful boundary reserve. Added optional moment-based rows, activated only by matching boundary evidence with independent passing holdouts and checked stable-branch coverage; there is no production boundary certificate yet. New offline boundary solver preserves actual liquid geometry, free trim and total-versus-affine moment coordinates. Synthetic/one-hull tests do not qualify the fleet. No arbitrary cap was applied.

**SF/BM:** independent historical tails exceeded reserves at500 light (lower SF1,349.66N, lower BM7,137.46Nm) and1000 light (upper SF55,304.14N). This is empirical-envelope undercoverage, not a passed holdout. The new qualification gate evaluates every direction/station and refuses any breach; known production profiles also require independent holdout evidence, preventing calibration-only legacy fallback. A fresh current-reference boundary/corner sweep and untouched holdout are still required to size and certify the new envelopes. No larger blanket factor is claimed valid.

**Proposed leader decision/status:** latency target met by warm construction timing fixtures; production3.7 qualification remains blocked on3.1, followed by current-reference calibration, targeted boundary/extreme-tank training and untouched nine-case holdouts. LIST moment reserves require empirical boundary qualification and a stable-branch assumption; every final plan still needs the exact checker. Existing half-full tank-arm/local hydrostatic approximations remain explicitly labelled; no new hull approximation was introduced.

Verification:

- `uv run pytest -q tests/linear`: 317 passed, 0 skipped; 93.635s.
- `uv run pytest -q tests/test_frozen_interfaces.py`: 240 passed, 16 skipped; 5.912s.

Compute: admitted2CPU/1GB/noGPU; estimate20min, extended30min; actualphase19.30min. Largest measured timing worker397.73MiB. Full-test peak not instrumented (prior535MiB admission pilot). No new192-condition calibration or cold KN build. Reservation released; no owned jobs remain.

Files changed this follow-up: `src/stowage/linear/{bands,cache,dependencies,family_calibration,margin_policy,operations,physics,reusable,warm}.py`; new `upright_mass.py`, `qualification.py`, `boundary_calibration.py`; own `LINEAR_MODEL_VERSION` in `src/stowage/versions.py`. Tests in `tests/linear/`: new prepared/upright/boundary/holdout cases, updated dependencies, margin-policy, epoch-guard and warm tests. Artifacts in this directory and D30 numeric caches under `experiments/cache/linear/`; own ledger/history lines only. Production policy bank unchanged. Frozen signatures unchanged.
