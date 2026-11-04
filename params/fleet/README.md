# Synthetic feeder fleet inputs

These are new synthetic designs, not operational particulars or approved loading
manuals for the named reference ships. Read the TOML with Python 3.12 stdlib
`tomllib`. All physical quantities use SI units: m, kg, m³, m⁴, N and N·m.
TEU and bay/row/tier identifiers are dimensionless. A cell is one physical
twenty-foot space; a forty- or forty-five-foot container occupies two cells.

Current generation applies the convergence review items1,2,3,4,8,9. Results
and outstanding test-owner updates are tracked in
[`convergence-verification.md`](../../experiments/fleet-params/convergence-verification.md).

Rebuild the complete fleet, including true polygon-inset tank solids:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run --with 'shapely>=2.1' python experiments/fleet-params/generate.py
```

Shapely is a generator-only experiment dependency; no project dependency file
is changed. Stored tank geometry and sounding tables require no Shapely at load
time. Uniform0.02m shell allowance uses an inward offset of the whole source
polygon, retaining concave/raised-keel stern geometry. Every published KCS
station is an integration-panel boundary. A zero-area centreline stalk may be
removed only with an explicit unchanged-area guard.

| Parameter file | Nominal TEU class | Retained physical cells | Stated tolerance |
|---|---:|---:|---:|
| `feeder_500.toml` | 500 | 516 | 3.2% |
| `feeder_800.toml` | 800 | 816 | 2.0% |
| `feeder_1700.toml` | 1700 | 1604 | 5.7% |

`nominal_teu` labels the target size class; usable geometric capacity is the
physical cell count. Hold/deck counts are156/360,256/560 and572/1032,
giving hold shares30.23%,31.37% and35.66%. Alternative forty-/forty-five-foot
positions add no cells. Weight, height, visibility and stability still apply.

APPROXIMATION: convergence review supersedes the earlier40% hold-share target.
Deck tiers are5/5/6; there is no automatic removal of deck tiers. The explicit
hold-share screen is at least28%, with tierwise KCS fitting preserving shorter
hold columns. The middle design is labelled ~800 TEU under D33: its816
physical cells differ from the nominal800 by2.0%. The868-cell public anchor
is a reference layout, not a capacity target for this synthetic ship.

APPROXIMATION, confidenceL: coaming height0.6m, cover depth0.6m, deck-stack base
D+1.2m and cover VCG D+0.9m. Permitted hold stack height is
D+0.6−0.05−base, giving50mm clearance below the cover. Cover depth contributes
to windage and cargo elevations; cover buoyancy is excluded, so the declared
KN envelope still closes at the coaming topD+0.6m. Cover mass remains included
once in the specified lightship mass; these are synthetic arrangements.

Envelope label: **hatchway credited to the coaming top; cover volume not credited: an intermediate convention pending Q20b**.
This convention is not qualified for real feeder topsides. D67 changes the
500's unprotected hold vents, not the hatchway or cover buoyancy convention.

`layout.kcs_fit_audit` records exact removed stack/cell IDs, clearance and
sampling assumptions for the final layout. Paired hold columns are retained
or removed together to preserve bottom-up and forty-foot pairing rules.
Historical audits of earlier layouts in `kcs-migration-review.md` are not the
current slot list. The subsequent design correction replaces the small
ballast/HFO prisms with hull-following solids; capacity, sounding, FSM and
heated-tank extents are recomputed. Details are in
[`design-corrections-review.md`](../../experiments/fleet-params/design-corrections-review.md).

## Provenance and reference material

Every numeric leaf has either an inherited table `assumption` explaining the
synthetic choice, or a per-field provenance entry overriding that assumption.
Directly sourced dimensions retain document, page/section, public URL and
date read. Numeric identifiers and generated coordinate lists inherit the
same declared generation assumptions as their parent records. Boolean flags
express capabilities and do not carry physical units.

Sources were read on 2026-10-27. The existing `data/raw/*/MANIFEST` files record
retrieval metadata and hashes. No new downloads were made for these inputs.
The brochures have no stated reuse licence; only numerical facts are used.

* [Arctic Trader owner brochure, PDF p1](https://mmred.no/images/filer/Arctic-Trader-Brochure-Rev-0-170226.pdf):
  principal dimensions, 532 TEU anchor, hatch sizes, hold/hatch stack weights,
  92 reefer plugs and aggregate tank capacities. Its printed deck forty-foot
  stack limit of “1000t” is suspect and is not treated as a valid limit.
* [Dina Trader owner brochure, PDF p1](https://mmred.no/images/Dina_Trader_Brochure-2022.pdf):
  principal dimensions, 868 TEU and 234 reefer connections. Dina has open-top
  holds; the synthetic closed-top design is a deliberate approximation, not
  a reconstruction of Dina or its sister Annabella.
* [Vega Tau particulars, PDF pp1–3 and 6](https://www.vega-reederei.de/pdf/tc_VEGA_TAU.pdf):
  nearby closed-top 960 TEU reference, separate tanktop/hatch/deck stack
  limits and tank capacities. Its loading conditions and hatch exceptions
  must be retained when interpreting source limits.
* [Public 1700 TEU Bangkokmax listing, Vessel’s Specification](https://www.eshiptrading.com/SS94778.html):
  principal dimensions, 1700 TEU, 192 reefers and five holds. URL SS94778
  displays listing SS92210, an unresolved source-identity discrepancy.
  Lpp, tank geometry and stack limits are not supplied by this listing.
  The KEH 2116 TEU Bangkok-Max design is a different ship and its numerical
  particulars must not silently be substituted for this anchor.
* [Larsen–Pacino benchmark](https://github.com/maritimelab/Stowage-Planning-Benckmark/tree/4b54b2100662f7d928af47b1139a914cde8f71e3),
  CC0 copy of Larsen & Pacino, *A heuristic and a benchmark for the stowage
  planning problem* (2020): bay/stack/cell grouping, tier/reefer flags,
  separate twenty-/forty-foot weight limits, tanks and longitudinal load
  grouping inform the synthetic schema. Its S/M/L ships are substantially
  larger than these feeders. Neither their hydrostatic points nor their
  unverified SF/BM unit conventions are transferred as feeder physics.

The complete numerical source audit and caveats are in
[`source-review.md`](../../experiments/fleet-params/source-review.md).

## Geometry and model mapping

Coordinates follow `src/stowage/model/vessel.py`: x forward from AP, y positive
to port, z above baseline. Odd rows are starboard (negative y); even rows
are port. Cell addresses are `BBBRRTT`: odd bay numbers identify twenty-foot
cells; an even forty-foot position spans its adjacent odd bays at the same
row/tier. Forty-five-foot capability permits overhang at explicitly flagged
paired positions, without creating additional physical cells.

The generated records map to `Vessel.cells`, `.stacks`, `.hatch_covers`,
`.tanks`, `.heat_sources`, `.bridge` and `.hull`. Provenance and construction
metadata must be removed or interpreted by the fleet loader before creating
strict Pydantic model objects. `Cell.vcg_base_m` is the container floor:
container-centre VCG is floor plus half its actual height, with actual heights
of containers below determining upper floors. Do not use a standard-height
centre for a mixed high-cube stack.

Input-only cell fields `vcg_m` and `accepts_high_cube`, cover `cell_ids`,
tank `geometry`, and `layout`/`envelope`/`hull_form` must be retained by the
loader separately from strict model records. The standard-height `vcg_m`
is descriptive; `vcg_base_m` plus actual stack heights drives physics.

`HatchCover.hold_stack_ids` and `.deck_stack_ids` identify the exact covered
and supported stacks. Their cell lists are explicit in the parameters, so
access and hatch restows need no geometric guessing. Each hold stack belongs
to one hatch cover. Covers form part of the lightship, not an additional
fixed load when closed.

`Stack.max_mass_20_kg` is a twenty-foot column limit. The paired cell stacks
share a forty-foot container’s mass equally under the current model; each
`max_mass_40_kg` therefore represents that cell stack’s half of the whole
forty-foot stack allowance. Do not apply the full source forty-foot allowance
to each half independently. Maximum heights constrain actual accumulated
container heights, including high cubes.

The checker implements this convention in `src/stowage/checker/rules.py`:
each column receives `gross_mass_kg / len(footprint)` and selects
`max_mass_40_kg` for an all40/45ft column. A40/45ft footprint has two cells,
so mass is halved once and its stored per-support allowance is half the
whole forty-foot stack rating. The allowance is not halved again by the checker.

| Ship/space | Whole20ft stack kg | Whole40ft stack kg | Stored40ft allowance per support kg |
|---|---:|---:|---:|
|500 hold|68000|68000|34000|
|500 hatch|45000|60000|30000|
|800 hold|100000|140000|70000|
|800 hatch|60000|90000|45000|
|1700 hold|130000|160000|80000|
|1700 foremost hatch|60000|90000|45000|
|1700 other hatches|70000|100000|50000|

The500 ratings use Arctic's published anchors;800 uses Vega Tau's general
tanktop/hatch ratings as synthetic design targets. The1700 ratings and
foremost-hatch distinction are low-confidence interpolation/design choices
between Vega Tau and Bardu. These are declared local structural ratings;
they do not replace lashing calculations or certify mixed-size weight patterns.

## Labelled approximations and physics handoff

APPROXIMATION: bay/row/tier topology, slot coordinates, hatch grouping, plug
allocation, forty-five-foot positions, bridge, heat-source extents and tier
limits are synthetic. The layouts have respectively 12/14/18 odd bays and
6/8/10 central rows, with the large design’s end pairs narrowed to 8 rows.
Retained hold/deck columns have maximum tier counts 3/3, 3/3 and 4/6;
removed hold columns create no loadable cells. Dimensions and capacity anchors
do not establish an approved stowage or lashing plan.

APPROXIMATION: the production hull uses checksum-pinned public KCS offsets,
affinely scaled to each feeder's principal dimensions. It is a scaled research
hull, not recovered feeder lines. The selected family is `stowlm_offsets_v1`;
`offsets_path`, `offsets_format` and `offsets_sha256` identify its exact geometry.
No block coefficient or target displacement is used to fit or replace sections.
The former rounded-bilge analytic family and its coefficients are retired fleet
inputs; it survives only as historical geometry-test material. Bardu's earlier
TPC exercise cannot validate the chosen KCS hull. Independent KCS/DTC evidence
and API details are in
[`experiments/hydrostatics/README.md`](../../experiments/hydrostatics/README.md).

The source is KRISO's public full-scale `kcs.fix`, documented by `readhull.txt`
in the [NMRI Gothenburg 2000 offset archive](https://www.nmri.go.jp/study/research_organization/fluid_performance/cfd/cfdws05/gothenburg2000/data/KCS_data/kcs_offsets.ZIP).
Read 2026-10-27; source metadata and checksums are in `data/raw/kcs/MANIFEST`.
The archive has no stated reuse licence; public availability is not an open
licence. Its 57 stations each contain 100 ordered half-contour points with
station-dependent heights. Preserve flat-bottom segments, repeated heights,
bulb and rise-of-floor contours; they are not a rectangular waterline grid.

Reference inputs are Lref=230 m, Bref=32.2 m and Tref=10.8 m. Source x=0 is AP,
x=230 is FP, z=0 is the keel baseline; the symmetric half is mirrored about
y=0. Source stations extend from x=-6 to236.9 m. Their scaled overhangs remain
buoyant, and the strength/grid consumers must handle the actual envelope.
The reference vertical cutoff **Dref=16 m is a synthetic sealed deck at the
highest published offsets**, not KCS's published moulded depth of19 m.

For source point `(xs,ys,zs)` and target dimensions `(L,B,T,D)`, the transform is:

```text
x = xs*L/230
y = ys*B/32.2
z = zs*T/10.8                                 if zs <= 10.8
z = T + (zs-10.8)*(D-T)/(16-10.8)              if zs > 10.8
```

APPROXIMATION: neighbouring half-contours are lofted linearly at matching
normalised SOURCE arc-length positions, then transformed. Original source
vertices are retained and design-waterline crossings inserted. The truncated
above-water contour is stretched to the target deck; it does not reconstruct
KCS's complete above-water19 m hull. Terminal stations are closed by transverse
end caps. Closed coaming boxes add reserve buoyancy only where explicitly
supported by the geometry union; unsupported overlaps fail. Deckhouses and
containers add no buoyancy. Unprotected `envelope.openings` limit the intact
stability curve at immersion; a closed geometric hull implies no ventilation
closure. These assumptions must not be interpreted as Dina's open-top design.

Underwater Cb, Cm, Cp and Cwp are diagnostics derived by integrating these
sections, invariant under the stated affine transformation at design draught.
They are not independent target inputs. LCB follows the transformed geometry,
not the former Lpp/2 analytic assumption. Integrate the actual heeled/trimmed
solid for KN and displacement rather than extrapolating upright GM.

The following upright diagnostics are integrated at the stated design draught
and1025 kg/m³, nx96/nz128. Full precision and method are recorded in each
`derived_hydrostatics`; these coefficients are results, not geometry inputs.

| Ship | Draught m | Displacement kg | Cb | Cm | Cp | Cwp | LCB m AP |
|---|---:|---:|---:|---:|---:|---:|---:|
|500|7.05|9,713,353.07|0.649436|0.984713|0.659518|0.830413|52.307079|
|800|8.70|16,211,273.38|0.649436|0.984716|0.659516|0.830413|60.366639|
|1700|9.52|27,985,049.66|0.649436|0.984716|0.659516|0.830413|77.635739|

Synthetic deadweight below equals integrated displacement minus the declared
component-design lightship. Anchor deadweights are published brochure/listing facts at their
respective maximum draughts, which equal these chosen design draughts.

| Ship | Synthetic DWT kg | Published anchor DWT kg | Difference |
|---|---:|---:|---:|
|500|6,813,353.07|6,832,000|−0.27%|
|800|11,011,273.38|11,150,000|−1.24%|
|1700|19,911,049.66|21,413,000|−7.01%|

The earlier approximately70%500-class discrepancy used the conflicting
brochure3850t DWT. The revised reference is6832t at7.05m, listed by
[Port of Hamburg, BF Philipp/IMO9123324](https://www.hafen-hamburg.de/en/vessels/bf-philipp/)
and [MagicPort, Arctic Trader](https://magicport.ai/vessels/container/arctic-trader-mmsi-304688000).
Confidence is M; preserve3850t as a disputed historical brochure value.
MagicPort also supplies the revised9.2m moulded depth, corroborated only by
the approximate load-line consistency check. It conflicts with the brochure's
8.25m and is not a class-verified measurement. No hull coefficient is fitted
to the corrected DWT. The500 comparison now uses the same7.05m draught as the corrected public anchor.

APPROXIMATION: active lightship inputs are the exact synthetic design values
selected in the physics review, replacing the2025 TEU-only mass/CG regression.
They are reasoned estimates, not surveyed anchor properties. The review's
Watson/Kupras component coefficients are not all verified against primary
texts. Confidence M means corroborating facts/method consistency; L means
reasoned design estimate with unresolved uncertainty.

| Ship | Lightship kg | LCG m forward AP | KG m baseline | Confidence |
|---|---:|---:|---:|---|
| feeder_500 | 2,900,000 | 49.6 | 6.624 | Mass M; CGs L |
| feeder_800 | 5,200,000 | 57.2 | 7.8 | Mass M/L; CGs L |
| feeder_1700 | 8,074,000 | 73.6 | 9.24 | Mass M; CGs L |

The500-class mass agrees approximately with integrated displacement minus the
corrected public6832t DWT anchor. KG choices correspond to0.72D/approximately
0.69D/0.66D with reviewer component/outfit allowances; these ratios are
assumptions, not verified published empirical coefficients. LCGs are the
explicit rounded review values, not an unrounded0.46L formula. TCG=0 assumes
port/starboard symmetry. Sensitivity ranges remain material: review mass
uncertainty about±250/approximately750/500t and KG uncertainty±0.5/0.6/0.7m.
See [`design-corrections-review.md`](../../experiments/fleet-params/design-corrections-review.md).

APPROXIMATION: longitudinal density is uniform within five intervals with edges
[0,0.15,0.30,0.55,0.80,1.00] Lpp. Positive base weights
0.20/0.15/0.25/0.25/0.15 are exponentially reweighted and normalized to match
the selected design mass and rounded LCG exactly. This supplies an explicit distribution
for shear/bending integration, not observed frame weights. Hatch-cover mass
remains included once within lightship. The source audit is in
`experiments/fleet-params/lightship-review.md`.

Original aggregate ballast anchors were3100/4100/7000m³; HFO remains554/790/1500m³.
The500/800 anchors are inspired by nearby public particulars, with synthetic
arrangement/density conversions; the1700 targets are labelled scaling/design
estimates. D64 revision3's HEEL redesign increases only the500's aggregate
ballast capacity to3159.3350118278818m³; its original3100m³ anchor is retained
explicitly. The800/1700 reallocate existing wing volume and keep4100/7000m³.
Actual integrated capacities are retained in `tank_design`.

Each ship has port/starboard hull-following tank sets for DB-A/M/F,
WING-A/F, HEEL, APT, FPT and FWD-DEEP. FPT is0.92–0.97L and FWD-DEEP
is0.88–0.92L; this splits the earlier0.88–0.97L forward-peak region without
changing its physical union. Consumable and HFO tanks are also explicit section
solids; no reduced-capacity rectangular consumable fallback remains.

| Ship | HFO total m³ | Gas oil total m³ | Fresh water total m³ |
|---|---:|---:|---:|
|500|554|103|110|
|800|790|115|120|
|1700|1500|250|200|

Gas-oil/freshwater targets follow the review's public feeder particulars for
500/800 (confidenceH);1700 targets are labelled scaled design estimates
(confidenceL). Internal geometry is always a synthetic approximation. Fuel and
freshwater are carved from available aft DB/wing pools, with ballast boundaries
originally solved to preserve3100/4100/7000m³, before the HEEL redesign above.
Every40ft hold-column envelope, including
the gap between its paired20ft cells, is excluded from tank solids. Heated HFO
tank bounds remain explicit heat sources.

MARPOL AnnexI Regulation12A was verified against the primary public
[IMO MEPC.141(54) resolution](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MEPCDocuments/MEPC.141%2854%29.pdf),
Annex paragraphs12A.1,3,6–7 (PDF pages3–5), read2026-10-27. It applies to
new ships at600m³ aggregate oil-fuel capacity, using98% filling and including
diesel fuel. All three designs exceed the threshold after restoring DO:
643.86/886.9/1715m³ at98%. Individual fuel tanks remain below500m³, so the
specified side minimum is0.76m; bottomh=max(0.76,min(B/20,2)) is
0.96/1.125/1.38m. The synthetic carve uses labelled conservative geometric
protection. This is a geometry screen, not Administration approval of the ship,
piping, construction or accidental-outflow performance.

APPROXIMATION: `geometry.shape="hull_sections"` solids are clipped from the
checksum-pinned KCS loft. Each carries quadrature `x_m`,
`integration_weights_m` and `sections_yz_m[section][polygon][vertex][y,z]`,
plus bounding extents. These finite-angle solids cannot be replaced by their
bounding boxes. Source-station boundaries and compartment/cargo cut boundaries
are retained in numerical quadrature.

Capacity, upright centroid and free-surface inertia are integrated from each
solid's clipped liquid geometry. Section quadrature and the chosen internal
boundaries are approximations; no real tank plan is claimed. At heel/trim,
consumers must clip these same polygons against the actual liquid plane and
recalculate centroid/free-surface effects. `TankGeometry.from_params` now
dispatches `hull_sections` to the production section-solid implementation in
`src/stowage/vessel/hull_tanks.py`. The final generator stores production
`sounding` rows after independently comparing capacity, centroids and FSM
against its polygon integration. No rectangular fallback is used. Interpolated
upright soundings or
bounding-box inertia cannot replace finite-angle liquid geometry. Heated HFO
tanks have corresponding heat-source bounds, along with engine room/funnel.

No permissible SF/BM, ship-specific GM limits, computed hydrostatics or KN
curves are invented here. Tasks 2.1/3.1/3.2 compute these from the geometry
and current rules. Bardu’s owner-sheet TPC is historical context only; KCS and DTC are the
independent geometry-validation references for the current integration path.

## Approximate freeboard design screen

`load_line` preserves both the raw and calibrated preliminary Type B screen.
The raw method follows the review's limited46CFR42.20 table/depth/sheer and
superstructure calculation, assuming statutory length≈Lpp, zero actual sheer
and standard-height effective enclosed superstructure length0.2L. It omits
bow-height and special Type B details; available freeboard includes an assumed12mm deck stringer. This is not an
assigned freeboard or a verified ICLL-compliant loading limit.

At D/T9.2/7.05,11.3/8.7 and14/9.52m, raw requirements are
2.165648/2.848207/3.765288m versus available2.162/2.612/4.492m including the stringer: the first two
**fail the raw screen**. Dina's brochure quotes assigned freeboard2.609m,
0.239207m below the raw model. A separately declared0.250m downward model
calibration includes that one-ship discrepancy plus0.010793m rounding/model
allowance. Calibrated requirements become1.915648/2.598207/3.515288m.
The800-class calibrated margin is0.013793m and its margin against Dina’s assigned2.609m is0.003m; this does not establish conservative
coverage of model uncertainty. The old500-class D8.25m still fails.

APPROXIMATION, confidence L: the calibrated screen passes the selected exact
design table; subtracting a fitted bias is **not** a statutory deduction or
an established conservative bound. Raw failures remain visible, and no hard
checker criterion or operating draught is relaxed. Complete assigned
freeboard and construction verification remain unresolved. Source tables,
exact equations and calibration decomposition are documented in
[`design-corrections-review.md`](../../experiments/fleet-params/design-corrections-review.md)
and generated by `experiments/fleet-params/freeboard.py`.

## Synthetic dangerous-goods permissions

`dg_document.areas` partitions every stack into `WEATHER-DECK` or a numbered
`HOLD-01` through `HOLD-06`/`HOLD-07`/`HOLD-09`, respectively. A hold is one
bay pair with its two side pontoons; this label does not establish an IMDG
segregation bulkhead. Each area gives `id`, `kind`, `stack_ids`,
`classes_allowed`, `packing_forms_allowed`, `equipment_assumptions` and an
explicit `assumption`. Every stack also carries the loader-ready
`dg_classes_allowed` list and `dg_doc_area_id`. Stack class checks must cover
primary and all subsidiary risks.

The public basis is [SOLAS II-2/19 Tables 19.1/19.3 and 19.4](https://www.imorules.com/solas/03_chapter_II-2.html)
and [IMO MSC.1/Circ.1266/Rev.1, Annex 1 and appendix](https://puc.overheid.nl/nsi/doc/PUC_1949_14/2/).
Table 19.3 specifies required equipment by cargo class/form, not automatic
permission; these are synthetic DoC-style design choices, not issued certificates
or statistically established typical feeder permissions.

| Space | Permitted class/division strings |
| --- | --- |
| Deck | 1.4S, 2.1, 2.2, 2.3, 3, 4.1, 4.2, 4.3, 5.1, 5.2, 6.1, 8, 9 |
| Forward hold HOLD-01 | 2.2, 4.1, 6.1, 8, 9 |
| Each other hold | 2.2, 3, 4.1, 4.2, 4.3, 5.1, 6.1, 8, 9 |

APPROXIMATION: only `packaged_goods_in_closed_freight_containers` is authorized,
including appropriate IMDG packages/pressure receptacles; bulk, portable tanks,
tank containers and open CTUs are excluded. Hold design premises include water
supply, detection/extinguishing, protected electrical equipment, appropriate
bilge drainage, PPE and six empty-hold air changes/hour with suitable extraction.
The forward hold has reduced class authorization; flammable/toxic gas and
explosives arrangements are not assumed in any hold. Class 5.2 is excluded
under deck in line with Table 19.3. Exclusion of 6.2 and 7 everywhere is
synthetic operating policy, not a SOLAS prohibition.

The current Stack model cannot encode packing form or detailed flashpoint
subcategories. Preserve the structured table for downstream generator/checker
gates; class membership alone is insufficient. Permission for 1.4S does not
bypass the checker's existing fail-closed handling of unsupported class-1
DGL stowage categories. Shipment-specific packaging, heat, segregation and
IMDG conditions remain independently binding. Review details are in
`experiments/fleet-params/dg-review.md`.

## Ballast system rates (D74 parameter amendment)

APPROXIMATION: synthetic; Q23/Q30. The selected rates below remain UNVERIFIED
scenario inputs pending expert confirmation. Public equipment datapoints give
commercial context, not pump ratings for these invented vessels.

| Ship | Combined non-HEEL ballast rate (m3/h; synthetic range) | Selected HEEL rate (m3/h; commercial context) | Max cranes |
|---|---:|---:|---:|
|feeder_500|500; 200-500|600; 200-2200|2|
|feeder_800|700; 300-700|1000; 200-2200|2|
|feeder_1700|1000; 400-1000|1500; 200-2200|3|

The review cites the [Damen CF800 public specification](https://www.marine-marchande.net/Voyage/Bruno/Henrike-Schepers/specification-container-feeders-800.pdf),
which lists an anti-heeling system at 1120 m3/h, and the
[Framo anti-heeling brochure](https://www.framo.com/media/azejuddh/framo-anti-heeling_system-brochure-small.pdf),
which supplies the commercial 200-2200 m3/h context. That range is not vessel-certified
and is not a certified operating range for any synthetic fleet member. It
supersedes the original HEEL ranges, which excluded the amended selected rates.
The ballast ranges remain synthetic and UNVERIFIED. The 9-10h sizing rule is withdrawn;
the earlier 35-60min half-fill target is also superseded by this amendment.
Model omission: gravity draining is not modelled (conservative).

Each raw `[ballast_system]` record uses bare, named-unit fields
`ballast_pump_m3_h`, `heel_transfer_m3_h` and `max_cranes`, plus its ranges,
synthetic assumption and D74/review/Addendum B.2 provenance. The declared ballast
rate is the combined system throughput; do not multiply it by the two-pump
design premise. These are selected synthetic scenario rates, not measured
operating ratings. The positive rates and integer crane count have no model
defaults when a system is supplied.

`Vessel.ballast_system` is typed `BallastSystem | None`. The fleet loader
attaches it only with `rule_freeze_v1` on; the existing cache partition separates
the two activation states. With the switch off or legacy data missing the
record, the value is unknown (`None`) and serialization omits the field entirely.
No numerical pump/crane fallback is supplied. Legacy serialized vessels remain
byte-identical on round-trip. Raw parameter SHA identity changes in both states
because the files gained data; that identity change is intentional. Activated
R1/R2 enforcement, crane timing and handling missing inputs belong to their
checker/finisher consumers, not this loading contract.

## Anti-heeling pair

APPROXIMATION: every ship declares `tank_pairs` with the existing `HEEL-P`
and `HEEL-S` ballast tanks as one `anti_heeling` pair, following D64 revision3.
This names the physical tanks; it adds no tank capacity or fill. The generator
sets the assumed50% arrival pre-fill of each tank. In-port redistribution keeps
the pair's total mass fixed. Other tank symmetries and ballast timing remain
consumer responsibilities.

APPROXIMATION: the reviewed anti-heeling design targets at least3° of
initial-GM list-correction authority in every loaded C1/C2/C3 manual condition,
with the pair at50%. The500 HEEL inner wall moves inward0.350182942700282m
into verified cargo-clear space, retaining the existing outer hull-following
contour and every other tank. The800/1700 reassign exact existing disjoint
WING-A/HEEL/WING-F section slabs into a longer central HEEL pair, preserving
aggregate ballast volume. Capacities per tank become175.90374368320875,
299.48906175754325 and589.497958613163m³. All five sounding rows are regenerated
by production finite-angle `TankGeometry`; capacity and FSM are not scaled.
The design screen uses correcting moment divided by displacement×fluidGM
and converts that slope to an angle with arctan, following D64's sizing probe.
It is a synthetic small-angle design criterion, not a manufacturer's rating.
Manual fills, D56/D58, booking headroom and the canonical policy require the
separate leader-owned D71 requalification epoch; these parameters do not certify it.
The authority comparison retains the frozen pre-redesign C1/C2/C3 cargo and
non-HEEL witnesses, substituting only the new actual tanks and50% HEEL pre-fill.
Newly generating the1700 C2 recipe with the larger HEEL cargo-budget reserve
changes its discrete uniform scan from572 hold-only25.8t boxes to1604×8.7t
boxes including deck cargo, and produces negative fluid GM. That generated
condition is unsafe and needs D71 requalification; it is not an authority pass
and is not repaired by this parameter change. Frozen500 C1 retains its
50%-prefill draught failure, and frozen1700 C3 exceeds its draught limit after
the larger pre-fill. Authority sizing alone certifies none of these conditions.

## Shared DG geometry

Each fleet file explicitly declares top-level `imdg_ship_type = "closed_holds"`.
ASSUMPTION: these are deliberately authored synthetic cellular feeders with
hatch covers, as declared by the fleet owner, rather than real-vessel
certifications. The type is not inferred from the vessel name, geometry,
presence/open state of covers, DoC, CTU closure or a chosen placement.
Sibling `[imdg_ship_type_provenance]` preserves that design statement and the
definition source in the loaded vessel's source notes.

The definition is from [IMO MSC.556(108), Amendment42-24](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.556(108).pdf),
IMDG7.4.3.1.2: the7.4.3.2 table applies to cellular containerships with hatch
covers;7.4.3.3 applies to hatchcoverless containerships (local primary-source
table cache: PDF pages506/507). This D59 declaration is loaded in both activation
states. Missing legacy data remains unknown (`None`) and serializes absent;
no ship type is invented from other fields. A declaration selects the applicable
segregation table; it grants no cargo or placement clearance by itself.

APPROXIMATION: `exclusion_regions` names accommodation, enclosed bridge work
area, machinery/service room, hot engine core, the two engine-room air intakes,
forecastle stores and steering-gear workspace. These reference the
same declared deckhouse windage rectangle, bridge conning reference, engine-room
HeatSource and actual aperture edges; the new3D design choices are explicit.
The deckhouse and enclosed bridge work area span0.85B, the bridge work area is0.5m long
and1m high around the conning reference, and each intake is a0.5m cube above
its lowest aperture edge. These are synthetic arrangements, not inferred or
surveyed dimensions. They add no mass or buoyancy and are not extra opaque
own-wheelhouse obstacles in visibility. A distinct0.85B machinery/service-room
envelope contains the existing narrow hot ER core and may include integral
service/tank compartments. The original hot core and disjoint tank geometry
remain unchanged; the entire broad room is not declared a hot solid. The room's
short0.025–0.11Lpp extent is also synthetic, not a verified typical ER length.
Below-deck forecastle stores and steering-gear rooms have explicit source bounds
in `structures`, with no new windage, mass or buoyancy. Named SW2 source references
enumerate every region. The map is complete by construction only for this
synthetic design; no real surveyed completeness is claimed. No operational
clearance follows from these design declarations alone.

APPROXIMATION: `imdg_placement_geometry.heat_sources` binds the complete declared
SW1 thermal map to the same `heat_sources` solids used by E8. Heated-tank records
also name the actual `tank_id`; no separate thermal bounds are invented. Each
listed source is explicitly declared liable to exceed55°C as a conservative
synthetic design choice, rather than inferred from `heated = true` or a future
temperature observation. The design provenance and scenario ids identify the
source only: generators must bind them to the actual shipment, voyage and slot
through the assessment registry before they can support clearance.
The SW2 map has its own explicit provenance and distinct
`*-synthetic-sw2-design` scenario id. Generators must register both this SW2
design reference and the SW1 thermal design reference for the actual assessment
context; neither reference, its registration, nor its completeness is inferred
from the other map. DG consumers must understand the named region references
and new enclosed-work-space source kinds before claiming operational clearance.

## Downflooding points

APPROXIMATION: `envelope.openings` now contains two unprotected hold ventilator
outlets per hold plus two engine-room air inlets: 14/16/20 points, respectively.
All have `x_m`, `y_m`, `z_m`, `kind`, `closure`, `progressive_flooding` and
`coordinate_reference`. Coordinates denote the **lowest unprotected aperture
edge**, not its centre. No weathertight closure is credited for these points.

Hold ventilators lie at x=`bay_pairs[p].lcg_m+7`, y=`±0.4B`, in the
longitudinal walkway beyond the45ft cargo footprint. Engine-room air
inlets lie on the aft deckhouse at x=`0.075Lpp`, y=`±0.08B`.
The800/1700 designs raise both opening groups3m above their former positions.
D67 applies the same synthetic3m raise to the500's hold vents only; its
engine-room inlets remain at their existing height. All hold vents therefore
stand7.5m above deck and remain not weathertight closable.

| Ship | Hold aperture edge z m baseline | Engine-room aperture edge z m baseline |
|---|---:|---:|
|500|16.7|15.2|
|800|18.8|20.3|
|1700|21.5|23.0|

These equal D+7.5/D+6m on500 and D+7.5/D+9m on800/1700.
These are design assumptions, not measured typical feeder arrangements.
The7.5m hold outlets represent synthetic tall trunks, potentially carried by a
lashing bridge or mast house. Typical real vent arrangements and closure heights
remain unverified pending Q20b; no claim that real feeders use these heights is made.
Tank air pipes are assumed effectively auto-closing and do not directly open
into floodable cargo/machinery spaces, so they are not listed as progressive
downflooding points. Trunk cross-sections are unresolved; existing lightship
windage is an approximation without extra slender ventilation-trunk area.

Task 3.1 must calculate the first immersion of these points along the actual
heeled/trimmed equilibrium path to obtain the downflooding angle φf, as defined
in IS Code Part A 2.3.1 (PDF p14). A closed geometric hull alone no longer
justifies a 90-degree assessment endpoint. Do not treat declared ventilation
fire dampers as verified weathertight closures.

## Operating limits and lightship windage

`operating_limits` supplies the seven mandatory assembly inputs read by
`stowage.vessel.hull.build_hull_model`; these override any equivalent `[hull]`
keys. Each value has a per-field source or assumption label. The minimum GM
is the general **0.15 m** floor in IMO MSC.267(85), Part A 2.2.4 (PDF p13),
not an assertion that this GM passes the other GZ, weather, containership or
free-surface requirements. Full loading-condition criteria still apply.

APPROXIMATION: for each synthetic ship, minimum forward draught is 0.35 of
design draught, maximum absolute trim is 0.01 Lpp, and maximum static list is
2 degrees, stored in radians. These are chosen planning limits in a synthetic
loading manual; no evidence establishes them as typical or approved values.
The forward-draught bound is not a verified slamming/propeller-immersion limit.
The list cap does not restrict the heel angles used to calculate KN/GZ or
dynamic roll. These assumptions need naval-architecture review before any
operational claim.

APPROXIMATION: `bilge_keel=false` for **all three** designs. No bilge or bar
keels are designed or inferred from the brochures, so the bare hull receives
no appendage roll-damping credit. Part A 2.3.4 (PDF p15) specifies **k=1** for
round-bilged ships without bilge/bar keels. Do not select the sharp-bilge
factor or infer a keel area from this boolean.

APPROXIMATION: lightship windage uses the exact union of the **declared
rectangles** in `windage.projected_rectangles` at design draught. These
rectangles approximate the lightship lateral outline; exact rectangle union
does not mean the freeboard silhouette was integrated from KCS offsets:

* Hull freeboard: a conservative bounding rectangle spans the full scaled
  source extent `x=[-6,236.9]*Lpp/230`, from design draught through D.
  This includes the KCS bow/stern overhangs. The shaped stem/stern lateral
  profile is not integrated for windage; the rectangle is a declared
  approximation, not a replacement for the offset hull.
* Aft deckhouse: x=0.025..0.11 Lpp, z=D..D+19 m, enclosing the existing
  bridge eye at D+18 m. It is counted for wind pressure and remains excluded
  from buoyancy. Its mass is already within the empirical lightship estimate.
* All closed hatch/coaming silhouettes, through D+1.2 m (coaming plus cover). Opposite-side covers
  overlap in projection and count once. The funnel silhouette is contained
  in the deckhouse silhouette; no extra mast/rail area is assumed.

`windage.area_m2`, `first_moment_m3` and `centroid_z_m` record the area and
vertical area moment above baseline. `operating_limits.windage_area_m2`
equals that area. **`windage_lever_m = centroid_z_m - design_draught/2`**:
it is the wind-pressure arm Z to the approximate underwater lateral centroid
at half mean draught, as permitted by Part A 2.3.2 (PDF p14). It is neither
centroid height above baseline nor height above the waterline. The reference
waterline and underwater centroid are stored explicitly to disambiguate it.

For task 3.1, rebuild the lateral projected union at the loading condition's
actual waterline/trim, using these fixed lightship components and each loaded
deck container's actual length and height, including high cubes and 45ft
overhang. A 40/45ft container contributes one rectangle, not two half-cell
rectangles. Recompute total area A and first moment Q after merging overlaps;
the cargo contribution is the increase in the union, not a blind sum of
shielded transverse rows or lightship overlaps. Compute z_A=Q/A and
Z=z_A-z_underwater, using the integrated underwater lateral-area centroid
or the Code's half-mean-draught approximation, labelled accordingly. Do not
reuse the cached design-condition lever at a different draught or average
unweighted component levers. Containers add windage, never reserve buoyancy.

## Remaining work and model extensions

Earlier D20 migration test counts and failures are historical and do not
verify the current design revision. The section-tank API hook has landed,
and the final generator has regenerated the fleet using production soundings
with independent polygon cross-checks. Full acceptance is not claimed from
regeneration alone. The root executive records the fresh verification run and
its final outcomes in
[`convergence-verification.md`](../../experiments/fleet-params/convergence-verification.md).
Historical migration results remain in `kcs-verification.md`.

The parameters are inputs to physics, not complete `Vessel` instances: the
loader must populate required computed hydrostatic, cross-curve and strength
fields before any full checker run. Windage must be updated for actual cargo
and waterline; operating assumptions still require downstream verification.
Absence must remain an error,
not a silently permissive default.

The current model lacks structured fields for checksum-pinned offset transformation
metadata, watertight/downflooding envelope, block/midship/waterplane/prismatic
coefficients, explicit tank solids, and length-specific paired positions.
Preserve these extra input sections for 2.1/2.2; do not discard them merely
because they are not Pydantic fields. `Tank.table` can represent upright
soundings but cannot replace the solids at heel. `hull_offsets_ref` identifies
the pinned source geometry or its transformation specification; it must resolve
consistently with `hull_form.offsets_path`. Provenance is richer here than
the model’s `sources: list[str]` and must remain accessible to audits.

The parameter acceptance test checks numeric provenance, capacity tolerance,
and referential integrity. It does not certify stability, strength, lashing,
visibility or operational fitness. Run:

```sh
uv run pytest tests/params -q -p no:cacheprovider
```

The root executive owns final parameter and integration verification in
[`convergence-verification.md`](../../experiments/fleet-params/convergence-verification.md).
Do not infer a checker-approved loading plan or current test pass from any
historical run. Preserve the full KCS buoyant overhangs when integrating
strength; earlier AP/FP-only consumer restrictions are historical integration
findings whose current status belongs in the final verification report.
