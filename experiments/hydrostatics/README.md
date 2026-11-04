# Public-offset hydrostatic validation (D20)

KCS is the independent acceptance reference for the chosen offset hull. No
hydrostatic target is fitted. The former synthetic family and Bardu experiments
are retained separately in [HISTORICAL_BARDU.md](HISTORICAL_BARDU.md),
`validate.py` and `results.json`; Bardu cannot validate the chosen KCS hull.

Reproduce on one CPU thread:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/hydrostatics/validate_kcs.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/hydrostatics/validate_dtc.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run pytest -q tests/vessel/test_hydrostatics_kcs.py tests/vessel/test_hydrostatics_reference.py
```

## Independent KCS comparison

Source: `data/raw/kcs/kcs_gc.html`, NMRI's public KCS particulars table, and
`kcs.fix`/`readhull.txt`, the published full-scale offsets and coordinates.
x is forward from AP, y is half breadth and z is above baseline. All source
stations, including overhangs outside AP/FP, are retained. At the published
even-keel draught 10.8 m, Lpp=230 m and beam=32.2 m:

| Quantity | Published reference | Integrated nx96/nz128 | Error | Stated tolerance |
|---|---:|---:|---:|---:|
| Displacement volume (m³) | 52,030 | 51,944.99 | −0.16339% | ±2% |
| Cb | 0.6505 | 0.64943578 | −0.16360% | ±2% |
| Cm | 0.9849 | 0.98471624 | −0.01866% | ±0.1% |
| LCB forward of AP (m) | 111.596 | 111.60137 | +0.00537 m | ±0.05 m |
| KM derived from model 2 (m) | 14.921090 | 14.94929 | +0.18898% | max(±1%, ±0.05 m) |
| KM derived from model 3 (m) | 14.907407 | 14.94929 | +0.28093% | max(±1%, ±0.05 m) |
| Bare-hull wetted area (m²) | 9,424 | 9,549.84647 | +1.33538% | ±2% |

All listed checks pass; `kcs_results.json` contains final full precision values.
The displacement tolerance uses the IACS UR L5 bar; the KM tolerance adopts its
KG/GM magnitude bar as a stated comparison criterion. This is numerical
validation, not software class approval. Cb, Cm, LCB and wetted-area tolerances
are supplemental and explicitly chosen here.

The reference LCB is −1.48% Lpp relative to midships, forward positive:
`230*(0.5−0.0148)=111.596 m` from AP. Full-scale KM is not directly printed;
it is independently derived from two model columns by geometric similarity:
`(KG+GM)*230/Lmodel`. Model 2 uses `(0.378+0.0158)*230/6.0702`; model 3 uses
`(0.168+0.007)*230/2.7`. Neither value generates or tunes geometry. The small
difference between the references is consistent with their printed rounding.

## Integration and approximations

The production integrator uses composite two-point Gauss quadrature along x,
with every published station retained as a panel boundary. Neighbouring source
half-contours are paired by normalised source arc length and lofted linearly;
target scaling retains that source correspondence. Original section vertices
are preserved and the contour grid adds vertices. Polygon clipping integrates
the actual immersed sections at heel and trim. Section areas and first moments
are integrated rather than inferred from coefficients.

APPROXIMATION: public offset spacing and the piecewise linear loft limit hull
resolution. The source top cutoff is closed by a synthetic watertight deck at
z=16 m for the reference solid, rather than inventing offsets to the published
moulded depth 19 m. This closure does not affect upright results at 10.8 m.
Fleet transformations map that cutoff to each stated deck. Declared closed
coamings count as buoyant; deckhouses and containers do not. Large-angle
results apply to this explicitly declared closed envelope.

Wetted area joins corresponding polygon edges with triangles and clips them at
the waterline. Duplicate internal centre-plane faces under raised keel sections
are excluded. Its approximately +1.34% residual reflects finite source/loft
resolution and is not fitted away.

Doubling nx48/nz64 to nx96/nz128 checks discretisation of this fixed source
loft, not missing detail between supplied stations. Free-trim KN is compared at
30° and 60° at the same displacement and reference LCG. Tests also check
KN(0)=0 and the small-angle KM·sin(heel) limit. Three affine fleet
transformations must preserve Cb, Cm, Cp and Cwp to numerical precision.
Their Cb≈0.6494 agrees with the published 0.6505 within the independent tolerance.

At the final KCS grid, halving nominal spacing changes volume by 0.001017 m³,
LCB by 0.00000191 m and KM by 0.00000256 m. Free-trim KN changes by
0.00000236 m at 30° and 0.00000015 m at 60°. Affine fleet coefficient
differences from the unscaled source are at floating-point roundoff.

## Independent DTC comparison

The OpenFOAM public DTC STL supplies a second independent hull. Published
references are el Moctar, Shigunov & Zorn (2012), Table 1 (PDF p2) and Table 6
(PDF p6), retained at `data/raw/dtc/el-moctar-shigunov-zorn-2012.pdf`.
The paper gives exact model scale 59.407. OpenFOAM's hRef=0.244 m and the
Piehl coordinate reference identify the baseline/AP frame. No normalization
is fitted to displacement, Cb, KM or wetted area.

The source STL is checksum-pinned and sliced into 121 longitudinal planes,
with AP/FP additionally retained. These contours feed the same production
section integrator; tests rebuild them independently from the pinned mesh.

| Draught (m) | Quantity | Published | Integrated | Error | Tolerance |
|---|---|---:|---:|---:|---:|
| 12.0 | Volume (m³) | 136,617.5 | 136,536.8104 | −0.05906% | ±2% |
| 12.0 | KM (m) | 25.95 | 25.9547034 | +0.01812% | max(±1%, ±0.05 m) |
| 14.0 | Volume (m³) | 165,868.5 | 165,809.6928 | −0.03545% | ±2% |
| 14.0 | KM (m) | 25.05 | 25.0504078 | +0.00163% | max(±1%, ±0.05 m) |
| 14.5 | Volume (m³) | 173,467 | 173,405.5794 | −0.03541% | ±2% |
| 14.5 | Cb | 0.661 | 0.66053607 | −0.07019% | ±2% |
| 14.5 | Wetted area (m²) | 22,032 | 22,140.5787 | +0.49282% | ±2% |

All listed checks pass. The 61→121-plane source-slice refinement changes
volume by at most 0.05146% and KM by at most 0.0032 m. Full precision values
and provenance are in `dtc_results.json`; no published target is calibrated.

APPROXIMATION: retain the STL's port half and mirror it, assuming the intended
bare hull is symmetric; preserve separated stem/bulb pieces through zero-width
centreline joins. Linear lofting between mesh slice planes approximates the
triangulated source. Its computational upper cutoff is 34 m; this does not
affect the tested upright draughts 12–14.5 m. This DTC input is a validation
reference, not an extra feeder or a claim about a real ship's watertight deck.

## Historical Bardu result

The Bardu owner-sheet TPC first served as a dimensional sanity check, then as
an explicitly fitted taper-only calibration. The held-out deadweight-derived
displacement increment missed the ±2% bar by +6.61117%. No lightship was
inferred. Preserved results demonstrate why one TPC anchor does not validate a
hull family. They are not validation of the chosen KCS hull. `validate.py`
explicitly constructs the former rounded-bilge fixture after fleet inputs change.

Compute: KCS and DTC reports are local one-core jobs taking 2.18 and 0.73
seconds respectively, below the heavy-job ledger threshold. Root owns
full-suite verification. Public-offset checks do not supply an independent
large-angle GZ reference; KN evidence here is geometric consistency/convergence.

## Exact fleet-input schema for the 1.6 handoff

Keep principal dimensions, operational limits and explicit `[envelope]` records.
Replace `[hull_form]` with these keys (numeric values may use the params
loader's `{value,unit,...}` wrappers):

```toml
[hull_form]
family = "stowlm_offsets_v1"
parent = "kcs"
offsets_format = "kcs_fix"
offsets_path = "data/raw/kcs/kcs.fix"
offsets_sha256 = "aafc7e4f4d773bc50452d397985554c6a20f054150b8865ef70d61f836809190"
reference_lpp_m = 230.0
reference_beam_m = 32.2
reference_draught_m = 10.8
reference_depth_m = 16.0
```

`parent` identifies provenance; it never selects hidden geometry. Each section
comes from the checksum-verified file. Scale x by L/Lref, y by B/Bref, z below
Tref by T/Tref, and z above Tref by
`T+(z-Tref)*(D-T)/(Dref-Tref)`. Here `reference_depth_m=16` means the supplied
offset sealing cutoff, not KCS's published 19 m moulded depth. Source contour
correspondence is fixed before scaling, preserving underwater Cb, Cm, Cp and
Cwp to roundoff. No coefficient formula replaces the sections.

`[envelope]` must retain `deck_z_m = hull.depth_m`,
`count_deckhouse = false`, explicit watertight `hatch_coamings` boxes and
`openings`. Coamings must not overlap source sheer or one another in volume;
unsupported unions fail explicitly. Closed coamings count as buoyant;
deckhouses and containers do not. Preserve the complete input-only metadata
for geometry consumers.

Production `build_hull_model` rejects the retired separable family. It remains
available through HullGeometry for analytic regression tests only. The params
owner must reconcile lightship/distribution and coefficient metadata with the
new hull; this task does not edit params.

## Review fixes and consumer API

`kn(heel_rad, displacement_kg, lcg_m=None, density_kg_m3=None)` accepts actual
port density; omission uses declared hull density. `kn_curve` continues the
same free-trim equations, falling back to the robust solver when needed.
Tables now contain every hydrostatic displacement knot (about 19) at heels
0..60 degrees in 2-degree steps. They remain interpolation aids; direct
geometry must evaluate criteria near deck-edge/flooding knees.

`trimmed_sectional_areas(ta,tf,heel_rad=0,density_kg_m3=None)` returns a full
Submerged record with actual trimmed station areas and Gauss weights. Stored
even-keel Bonjeans extend from zero through the hull/coaming top. MCT uses
`mass*g*(KB+BM_L-KG)/(100*L)`: `hydrostatic_row(...,kg_m=...)` sets actual KG,
while HullModel's table uses lightship KG as its declared reference. Legacy
circular bilges use equal arc-angle nodes; offset sections retain every source
bilge node.

Solve, scalar-KN and KN-curve caches are bounded and process-local. Keys include
the actual geometry/grid SHA, implementation class, rounded lookup values and
the exact physical tuple. Rounding never changes physical inputs or permits a
false hit. Returned arrays are independent copies. Tests compare cached and
uncached outputs exactly, including identical new geometry instances, and
verify density/grid/nearby-input misses.

One-core KCS timings: scalar uncached KN 0.233 s, cached KN 19 microseconds;
31-angle continuation curve 1.62 s, cached curve 35 microseconds. No end-to-end
checker speedup is claimed here.

## Integration handoff outside task 2.1

The supplied KCS hull extends x=-6..236.9 m relative to AP=0 and FP=230 m.
Overhangs remain buoyant and `buoyant_x_min_m/max_m` expose their scaled extent.
The current 3.2 `permissible_stations` rejects points outside 0..Lpp, while
`integrate_strength` assumes its Gauss load grid starts at AP and ends at FP.
Those consumers must handle the actual source envelope; cropping the hull to
satisfy old grid assumptions would discard buoyancy. No strength/fleet source
was edited by this task.

## Proposed decision and current verification

Proposed D20 implementation text: replace the separable fleet hull with the
checksum-pinned published KCS offset loft, affinely transformed below design
draught and piecewise transformed above it. KCS and the independent DTC mesh
checks pass the stated displacement/KM comparison bars without calibration.
APPROXIMATION: linearly loft supplied contours, seal the declared source cutoff
and terminal caps, count only declared watertight coamings, and mirror the DTC
port mesh for that reference. Do not claim independently validated large-angle
GZ or class approval from upright hydrostatics alone. Bardu is historical and
cannot validate this hull.

`UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run pytest -q tests/vessel -o addopts='--import-mode=importlib'`
gave **105 passed, 3 failed in 201.26 s**. Complete KCS HullModel construction
passed at all three feeder dimensions, including dense KN and KG-aware MCT.
The three failures are real-fleet loader tests: current TOMLs still select the
retired family and production assembly correctly refuses it. Task 1.6 owns
that pending migration. The default-suite outcome follows.

The default-suite command with the same environment/settings completed with
**1,823 passed, 13 skipped, 8 failed and 11 setup errors in 545.61 s**.
The 3 real-fleet loader failures and all 11 strength/linear setup errors reject
the still-retired fleet family. Five other failures are outside task 2.1:
`test_draft_trim_and_list_limits` (3.1 free-trim curve at 89.99 degrees),
`test_declared_flooding_uses_real_free_trim_and_preserves_windward_roll` (3.1),
two ballast-integration completion expectations (4.2), and
`test_cannot_disable_completion_certification` (scalable solver). No outside files were
changed to hide these failures. Default-suite acceptance therefore remains
blocked on the fleet migration and the respective owners' integration work.

Compute estimate for dense three-ship tables was about 19 displacement curves
times 1.62 s times three ships, plus table/Bonjean overhead; measured vessel
suite wall time was 201.26 s, with individual assembly tests about 56–60 s.
All runs were one CPU thread, no GPU and no cloud calls. Raw KCS/DTC collection
and manifests were handled by the data owner; task 2.1 changed no raw files.
The optional default suite was estimated from the 201 s vessel run plus the
previous roughly 267 s complete baseline; the expanded default run took 545.61 s.
