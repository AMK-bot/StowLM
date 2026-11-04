# DG context requests for current feeder canaries

`requests.json` is the machine-readable **dg-context-request-v1** manifest; it
loads seven JSON requests, covering six provisions and both SG25 target classes.
These are synthetic authored shipment/context fixtures, **pending qualification,
not evidence**. They deliberately contain no placeholder `plan` or `action`.
The production `external-pairs.json` was absent when this handoff was written;
the contract below follows the current `qualified_canary_cases._external` fields.

| Request | Driver name | Cargo | Control / exercise | Expected independent defect |
|---|---|---|---|---|
| SW1 | dg_sw1 | UN2211 III | deck0190182 /0230182 | imdg.stowage.3_2, SW1 heat clearance |
| SW2 | dg_sw2 | UN2810 III | deck0210182 /0230182 | imdg.stowage.3_2, SW2 accommodation clearance |
| H1 | dg_h1 | UN1333 II | same0050182; dry / wet packing | imdg.stowage.3_2, H1 dryness |
| SW19 | dg_sw19 | UN3480 | same0050102; intact normal / intact SP377 recycling, same non-short voyage | imdg.stowage.3_2, categoryC on-deck requirement |
| SG17 | dg_sg17 | UN1333 II + UN1017 subsidiary5.1 | onboard0050182 + deck0050482 /0050282 | imdg.segregation.7_4_3, level2 |
| SG25_2_1 | dg_sg25_2_1 | UN1333 II + UN1057 class2.1 | onboard0050102 + deck0050482 /0050282 | imdg.segregation.7_4_3, level2 |
| SG25_3 | dg_sg25_3 | UN1333 II + UN1202 III class3 | onboard0050102 + deck0050482 /0050282 | imdg.segregation.7_4_3, level2 |

The SG anchor is already onboard before the assessed candidate load. SG25's
hold anchor remains under its actual secured hatch cover; the candidate goes on
deck, so neither a source lift under a closed cover nor SG26 is introduced.
All requested positions are actual bottom decision cells of feeder_500. Their
DoC permissions, named accommodation regions and actual tank/source shapes are
retained. SG17 uses chlorine's real subsidiary5.1, avoiding a fabricated
SG53/SW11 clearance for a different class5.1 cargo. The SG25-class3 general-table
entry is X, not numeric1; the request distinguishes its independent permissive
level1 geometry comparison from the actual general table.

The reference decision booking is an ordinary loaded20ft CTU,6.058x2.438x2.591m,
12000kg with a known adequate CSC plate (tests explicitly declare30500kg).
Unknown/low plates are rejected, never filled by the materializer. Existing
vessel, fixed background, port, rotation, voyage, liquid quantities, covers,
DoC and service restrictions are not rewritten. Actual input container IDs are
reused for the authored roles; optional `role_container_ids` makes the selection
explicit. Decision cargo, its load list/yard locations and initial onboard
anchor are fixture construction inputs and must be included in conditioning.
Do not retain an earlier qualification stamp after changing them.

## Materialization before qualification

The preparation owner first retains the union of requested decision cells and
constructs current physical input/bookings. Its decision-onboard state must be
empty before this constructor authors the requested initial anchor. Then:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
UV_CACHE_DIR=$PWD/.uv-cache uv run python -m stowage.rules.imdg.canary_contexts \
  --instance /path/to/current-preparation-input.json --case SG17 \
  --output /path/to/sg17-contexts.json
```

Or import `load_context_catalog()` and
`materialize_context_pair(instance, 'SG17', role_container_ids={...})`.
The result has `baseline`/`defect` members with validated `input` Instance JSON,
requested final `placements`, candidate ID/slot and expectations. It is still
`status=context_only`, `evidence_eligible=false`: no numerical job, plan, action
or qualification is fabricated. `fixture_source_hashes` records the eight JSON
source blobs; pin these plus the materializer, actual input and final plans in
the execution epoch. Hashes inventory inputs; they do not authenticate facts or
replace the campaign's signed execution receipts.

Every authored provenance record and every actual model map/source provenance
uses the unchanged source scenario ID. The registry stores **lists of complete
records** for all relevant role contexts, including explicit known-null PG,
actual instance/vessel/voyage/call/entry and every compatible decision footprint.
There is no DoC/geometry filtering of allowed_slots: declarations are evidence
scope, not slot permission. Thus C1/C2 can consider legal alternatives instead
of declaring a bad target globally infeasible merely because other slots lack
registration. No record for one role can satisfy another role's context.

## Export to the existing external-pair contract

The owner must condition and D58-qualify the resulting real feeder slices, then
construct complete plans and their actual canonical action prefixes. Register
the seven driver names above and route them to the external loader. Export:

- `contracts[name] = "qualified-pair-v1"`;
- `pairs[name].baseline` and `.defect`, each containing actual `input`, `plan`,
  canonical `action`, `prefix_actions` and its expectation;
- the exact expected rule above, and the request's exact expected message(s) for
  checker-side provision attribution.

Those exported members must meet `validate_baseline`, plan/input identity,
physical projection and current source/component/cache pins. Run both public
full-checker members and replay both through the default production C2 mask;
retain all parts and signed execution receipts. A control must pass fully.
Report any independent failure or refusal fallout such as objective.all_loaded;
never accept unrelated/missing mask reasons or a missing-evidence error as the
requested provision witness. This handoff does not modify gate expectations.

## Declarable facts and limits

H1 declares closed/weathertight equipment and dry versus wet consignment
packing; operational precautions remain handling instructions. SW19 declares an
intact battery route, its specific SP376/377 applicability and an explicitly
synthetic non-short voyage; the recycling member additionally declares SP377
P909/mark/document compliance. These are shipper declarations, not inspection or
future transport-success outcomes. Other candidate special provisions remain
unassessed.

SW1 explicitly assumes no additional sunlight precautions for this synthetic
consignment/voyage. This is a provenance-bound applicability declaration under
the existing assessor contract, not a claim that all UN2211 shipments need none,
that a slot is shaded, that CTU closure proves protection, or that particular
weather will occur. A real shipment requires its own supported applicability
assessment. Both pair members share it, so only actual heat clearance changes.
On deck, UN2211's SW6 hold-ventilation condition is inapplicable. No H2/SW27,
SG53/SG72, shading, air-circulation or legacy protection flag is invented.
