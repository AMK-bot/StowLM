# C6 mixed-equipment multiport TRAIN cohort

The frozen specification is 32 medium maps: feeder_500 and feeder_800, each
with 3/4/5/6 distinct discharge ports at requested geometric fills 60/70/80%,
plus a second 80% replicate for each ship/port-count cell. Seeds are fixed in
advance to the contiguous fresh range 263360000–263360031. No planner outcomes
select, replace or exclude a map. Six development maps are fixed in
`cohort-spec.json` before the first generation or planner result: feeder_500
p3/f60, p4/f70, p5/f80; feeder_800 p6/f60, p3/f70, p4/f80. They cover both ships,
all fills and all port counts. `development.txt` lists their generated paths.

`cohort.py` adapts the C3 wrapper, documented here as copied algorithm structure,
but imports only the unchanged native `stowage.generate.random_yard` generator,
native models/rules, and its exact expected-equipment-mix helper. It does not
read or import any live experimental solver runtime. No source or parameter
file is edited; no sealed or evaluation data, including c6-maps, are accessed.

The normal feeder-export equipment records and shares remain unchanged:
20/40/45-foot boxes, high cubes, reefers and flat racks, with their original
empty/full, DG, OOG and special-booking draws. There is no dry20 conditioning,
mass rescaling, DG suppression or special-cargo conversion. Rejection sampling
and native necessary conditions can change the admitted mixture, so the
manifest records actual counts rather than asserting every rare category must
occur on every map. Yard blocking remains the source's two-cohort model at0.2.

The native API has no port-count or requested-fill argument. Scoped hooks
replace its cargo recipe, geometric demand sizing and final generation
conditions, restoring every hook in `finally`. The synthetic open route starts
at THLCH and visits the first 3/4/5/6 of SGSIN, MYPKG, VNSGN, HKHKG, THBKK and
USLAX; these already have synthetic profiles. Uniform POD shares and unit
payload multipliers avoid assigning invented port-specific mass differences.
This is a controlled synthetic experiment, not a commercial rotation claim.
Every advertised discharge port must occur in the load list.

Demand is floor(requested fill × decision-cell TEU / native mean TEU per box).
The original per-length geometric budgets are scaled to the requested fill.
Actual admitted TEU must lie from two percentage points below the nominal
fill through the nominal fill, inclusive. This avoids calling a materially
underfilled mixed-equipment draw an 80% map. Draws are hash-addressed and the
coverage/fill conditioning is recorded; singleton native DG replacement draws
remain native, with a final generation condition rechecking the whole list.
Native geometry/mass checks, C1 completion checks, background construction and
physical background acceptance stay unchanged. Optional C2 is off. The current
legacy rule activation must remain off; the wrapper does not change it.

These are empty-arrival, single-loading-call instances with no later loads or
repeated calls. More discharge ports alone do not prevent a sorted zero-crossing
stack; equipment, physical restrictions and hatch dependencies create the
nontrivial choices. Generation is not a full-checker plan pass or a completion
result. Subsequent experiments must report the unchanged full-check pass and
the D241/D242 completion columns separately; no rules are peeled in generation.

Each instance round-trips through the unchanged `Instance` schema. The manifest
records input and generation-record SHA-256, source hashes, exact specification,
actual TEU/fill, POD counts, lengths/ISO types, DG, reefer, high-cube, OOG, empty
and special-booking counts. All seeds and development membership are fixed
before generation. The script checks the full32-grid, six development maps,
coverage, fill band, clean provenance and hashes before completion.

Generation uses one CPU-busy process for this team and one numerical thread,
requesting `nice -n5`. No planners or experimental numerical jobs run alongside
it. If the native background acceptance and repeated cargo/C1 screening make
the32-map run exceed20minutes, the reason is retaining those native physical
gates at high mixed-equipment fill; they are not bypassed or weakened for speed.

Generation completed normally in590.47seconds summed over the32maps, with no
seed substitutions or failed map exclusions. `nice -n5` was requested, but the
sandbox denied priority adjustment; execution continued at inherited priority.
After generation, an independent fresh-process audit round-tripped all32saved
JSON files through `Instance`, verified every input/generation-record/source
hash, the exact24-cell grid plus8replicates, the contiguous seed range, all PODs,
fill bands, clean provenance and all six development selections. The result is
recorded under `verification` in `cohort-manifest.json`.

| Vessel | Nominal fill | Maps | Boxes/map | Decision TEU | Actual fill range |
|---|---:|---:|---:|---:|---|
| feeder_500 |60%|4|86|240|58.44–59.58%|
| feeder_500 |70%|4|100|240|68.44–70.00%|
| feeder_500 |80%|8|114|240|78.02–79.69%|
| feeder_800 |60%|4|162|452|59.13–59.73%|
| feeder_800 |70%|4|188|452|68.25–69.19%|
| feeder_800 |80%|8|215|452|78.04–79.65%|

Per-map emitted cargo ranges are DG0–10, powered reefers1–12, high cubes33–108,
OOG0–5, empties16–48 and special-booking boxes0–12. Every map has both20-foot
and40-foot boxes;45-foot cargo occurs where the unmodified draws and native
screens admit it. These are descriptive inventory counts, not solver outcomes.
No experimental planner arm was run during construction or verification.
