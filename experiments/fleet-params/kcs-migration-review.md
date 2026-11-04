# D20 fleet migration review — 2026-10-27

Reviewed `data/raw/kcs/NOTES.md` and MANIFEST first, then
`experiments/hydrostatics/README.md`, `src/stowage/vessel/hull.py`,
`hull_offsets.py` and the fleet README. Scope here is documentation only;
the parent owns generation, TOMLs and geometry reconciliation.

## Authoritative geometry and source limits

Use `hull_form.family="stowlm_offsets_v1"`, parent `kcs`, offsets_format
`kcs_fix`, offsets_path `data/raw/kcs/kcs.fix`, SHA256
`aafc7e4f4d773bc50452d397985554c6a20f054150b8865ef70d61f836809190`.
Read 2026-10-27. Public source archive:
<https://www.nmri.go.jp/study/research_organization/fluid_performance/cfd/cfdws05/gothenburg2000/data/KCS_data/kcs_offsets.ZIP>.
`readhull.txt` specifies AP=0, FP=230, positive forward, keel baseline z=0;
published offsets retain all57 stations×100 ordered contour points.

Reference Lpp230 m, B32.2 m, design draught10.8 m are sourced dimensions.
**Reference_depth16 m is an assumed sealing cutoff** of the published
offsets, not the actual KCS moulded depth19 m. Published source particulars
are at <https://t2015.nmri.go.jp/kcs_gc.html>, Main particulars/full-scale
column. No original-source open licence is stated.

Target transforms: x=xs*L/230, y=ys*B/32.2, z=zs*T/10.8 below10.8;
above, z=T+(zs−10.8)*(D−T)/(16−10.8). Correspondence is established in
SOURCE contour arc length before transformation. Source overhangs
x=−6..236.9 m remain buoyant. Do not crop to AP/FP or reinterpret the
ordered contours as single-valued half-breadth-versus-height functions.
Bulbs, bottom segments and repeated heights are intentional geometry.

APPROXIMATION: piecewise-linear contour loft, sealed16 m reference cutoff,
transverse terminal caps, affine transfer of a3600TEU research hull to
feeder dimensions, and declared watertight coamings. No deckhouse/container
reserve buoyancy is credited. Large-angle downflooding still depends on the
explicit unprotected openings; the solid itself cannot establish φf.

## Downstream contract

`build_hull_model` explicitly rejects the retired analytic family. It requires
minimum forward draught, trim/list/GM bounds, bilge-keel flag and windage area
and lever, supplied through hull/operating_limits. It calculates the
hydrostatics and cross curves; strength envelopes remain downstream work.
Cross-curve interpolation is an aid, not a substitute for direct geometry
near deck-edge/downflooding knees. Actual KG is required for MCT and actual
LCG for free-trim KN. Tank solids remain necessary at heel.

Cb/Cm/Cp/Cwp must be derived diagnostics, not fitted geometry inputs. At
design draught affine fleet copies should preserve the source coefficients
to numerical precision. Published KCS Cb0.6505/Cm0.9849/volume52030m³/LCB
111.596m AP are independent validation references. The hydrostatics executive's
nx96/nz128 production integration reported Cb0.64943578, Cm0.98471624,
volume51944.99m³ and LCB111.60137m AP; these numerical values must not be
used to fit offsets. Bardu's previous TPC calibration is historical only.

Check cargo box and tank corners against the actual transformed contour.
Old rectangular-feeder fit assumptions do not survive the move to KCS.
Check source-hull/coaming union validity and reconcile visibility bow/LOA
with scaled buoyant overhangs. Existing3.2 strength station code had AP/FP
bounds assumptions noted by the hydrostatics executive; migration must not
hide that integration issue by deleting overhang buoyancy.

## Capacity and deadweight comparison contract

Report synthetic design displacement from the integrated hull, in kilograms,
then **synthetic DWT = displacement − empirical lightship**, using the same
draught and water density for each ship. Compare it with the following
anchor facts without fitting hull coefficients to them:

| Anchor | Published DWT kg | Published draught m | Source section |
|---|---:|---:|---|
|Arctic Trader|3850000|7.06|Owner brochure PDFp1 Dimensions|
|Dina Trader|11150000|8.70|Owner brochure PDFp1 Dimensions|
|1700TEU Bangkokmax listing|21413000|9.52|Vessel's Specification|

All read2026-10-27. URLs:
<https://mmred.no/images/filer/Arctic-Trader-Brochure-Rev-0-170226.pdf>,
<https://mmred.no/images/Dina_Trader_Brochure-2022.pdf>,
<https://www.eshiptrading.com/SS94778.html>.
The Bangkokmax URL/listing identity discrepancy remains unresolved.
Differences are expected because these are new synthetic designs, with
research-hull scaling and empirical lightship. Do not label a synthetic
DWT difference as a numerical-integration error against a different ship.

## Retired material

The former separable q/v/F analytic family, its integrated coefficients,
LCB=Lpp/2 claim and35%-displacement lightship have no role in current fleet
geometry. Historical notes and analytic regression tests may retain them
only under that description. `params/fleet/README.md` now describes the
pinned source offsets and actual affine transformation instead.

## Generated migration audit

All three current TOMLs parse with stdlib tomllib. Retained counts/tolerances:
440/.12,840/.16,1432/.158. Removed hold cells64/168/288; removed stacks32/56/72.
Every removed cell ID and clearance is retained in layout.kcs_fit_audit.
All ten tanks per ship were adjusted, their capacities and sounding/FSM tables
recomputed. The method checks a20 mm margin at source stations, longitudinal
endpoints and at most0.5 m spacing plus section-vertex heights; it is a sampled
geometry audit, not class approval.

Derived upright displacement and DWT at1025kg/m³:

|Class|Displacement kg|Synthetic DWT kg|Anchor DWT kg|Difference kg|
|---:|---:|---:|---:|---:|
|500|9727130.878595788|6539930.878595788|3850000|2689930.878595788|
|1000|16211273.379058883|10987773.379058883|11150000|−162226.6209411174|
|1700|27985049.660270333|19910729.660270333|21413000|−1502270.3397296667|

The500-class mismatch is+69.87%; report it as material synthetic-design
mismatch, never a real-vessel prediction or fit-validation success.

Verification handoff: a documentation-worker run of
`UV_CACHE_DIR=/private/tmp/stowage-data-uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run pytest tests/params -q -p no:cacheprovider -x`
stopped after32 passes on the independent tank-containment assertion:
feeder_1000 FO-P atx7.4646m,y1.4733628318584056m, source-z1.8620689655172418m
was outside the source polygon. **Fixed in the final regenerated parameters**;
the parameter-test owner subsequently reports57 passed in7.68s, including
independent tank-containment checks. This paragraph preserves the intermediate
finding, not a current open tank defect. No further duplicate tests were run.

Final windage metadata uses a conservative freeboard bounding rectangle across
the full source overhangs x=[−6,236.9]*Lpp/230, from design draught toD.
Rectangle-union area is exact for those declared shapes; the actual shaped
stem/stern lateral profile is not integrated for windage. This approximation
is explicit in both generator metadata and the fleet README.

Root's full-suite verification is underway separately. Its reserved job is
one CPU/about1GB, estimated7minutes; this documentation worker starts no tests
or heavy jobs. A known out-of-scope integration issue remains in
`src/stowage/checker/strength_limits.py` around line175: source readout stations
outside0..Lpp are rejected, although the KCS offsets retain buoyant overhangs.
Do not discard source geometry to satisfy that old consumer restriction.


### feeder_500: removed hold stack IDs

`00101H`, `00301H`, `00102H`, `00302H`, `00103H`, `00303H`, `00104H`, `00304H`, `00105H`, `00305H`, `00106H`, `00306H`, `00505H`, `00705H`, `00506H`, `00706H`, `01705H`, `01905H`, `01706H`, `01906H`, `02101H`, `02301H`, `02102H`, `02302H`, `02103H`, `02303H`, `02104H`, `02304H`, `02105H`, `02305H`, `02106H`, `02306H`

### feeder_1000: removed hold stack IDs

`00101H`, `00301H`, `00102H`, `00302H`, `00103H`, `00303H`, `00104H`, `00304H`, `00105H`, `00305H`, `00106H`, `00306H`, `00107H`, `00307H`, `00108H`, `00308H`, `00505H`, `00705H`, `00506H`, `00706H`, `00507H`, `00707H`, `00508H`, `00708H`, `00907H`, `01107H`, `00908H`, `01108H`, `01707H`, `01907H`, `01708H`, `01908H`, `02105H`, `02305H`, `02106H`, `02306H`, `02107H`, `02307H`, `02108H`, `02308H`, `02501H`, `02701H`, `02502H`, `02702H`, `02503H`, `02703H`, `02504H`, `02704H`, `02505H`, `02705H`, `02506H`, `02706H`, `02507H`, `02707H`, `02508H`, `02708H`

### feeder_1700: removed hold stack IDs

`00101H`, `00301H`, `00102H`, `00302H`, `00103H`, `00303H`, `00104H`, `00304H`, `00105H`, `00305H`, `00106H`, `00306H`, `00107H`, `00307H`, `00108H`, `00308H`, `00505H`, `00705H`, `00506H`, `00706H`, `00507H`, `00707H`, `00508H`, `00708H`, `00509H`, `00709H`, `00510H`, `00710H`, `00907H`, `01107H`, `00908H`, `01108H`, `00909H`, `01109H`, `00910H`, `01110H`, `01309H`, `01509H`, `01310H`, `01510H`, `02507H`, `02707H`, `02508H`, `02708H`, `02509H`, `02709H`, `02510H`, `02710H`, `02905H`, `03105H`, `02906H`, `03106H`, `02907H`, `03107H`, `02908H`, `03108H`, `02909H`, `03109H`, `02910H`, `03110H`, `03303H`, `03503H`, `03304H`, `03504H`, `03305H`, `03505H`, `03306H`, `03506H`, `03307H`, `03507H`, `03308H`, `03508H`

## Final verification handoff

Final parameter-only run:57 passed. Combined run:169 passed/11 failed; its
one stale windage expectation passes after the concurrent test update and
full parameter rerun. Ten vessel failures remain (old capacities, tiny-slice
stack counts, retired analytic strength inputs, small-angleKN referenceLCG).
All three explicit KCS HullModel constructions pass. Full loader still needs
strength-owner integration; the first actual blocker is the missing retired
`aft_run_fraction` key, before the overhang station restriction noted above.
Exact commands, failures and compute evidence: `kcs-verification.md`.
