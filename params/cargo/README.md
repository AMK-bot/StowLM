# Synthetic feeder cargo parameters

`feeder_export.toml` supplies task 2.5 with synthetic export bookings for
Laem Chabang-like regional feeders. Read it with Python 3.12 `tomllib`.
These are public-source-informed test inputs, not observed carrier demand.
The same conditional cargo model can supply stage B onboard inventories;
the onboard sampling procedure is described below.

Every numeric parameter is a table with `value`, `unit`, and exactly one of
`source` or `assumption`. Sources identify the document, table/page or section,
public URL and date read. A number inspired by a source remains an assumption
unless that source actually measures it. UN numbers, class/division labels,
equipment identifiers and UN/LOCODEs are strings. Masses use kg and dimensions m;
nominal equipment labels retain the familiar foot designation as identifiers.
Shares count physical boxes, never TEU.

## Sampling contract

Use a local seeded random generator; record seed, profile and generator version.
Select one `rotations` row per call using its `share`. For each box select an
`equipment` row using its `share`, then empty/full using that row's
`empty_share`, and POD from the chosen rotation's `pods` shares. Rotation shares
weight calls; different call volumes can produce a different overall box mix.

The equipment categories jointly encode size and type so they cannot conflict.
The initial assumed box shares are 32% dry 20, 20% dry 40, 40% high-cube 40,
1% high-cube 45, 1% reefer 20, 4% reefer high-cube 40 and 2% high-cube flat rack
40. Thus nominal length shares are 33% / 66% / 1% for 20 / 40 / 45; 47% are
high cube, including reefer and flat rack. ISO code `45G1` means a 40-foot high
cube, not a 45-foot box; use `length_m` for geometry. All equipment has an
assumed 20% empty probability. The 5% reefer equipment share includes empties;
only the 4% of all boxes that are loaded reefers require power in this profile.

For a loaded box, draw gross mass `g` from its `loaded_weight_kg` triangular
family. This is a bounded distribution described by a minimum, most likely
value (mode), and maximum; use
[`random.Random.triangular(low, high, mode)`](https://docs.python.org/3.12/library/random.html#random.triangular).
Apply the POD multiplier to payload, preserving tare:

```text
gross = tare + (g - tare) * payload_weight_multiplier
gross = min(min(loaded_max, max_gross_kg), max(loaded_min, gross))
```

With rule-freeze-v1 off, keep kg as continuous values. With it on, D66 rounds
to whole kg within tare/plate/D60 bounds, as detailed below. Legacy empty gross
is the listed point mass, equal to tare. Size/type and
reefer affect weight through different triangular parameters. PODs Singapore
and Hong Kong multiply payload by 0.95 and 0.90; Vietnam and Port Klang by
1.05. Those correlations and the triangular families are **APPROXIMATIONS**,
not fitted trade statistics. Clipping puts some probability exactly at the
bounds, so the final distribution is not simply a scaled triangle. POD is
otherwise independent of equipment and empty state. DG, OOG and E8 do not
change weight parameters in this profile; this lack of additional dependence
is also an assumption.

Draw OOG, DG and extra E8 requests with the eligibility and ordering in
`sampling`. Their conditional probabilities have different denominators:

| Event | Conditional probability | Implied share of all boxes before feasibility conditioning |
| --- | --- | --- |
| DG | 7% of eligible loaded dry equipment | 5.208% |
| OOG | 50% of loaded compatible flat racks | 0.8% |
| Extra E8 request | 4% of loaded non-OOG boxes | 3.168% |

All probabilities above are **assumptions** or arithmetic consequences of
assumptions. Empty boxes have none of these flags. DG and E8 may overlap; OOG
has neither DG nor an extra E8 draw under this synthetic profile. E8 draws
exactly one additional request, conditional mix 35% under deck, 25% on deck,
25% away from heat, 15% top stowage. Mandatory IMDG requests are additional.
OOG dimensions extend the exterior envelope: height above the top, width on
each side, length at each end. The checker must determine actual blocked
slots and legal securing; these overhangs do not establish a placement rule.

DG samples cover all nine main classes with selected divisions. Choose the
class, then its representative UN number by the nested shares; omitted
divisions are outside this scenario's support, not measured zero traffic.
Resolve the full IMDG entry, including packing group/variant, subsidiary
hazards and explosive compatibility group, through task 1.3. If a sampled UN
or variant is unsupported, fail explicitly and report it; silently redrawing
would change the specified statistics. No UN number by itself authorises a
stowage location or establishes compliance.

| Synthetic rotation | POD box shares |
| --- | --- |
| THLCH–SGSIN–MYPKG–THLCH | SGSIN 65%, MYPKG 35% |
| THLCH–VNSGN–SGSIN–THLCH | VNSGN 55%, SGSIN 45% |
| THLCH–HKHKG–VNSGN–THLCH | HKHKG 60%, VNSGN 40% |

These are shortened invented loops inspired by public regional connectivity,
not copied schedules. POD denotes the discharge port, including a hub where
the box transfers; it is not necessarily the final inland destination.

For stage B, fix the rotation and current call occurrence, retain only POD
occurrences strictly later on that voyage, and renormalise their shares.
Generate new synthetic initial onboard boxes using that conditional population;
do not mutate real or previously generated inherited boxes. If no supported
future POD exists, this profile supplies no onboard boxes there: explicitly
reject that request or use a separately documented profile. This is an
**APPROXIMATION** of onboard composition, not a survival/age model of cargo.

Task 2.5 should report both requested and realised statistics and every
conditioning/rejection, including vessel support for 45-foot/OOG cargo,
reefer plugs, DG acceptance and incompatible special requests. Missing checker
semantics for heat/top-stowage or OOG must be reported as unsupported cases.
Never relax hard constraints or silently convert equipment/requests to fit.

## Evidence and its limits

Sources were read on **2026-10-27**, without login. No customer or company booking
records were collected. No downloaded source code was executed.

| Source | What it supports |
| --- | --- |
| [Larsen–Pacino benchmark, pinned CC0 copy](https://github.com/maritimelab/Stowage-Planning-Benckmark/tree/4b54b2100662f7d928af47b1139a914cde8f71e3/container_instances) | Deep-sea benchmark prior, not measured feeder demand. The local 27 lists contain 123,431 boxes: 40,788 nominal 20-foot and 82,643 nominal 40-foot; DC 64,508, HC 53,227, RC 574, HR 5,122. Full/empty status is absent. The lowest weight class does not identify empties. |
| [IMO, verified gross mass](https://www.imo.org/en/ourwork/safety/pages/verification-of-the-gross-mass.aspx) | SOLAS VI/2 requires VGM before loading packed containers. VGM includes contents, securing material and tare, or is obtained by weighing the whole packed container. SOLAS does not supply a cargo weight distribution, universal minimum mass, or a universal numerical error tolerance. |
| [Thai MOT / PAT monthly Laem Chabang statistics](https://datagov.mot.go.th/en/dataset/s6-5-2) | Monthly ship calls, cargo and TEU totals. The catalogue does not establish per-call POD shares or equipment/weight correlations. |
| [MPA monthly container throughput](https://data.gov.sg/datasets/d_da030f7028200d19ffcbe4a2d71af39c/view) | Whole-port TEU context under Singapore Open Data Licence; no booking-level POD mix is inferred. |
| [Hong Kong C&SD, 2022 port statistics](https://www.info.gov.hk/gia/general/202303/02/P2023030200268.htm) | Empty throughput of 3.37 million out of 16.69 million TEU is a TEU ratio for Hong Kong, not a measured empty-box ratio for Laem Chabang exports. |
| [IMO resolution MSC.556(108), IMDG 42-24](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.556(108).pdf) | Dangerous Goods List identities and primary classes/divisions; chapter 3.2, PDF pages 581–752. Sampling probabilities are synthetic. Full entry requirements remain task 1.3/checker responsibilities. |
| [Hapag-Lloyd public equipment examples](https://www.hapag-lloyd.com/en/services-information/cargo-fleet/container.html) | Selected tare and maximum gross masses; individual equipment URLs and Weight-table locators are in the TOML. The fleet page explicitly warns that specifications vary by manufacturer. These are selected equipment ratings, not one universal ISO maximum per nominal length. |
| [RCL RMB8, PDF p1](https://www-imedia.rclgroup.com/CorpWEB/PDF/SvcPtrn/20250331-RMB8.pdf), [RSK2, PDF p1](https://www-imedia.rclgroup.com/CorpWEB/PDF/SvcPtrn/RCL-ServiceMap-NorthAsiaASEAN-RSK2-20250912.pdf) | Historical regional connectivity involving Laem Chabang, Singapore, Klang, Hong Kong and Ho Chi Minh City. Shortened loops, call order and demand shares are assumptions. |

Reproduce the benchmark counts with
`UV_CACHE_DIR=/private/tmp/stowage-cargo-uv-cache uv run python experiments/cargo-params/summarize_benchmark.py`.
The script reads the inert local CC0 data and checks each file's declared row
count. The benchmark's weight field is treated as a weight-class label; its
tonne interpretation is unverified and is not used as a sourced VGM sample.

## Expert review and missing evidence

| Expert questions in `docs/domain.md` | Assumptions awaiting review |
| --- | --- |
| Q2 / E1–E2, Q11 / E6–E7 | Weight-by-equipment shapes, POD multipliers and full/empty proportions; definition of heavy and permissible ordering tolerance. These files supply mass, not an exception to weight or stability rules. |
| Q8 | Equipment proportions, export POD shares and stage B cargo composition; port aggregate volumes cannot calibrate these booking-level distributions. |
| Q9 | Port-specific DG restrictions, reefer acceptance and special-cargo handling remain unverified. |
| Q12 / E8 | Share and mix of special requests, binding top-stowage semantics and the physical meaning/distance of away-from-heat. |
| Q13 / E9 | OOG frequency, overhang distributions, slot blocking and port/ship acceptance. |

Still missing: measured intra-Asia VGM histograms and correlations, declared DG
class/UN frequencies, special-request combinations and OOG overhang data,
per-rotation booking flows, equipment-specific empty rates, and stage B survival
composition. All corresponding probabilities remain labelled assumptions.
The file does not replace container CSC plates, full IMDG entries, port rules,
vessel capacity/compatibility, reefer plugs, lashing or the feasibility checker.

Validate with
`UV_CACHE_DIR=/private/tmp/stowage-cargo-uv-cache uv run pytest tests/params -q -p no:cacheprovider`.

## Activated D66 coverage mix

`dg.pending_rule_freeze_v1` stages a synthetic demand change behind the common
catalogue activation switch. Off-state class and UN draws stay unchanged. When
on, division 1.4 has 1% of DG bookings, split equally between existing UN0336
1.4G and new UN0012 1.4S; class3 falls from 30% to 29.5%. Thus each explosives
entry has 0.5% of DG bookings, comparable to the other rare classes. This is an
**APPROXIMATION** for test coverage, not observed traffic or an asserted
representative mix. Total DG prevalence remains 5.208% before conditioning.

UN0012 uses curated DGL entry `UN0012-none`, CARTRIDGES FOR WEAPONS, INERT
PROJECTILE or CARTRIDGES, SMALL ARMS: division1.4, compatibility groupS,
stowage category01, SW1 and SP364 ([IMO MSC.556(108), DGL PDF page581](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.556(108).pdf)).
The generator retains unknown shipment assessments and does not infer an
exemption or checker clearance. At the Laem Chabang-like port these bookings
are marked direct delivery by the primary-class policy; a later confirmed
1.4S exemption will change that derived marker through the port rule.

Effective parameter resolution copies shared inputs, consumes the pending
overrides, and applies only overrides for classes retained by a custom profile.
Consumers should derive custom active scenarios from the effective parameters
before changing shares or UN choices. Fleet DoC compatibility is checked
separately from physical placement and full-checker acceptance; no evaluated
instance coverage is claimed by a raw booking draw.

## Activated VGM resolution and booking-flow audit (D66)

With `activation.rule_freeze_v1` off, the generator keeps its legacy continuous
kg values and output bytes. With activation on, round synthetic gross masses to
nearest whole kg (ties to even), bounded below by the first integer at or above
tare and above by the last integer within both the individual CSC plate and D60
cap. Reject a profile with no integer in those physical bounds. Derive D25 labels
and the exact mass category after rounding. This is synthetic declaration
resolution chosen in D66, not a claim about measured VGM accuracy. The exact-kg
`Container.category` is a strict swap-safety key; yard gate-in still uses D25.

The activated `CargoGenerationReport.requested.booking_audit` records actual
`requested` (first complete candidate per output slot), `drawn` (all candidates),
`redrawn` (replacement candidates, counted by their own identity), `refused`, and
`emitted` counts. Each stage includes primary DG class and UN histograms, reporting
mass bands, mean/p95 kg, count above 25 t and reefer/OOG/flat-rack counts by POD.
Mass bands are `<10000`, `10000<=mass<20000`, `20000<=mass<=25000`, `>25000` kg;
they are reporting bins, not ship balancing or yard clustering categories.
`demand_expectation` separately echoes configured per-class/UN shares and expected
fractional counts for an exact requested box count. TEU-only expected counts and
unmodelled analytic mass expectations are explicitly unspecified; actual sampled
stage counts remain available. On success, `realised.booking_audit` is a detached
snapshot. On failure the requested audit survives, with no returned-list emissions.
“Emitted” means the raw returned load list: L3/L4 must aggregate this audit through
their own redraw/screen loops and separately count checker-admitted instance cargo.

Use `stowage.generate.cargo.effective_cargo_generator_version()` at operation time
for instance/campaign identity and SFT admission: it is 8.0.0 on, 7.0.0 off, pinned
independently of the registry constant until the coordinated migration completes.

## Declared entry scenarios for H1 and ordinary SW19 batteries

`declared_scenarios.toml` supplies two explicitly synthetic shipment-entry
templates. `declare_entry_scenarios(entry, iso_type=..., scenarios=templates)`
is a deterministic transformation with no RNG or input mutation. Omitting
`scenarios` loads that file; passing `{}` disables declarations. Generation
calls it only after rule-freeze activation and retains its result under each
container's entry path. Each template explicitly declares `ctu_closed` and
closed packaged packing form, which must agree with the existing DG model.
The binder validates agreement and never overwrites contradictory model facts;
the helper never changes `declare_shipment` or its legacy bytes.

The H1 template declares weathertight closed packaged G/R equipment and dry
consignment packing. It supplies no inspection results, handling outcomes,
arrival condition, per-slot protection or permitted-placement list. This is an
**APPROXIMATION**, matching the assessor's sufficient declared-facts policy
pending expert validation. Actual placement and hatch coverage remain checker
inputs. Unsupported open or tank equipment receives no invented dry scenario.

The ordinary SW19 template covers sampled UN3480/UN3481 entries only, declaring
intact batteries for normal transport and explicit inapplicability of SP376
and SP377. The [primary IMDG text](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.556(108).pdf)
places both provisions on PDF773: SP376 covers damaged, defective or
undiagnosable batteries; SP377 offers disposal/recycling packing and redirects
damaged/defective batteries to SP376. This template asserts no conformity or
inspection result, no voyage classification, and no applicability conclusion
for any other candidate SP. The model's `special_provisions` remains unknown
for these entries. SW19's assessed category comes from the checker and actual
DGL entry; the generator does not relabel it.

Each template has `synthetic = true`, an `id`, `scope = "shipment_entry"`,
source citations and declaration provenance (`document`, `clause`,
`scenario_id`). Those initial IDs identify templates, not bound evidence.
Instance assembly must bind the actual shipment/entry, vessel, voyage, call
and explicit real slots through the assessor's authoritative registry. The
unbound declarations alone remain unknown to the assessor. They provide no
SW1 or SW2 geometry, sunlight facts, operational observations or acceptance.

The active cargo report exposes
`report.realised['shipment_scenarios'][container_id][entry_path]`; a detached
copy of the successfully generated manifest is retained separately from the
requested inventory. Each entry pins its authored DGL entry ID, four-digit UN
and explicit packing group (including null). Finalizing an instance does not
authorize changing that shipment identity and reusing its original declaration.

After the actual instance, vessel, rotation and any VoyageSpec are finalized,
call `bind_shipment_scenarios(instance, report)` from `stowage.generate.cargo`.
Use its returned copy before IMDG/C1 screening; neither input is mutated. It
validates entry identity and shared CTU/packing facts, scopes provenance to the
actual instance/container/entry/UN/PG/vessel/voyage/call and enumerates all
physically applicable footprints. Those slots define evidence scope, not lawful
placements. Wrong or stale scope stays unknown to the checker. Failed generation
reports cannot be bound; off-state binding returns the input unchanged. A new
call or changed finalized context needs freshly authored/bound evidence rather
than replay of old registered assessments.
