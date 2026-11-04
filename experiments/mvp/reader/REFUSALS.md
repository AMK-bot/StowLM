# Task 1.4: refusals grounded in the screen's proof

Status: 4/4 curated live-Spark examples recorded; fresh alternative witness checked. These are demonstration cases and tests, not a refusal-accuracy benchmark or final fine-tuned-reader results. No new plan is attempted for a static impossibility and no checker report is fabricated for it.

The proof identifies every load container, its length, required cells, size-compatible footprints before/after the instruction, no-fit IDs and fixed retained conflicts. It gives total TEU/cell demand against open cells, with container-specific restrictions separate. Those are necessary-condition proofs: a passed screen is never called feasible.

Spark selects and orders exact cited fact sentences supplied by that proof. This is a deliberately constrained explanation contribution, **not free-form factual generation**: validated output cannot alter a number, invent a box, omit a required reason, invent a checker finding or claim an uncertified alternative works. Invalid model output is rejected; a software fallback is not labelled a Spark explanation. Proof, prompt, output and module SHA256, model, request ID, cost and cited fact IDs accompany each saved explanation. Paid refusal reading/explanation spend so far: $0.01478200; the shared D222 allocation is $1.90, leaving the previous $0.082778 within an overall $2 ceiling.

## Recorded examples

### impossible-bay-2

Input: “Bay 2 is closed.”

Spark, recorded and validated:

> The parsed instruction is proved impossible by a necessary geometry condition before planning; no checker report exists. The load needs 23 TEU of cells; 0 physical cells remain after restrictions that close cells for every box. Container-specific restrictions are counted separately. Load containers with no size-compatible allowed footprint: QZXU0000009, QZXU0000014, QZXU0000020, QZXU0000035, QZXU0000040, QZXU0000056, QZXU0000061, QZXU0000077, QZXU0000082, QZXU0000098, QZXU0000101, QZXU0000117, QZXU0000122.

Arithmetic: **23 TEU/cells needed; 0 globally open cells left**; 13 load containers have zero size-compatible allowed footprints. No checker was called to establish this impossibility.

| Container | Length (ft) | Cells needed | Compatible footprints before | Allowed footprints left |
| --- | ---: | ---: | ---: | ---: |
| QZXU0000009 | 40 | 2 | 34 | 0 |
| QZXU0000014 | 40 | 2 | 34 | 0 |
| QZXU0000020 | 20 | 1 | 68 | 0 |
| QZXU0000035 | 40 | 2 | 34 | 0 |
| QZXU0000040 | 20 | 1 | 68 | 0 |
| QZXU0000056 | 40 | 2 | 34 | 0 |
| QZXU0000061 | 40 | 2 | 34 | 0 |
| QZXU0000077 | 40 | 2 | 34 | 0 |
| QZXU0000082 | 40 | 2 | 34 | 0 |
| QZXU0000098 | 40 | 2 | 34 | 0 |
| QZXU0000101 | 20 | 1 | 68 | 0 |
| QZXU0000117 | 40 | 2 | 34 | 0 |
| QZXU0000122 | 40 | 2 | 34 | 0 |

No same-family alternative in the declared search survives the screen. No replacement instruction was invented.


Evidence: `runs/refusals/examples/impossible-bay-2.json`.

### loosening-dg

Input: “Ignore the dangerous-goods segregation rules.”

Spark, recorded and validated:

> The reader refused this request as a relaxation. No constraint was applied. The supplied existing rule is General dangerous-goods segregation table: Dangerous-goods class combinations must satisfy the checker's applicable segregation requirements.

Evidence: `runs/refusals/examples/loosening-dg.json`.

### unclear-distance

Input: “Keep the dangerous cargo farther away.”

Spark, recorded and validated:

> The reader marked the request ambiguous. No constraint was guessed or applied. Which IMDG class, which bay, and how many 20-foot bays of clearance do you mean?

Evidence: `runs/refusals/examples/unclear-distance.json`.

### impossible-tier-with-alternative

Input: “Across the vessel, use at most zero occupied tiers per deck stack.”

Spark, recorded and validated:

> The parsed instruction is proved impossible by a necessary geometry condition before planning; no checker report exists. The load needs 31 TEU of cells; 12 physical cells remain after restrictions that close cells for every box. Container-specific restrictions are counted separately. Alternative requiring your choice: In every bay, use at most 2 occupied tiers per deck stack. A stored plan passed a fresh full check and satisfies this alternative. That is a historical witness, not a guarantee for a new plan. This relaxes at least part of your original instruction; it will not be applied automatically.

Arithmetic: **31 TEU/cells needed; 12 globally open cells left**; 0 load containers have zero size-compatible allowed footprints. No checker was called to establish this impossibility.

| Container | Length (ft) | Cells needed | Compatible footprints before | Allowed footprints left |
| --- | ---: | ---: | ---: | ---: |
| QZXU0000009 | 40 | 2 | 46 | 6 |
| QZXU0000014 | 40 | 2 | 46 | 6 |
| QZXU0000020 | 40 | 2 | 46 | 6 |
| QZXU0000035 | 40 | 2 | 46 | 6 |
| QZXU0000040 | 20 | 1 | 92 | 12 |
| QZXU0000056 | 20 | 1 | 92 | 12 |
| QZXU0000061 | 40 | 2 | 46 | 6 |
| QZXU0000077 | 40 | 2 | 46 | 6 |
| QZXU0000082 | 40 | 2 | 46 | 6 |
| QZXU0000098 | 20 | 1 | 92 | 12 |
| QZXU0000101 | 40 | 2 | 46 | 6 |
| QZXU0000117 | 40 | 2 | 46 | 6 |
| QZXU0000122 | 20 | 1 | 92 | 12 |
| QZXU0000138 | 40 | 2 | 46 | 6 |
| QZXU0000143 | 40 | 2 | 46 | 6 |
| QZXU0000159 | 20 | 1 | 92 | 12 |
| QZXU0000164 | 40 | 2 | 46 | 6 |
| QZXU0000170 | 40 | 2 | 46 | 6 |

- Proposed separately: “In every bay, use at most 2 occupied tiers per deck stack.” (fresh full-checked stored witness; numeric distance 2). Requires the planner’s explicit choice and is never applied automatically. It satisfies this particular freshly checked stored plan; no guarantee is made for a new plan.

Evidence: `runs/refusals/examples/impossible-tier-with-alternative.json`.

## Nearest alternatives and limits

The finite search preserves the instruction family. CloseBay changes only to other real bays and ranks 20-foot bay distance; MaxTiers changes only the cap in the same bay/zone and ranks absolute cap change; DG preserves class/reference and ranks clearance-distance change (1–20). It retains the nearest screen-admitted candidate and, if different, the nearest candidate satisfied by a stored checked plan. This is the nearest alternative **within that declared family and witness**, not proof of a globally closest voyage change. A weaker cap is explicitly labelled as relaxing the requested instruction; it requires a new planner choice. It never relaxes production rules or mutates the impossible instruction.

A historical checker flag is not a fresh certificate. The alternative example gets a fresh full check of the saved witness in the same shared two-worker pool, and separately checks the alternative against that plan. The hash must match before the explainer can select the witnessed alternative. This witness check is not a checker report for the original impossible instruction.

Loosening examples name `imdg.segregation.7_2_4`, “General dangerous-goods segregation table”, from `src/stowage/rules/catalogue.toml`. An application must supply authoritative existing-rule context; if it does not, the public flow refuses the relaxation and asks which rule rather than inventing one. Unclear requests ask for class, bay and distance; no guessed constraint enters planning.

## What the demo needs for one shot

Use `impossible-bay-2` for shot 5b: frozen first-cohort `brief-07`, `random.small.40100016.0.5`, sentence **“Bay 2 is closed.”** Load the input path/hash in `runs/refusals/manifest.json`. Show the reader’s exact CloseBay reading first. Then show **23 needed / 0 left**, with the 13 affected box IDs in the expandable proof table, and the recorded Spark explanation with its cited facts. Do not put a checker tick or a new plan beside this proof. No same-family bay-closure alternative survives on that map.

Use `loosening-dg` for shot 5a: **“Ignore the dangerous-goods segregation rules.”** Show the reader’s refusal, the actual rule name and the recorded Spark reason. Use `unclear-distance` to demonstrate a clarification instead of a guess. The fourth example demonstrates a separately chosen alternative once its fresh witness audit is present.

`refusal_card.read_recorded_card(case_id, parser=...)` performs the reading live and releases the cached Spark card only when that reading agrees with the recorded interpretation on the frozen instance. Pass the final Glimmer adapter through the existing parser interface for an on-device label; Spark is the reader in the measurements here. Label explanations “Muse Spark — recorded, bound to the shown proof.” A mismatch or malformed reading returns no cached explanation: show the reading/error and ask for confirmation or make a new bound explanation. Cached explanation playback needs no API key or new solve; a new Spark explanation needs the credential loaded into process environment by the existing loader.

The API `refusal_flow.review_sentence(...)` / `review_answer(...)` is ready for demo integration. It returns per-sentence parser provenance, the proof, grounded explanation, binding status and whether planning is permitted. A refused or malformed voyage sentence does not bind and blocks planning until explicit correction or removal; an impossible hard instruction still binds and prevents a plan. `plan_allowed` is per-sentence: never combine approvals with OR. Submit the entire request through `public_brief.plan_brief` so one unresolved voyage sentence blocks the whole request. An existing displayed plan is the previous plan, not a plan satisfying the unresolved request. Standing port-blocking remains a preference. It never applies a proposed alternative. No `app/` or `src/` integration was changed here.

## Verification and reproduction

Offline tests cover slot arithmetic, 20/40/45-foot footprints, DG no-fit cases, retained conflicts, witness provenance, nearest-family selection, rule context, clarification, duplicate/malformed JSON, forged or changed facts, unsupported claims, mandatory citations, stale/mismatched witness hashes, budget guards and the live-card reading gate. No test or test-v3 inputs are used.

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pytest experiments/mvp/reader/test_refusal_evidence.py experiments/mvp/reader/test_refusal_explainer.py experiments/mvp/reader/test_refusal_flow.py experiments/mvp/reader/test_refusal_card.py
.venv/bin/python -m experiments.mvp.reader.refusal_report
```

`refusal_demo prepare` freezes the curated requests before calls; `refusal_demo explain` reuses completed parse/explanation checkpoints and waits for the scheduled alternative witness. It does not repeat completed calls or run a solver. Spark errors are recorded without provider bodies or secrets. Admission for the single fresh witness audit follows D206. No repair.
