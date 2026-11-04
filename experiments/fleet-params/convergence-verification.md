# Fleet convergence corrections

2026-10-27. Implements `CONVERGENCE.md` section 5 items 1, 2, 3, 4, 8 and 9. Historical `design-verification.md` describes the superseded lower-capacity layout.

| Ship | Cells; hold share | Displacement t | DWT t / reference t | Lightship t | LCG / VCG m |
|---|---:|---:|---:|---:|---:|
| feeder_500 | 516; 30.23% | 9713.353 | 6813.353 / 6832.000 | 2900 | 49.6 / 6.624 |
| feeder_1000 | 816; 31.37% | 16211.273 | 11011.273 / 11150.000 | 5200 | 57.2 / 7.8 |
| feeder_1700 | 1604; 35.66% | 27985.050 | 19911.050 / 21413.000 | 8074 | 73.6 / 9.24 |

## Changes and approximations

- Deck tiers are 5/5/6. Removed automatic tier deletion and asserted hold share at least 0.28. Hold counts remain 156/256/572, retaining shorter columns that fit the KCS form.
- APPROXIMATION, confidence L: 0.6 m covers above 0.6 m coamings; deck bases D+1.2 m, cover VCG D+0.9 m, hold headroom D+0.6−0.05−base. Covers contribute windage through D+1.2 m; their buoyancy is excluded, so the KN envelope still closes at D+0.6 m.
- HFO remains 554/790/1500 m³, inboard of the solved wing edge and outside full 20/40-foot guide columns. Whole paired 40-foot footprints reserve the gap between their 20-foot supports.
- Hull-following DO totals are 103/115/250 m³; FW totals 110/120/200 m³. All 26 tanks per ship have explicit section solids and production API soundings. Ballast remains 3100/4100/7000 m³. Synthetic compartment arrangements are confidence L; 500/1000 consumable capacity anchors are H, scaled 1700 targets L.
- FPT is now 0.92–0.97 L and FWD-DEEP 0.88–0.92 L. Their physical union preserves the earlier forward ballast region.
- The 500 design/scantling draught is 7.05 m from Port of Hamburg particulars. APPROXIMATION, confidence L: a 12 mm stringer contributes to available freeboard for all ships. Raw 500/1000 approximate screens still fail; calibrated screens pass. Dina’s assigned 2.609 m is compared with 2.612 m available. This does not establish assigned load-line compliance.

## Public MARPOL verification

[IMO MEPC.141(54)](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MEPCDocuments/MEPC.141%2854%29.pdf), Annex Regulation 12A.1/3/6/7, PDF pages 3–5, read 2026-10-27. Rule provenance H; geometric implementation L.

The 600 m³ applicability threshold includes diesel fuel and uses 98% filling. The restored fuel totals put all three designs above it: 643.86/886.9/1715 m³. Individual fuel tanks are below 500 m³; side minimum is 0.76 m and bottom minimum max(0.76,min(B/20,2)) is 0.96/1.125/1.38 m. The carve follows the actual KCS contour, including raised bottom, with an additional labelled 0.1 mm side numerical allowance. No Administration-issued approval or oil-piping assessment is claimed.

## Verification

Parameter preflight: `uv run pytest tests/params -q -p no:cacheprovider` **65 passed**, exit 0, 33.81 s, one CPU thread. Includes independent tank/cargo/engine-room exclusion, nonconvex polygon overlap, actual-contour oil protection, capacities, soundings and numeric provenance.

Independent generator geometry probes: zero hull and bottom-protection violations; minimum oil-shell distance above 0.760099 m; residual tank overlap below 4×10⁻¹⁴ m³. Final one-thread probes took 20.90/22.17/23.98 s.

Regeneration produced all three TOMLs in 79.34 s (75.44 CPU s). The optional `/usr/bin/time -l` RSS probe was denied `sysctl kern.clockrate` by the sandbox and returned 1 after generation finished. All completion records and successful TOML readback are retained; this is a measurement-wrapper failure, not a generator traceback.

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run --with 'shapely>=2.1' python experiments/fleet-params/generate.py
uv run python -m stowage.cache_warm --workers 2
uv run pytest -q tests/params tests/vessel
```

Cache warm-up started under the shared ledger with 2 CPU workers / 2 GB / no GPU. Cold estimate: 30–45 min per ship, 60–90 min with two workers. The previous 208 s run reused caches and was not a cold baseline. The 500 and 1000 shards finished after 1528.97/1530.20 s with incomplete loaded-reference qualification (some 120 s offline reference timeouts); no vessel certificate was issued. The final1700shard finished after779.17s. The complete requested command returned exit2 after2310.02s(38.50min), maximum individual childRSS665,878,528bytes; this is not summed aggregate RSS. All three vessel certificates were incomplete. Some production checks exhausted30s and offline references120s; no failing case was accepted. The last observed component interpolation checks passed allships after3.1refinement. The requested combined test command finished: **241 passed, 4 failed, 6 skipped**, 251 selected cases, exit1,369.89s, maximum childRSS638,173,184bytes. All65parameter tests passed. The only failures are stale500/1000 capacity and slice-count expectations in `tests/vessel/test_fleet.py` and `test_fleet_slices.py`, outside1.6scope. Six optional Shapely-dependent hull-tank builder tests were skipped by the locked runtime; generator geometry was independently checked with Shapely and the65parameter tests. No new hull/tank integration failure was reported.

The shared runtime changed during this run. `convergence-source-start.json` records the observed startup source and parameter hashes. Cache completion must be distinguished from merely built tables. No current loading-manual checker pass is claimed by this parameter task.

## Required 2.2 test-owner updates

- Full capacities: 516 / 816 / 1604.
- Slice ladders: 500 `[14,68,252,516]`; 1000 `[16,92,456,816]`; 1700 `[20,116,880,1604]`.
- Tiny slices have four stacks (two hold, two deck), with hatch hold IDs `00102H` / `00302H`.

No files under `tests/vessel` or production source were edited by 1.6.

## Proposed leader-only documentation

D27/plan: replace the 40% hold target and deck-tier deletion with about 30–36% hold share (minimum 28%) and 5/5/6 deck tiers. Actual cells 516/816/1604 are 3.01%/5.99%/5.65% below the published 532/868/1700 anchors. Class names remain 500/1000/1700, so class-relative tolerances are 3.2%/18.4%/5.7%. Record the MARPOL threshold including restored DO on the 500. Keep compartment geometry, cover/stringer dimensions and scaled 1700 consumables labelled approximations.

Files: `params/fleet/*.toml`, `params/fleet/README.md`, `tests/params/test_fleet_params.py`; generator helpers `generate.py`, `kcs_fit.py`, `design_tanks.py`, `freeboard.py`; measurement helper `convergence_run.py` and convergence logs/metrics in this directory. Historical design reports gained a supersession link.

Final fresh-process read-only cache inspection at15:28EDT (`uv run python experiments/fleet-params/convergence_cache_status.py`) returned2: KN qualified at1025/1000kg/m³ and all26liquid tables qualified for each ship. The500vessel-liquid manifest is now valid through concurrent3.1requalification;1000/1700manifests remain invalid/pending. This is newer shared-runtime state than this command’s captured exit2 outcome. Current-runtime status is preserved in `convergence-current-cache-status.json`. The shared3.1owner is requalifying concurrently, so `convergence-evidence/` contains timestamped post-run snapshots of shared reports, not an immutable per-case trace of this process. The captured `convergence-warm.log` and `convergence-warm.json` are authoritative for this command’s outcome. Parameters did not change afterwarmstartup.

## Final handoff

Completed parameter corrections are independently accepted (65/65). Two outside-scope owners remain:2.2 updates four stale capacity/slice assertions;3.1 completes1000/1700 vessel-liquid qualification and associated public linear warm-up. No regulatory criterion or numerical acceptance bound was relaxed by1.6. The raw approximate freeboard caveat remains labelled.

Compute: cachewarm estimated60–90min, revised45–60min, actual38.50min with2CPU/2GB reserved and maxindividualchildRSS0.666GB. Full tests estimated7–12min, actual6.16min with1CPU/1GB reserved and0.638GB maximum childRSS. No GPU. Reservation cleared after finalstatusread. Exact commands, return codes and elapsed measurements are in `convergence-warm.json` and `convergence-test.json`; outcome counts are in `convergence-test-counts.json`.
