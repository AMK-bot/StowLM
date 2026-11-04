# D220: a required reader before every brief plan

Status: complete; validation only, first 30 small maps in ascending stored `val-0.9` result filename order (the 55-map validation cohort), with no success or violation filtering. Spark is the active reader; the Glimmer adapter is a strict, provenance-recording plug-in seam, unavailable until attached. These are **not fine-tuned-reader results**. D219's gates are unchanged.

| Arm | Instructions broken among returned plans | Full-checker / parsed-constraint passes | Checked intended-instruction passes |
| --- | ---: | ---: | ---: |
| Stored Frontier 0.9, without reader | 19/30 | 30/30 | 11/30 |
| Same standing brief + one voyage instruction, Spark reader | 0/14 | 14/30 | 14/30 |

A missing plan is a failure and has no instruction-compliance success. The broken-instruction denominator counts returned plans; pass denominators remain all 30, including static impossibilities and failures. Fresh audit attempts: 30/30; completed without audit error: 30/30. Returned plans with unassessed instruction compliance: 0. Exact voyage parses: 30/30 audited. Confirmed binding replans (also exact intended parse, violating baseline, changed container plan): 5/30.

Mean move cost of obeying, on 14 paired checked compliant plans: +2.143 weighted nonproductive moves versus each stored unconstrained plan. This includes instructions already obeyed without the reader; it is not a claim that the reader saves moves. Mean pipeline wall time: 230.2 s on 28 known timings; 2 interrupted attempts have unknown planner wall time and are excluded from this mean; excludes the independent fresh audit. Spark measured spend: $0.08277800 (dedicated restart-safe allocation ≤ $2).

## Fixed cohort and static screens

`runs/brief/cohort.json` and `preregister.json` freeze the seed, map order, one sentence and independent expected constraint per map before paid parsing or planning. No map or instruction is replaced after a failed screen. Types cycle CloseBay, MaxTiers, DgKeepAway (10 each); values derive from ship geometry/cargo, with a recorded fixed class-3 assumption when a DG instruction has no matching cargo. Static-impossibility rows: 14/30. Vacuous DG restrictions: 4/30 maps (4/10 DG cases); they remain in the headline denominator and are not evidence of meaningful DG restriction handling. The screen is a sufficient proof only, not a feasibility oracle.

| Static-screen stratum | Maps | Stored baseline breaks | Reader checked compliant passes |
| --- | ---: | ---: | ---: |
| Proven impossible | 14 | 14/14 assessed | 0/14 |
| No static impossibility proof | 16 | 5/16 assessed | 14/16 |

On the 14 compliant paired plans, stored/new mean move costs are 14.286/16.429. On the 5 compliant pairs whose baseline broke the instruction, mean additional cost is +6.000 moves. 

| Instruction family | Maps | Static impossible | Vacuous | Fresh compliant passes |
| --- | ---: | ---: | ---: | ---: |
| close_bay | 10 | 10 | 0 | 0/10 |
| max_tiers | 10 | 0 | 0 | 9/10 |
| dg_keep_away | 10 | 4 | 4 | 5/10 |

All statically impossible rows remain below. In particular, this cohort supplies no successful CloseBay evidence when every CloseBay row is statically impossible.

- brief-01 (random.small.40100000.0.2): [{"containers": ["QZXU0000014", "QZXU0000035", "QZXU0000056", "QZXU0000061", "QZXU0000098", "QZXU0000117", "QZXU0000122"], "proof": "no_size_compatible_allowed_footprint"}]
- brief-04 (random.small.40100008.0.8): [{"containers": ["QZXU0000014", "QZXU0000020", "QZXU0000056", "QZXU0000077", "QZXU0000082", "QZXU0000098", "QZXU0000101", "QZXU0000117", "QZXU0000122"], "proof": "no_size_compatible_allowed_footprint"}]
- brief-07 (random.small.40100016.0.5): [{"detail": "the load list needs 23 TEU of cells but only 0 remain open (68 closed by the instruction)", "proof": "open_cell_capacity"}, {"containers": ["QZXU0000009", "QZXU0000014", "QZXU0000020", "QZXU0000035", "QZXU0000040", "QZXU0000056", "QZXU0000061", "QZXU0000077", "QZXU0000082", "QZXU0000098", "QZXU0000101", "QZXU0000117", "QZXU0000122"], "proof": "no_size_compatible_allowed_footprint"}]
- brief-10 (random.small.40100023.0.8): [{"containers": ["QZXU0000009", "QZXU0000014", "QZXU0000035", "QZXU0000040", "QZXU0000056", "QZXU0000061", "QZXU0000077", "QZXU0000082", "QZXU0000098", "QZXU0000117", "QZXU0000122", "QZXU0000138", "QZXU0000143", "QZXU0000164"], "proof": "no_size_compatible_allowed_footprint"}]
- brief-12 (random.small.40100028.0.5): [{"containers": ["QZXU0000098", "QZXU0000122"], "proof": "no_size_compatible_allowed_footprint"}]
- brief-13 (random.small.40100032.0.8): [{"detail": "the load list needs 22 TEU of cells but only 0 remain open (68 closed by the instruction)", "proof": "open_cell_capacity"}, {"containers": ["QZXU0000009", "QZXU0000014", "QZXU0000020", "QZXU0000035", "QZXU0000040", "QZXU0000056", "QZXU0000061", "QZXU0000077", "QZXU0000082", "QZXU0000098", "QZXU0000101", "QZXU0000117", "QZXU0000122"], "proof": "no_size_compatible_allowed_footprint"}]
- brief-16 (random.small.40100039.0.2): [{"containers": ["QZXU0000009", "QZXU0000014", "QZXU0000020", "QZXU0000035", "QZXU0000040", "QZXU0000056", "QZXU0000077", "QZXU0000098", "QZXU0000122", "QZXU0000159", "QZXU0000164"], "proof": "no_size_compatible_allowed_footprint"}]
- brief-19 (random.small.40100048.0.2): [{"containers": ["QZXU0000009", "QZXU0000020", "QZXU0000040", "QZXU0000056", "QZXU0000061", "QZXU0000077", "QZXU0000098", "QZXU0000117", "QZXU0000122"], "proof": "no_size_compatible_allowed_footprint"}]
- brief-21 (random.small.41100004.0.2): [{"containers": ["QZXU0000061"], "proof": "no_size_compatible_allowed_footprint"}]
- brief-22 (random.small.41100008.0.5): [{"detail": "the load list needs 36 TEU of cells but only 0 remain open (116 closed by the instruction)", "proof": "open_cell_capacity"}, {"containers": ["QZXU0000009", "QZXU0000014", "QZXU0000020", "QZXU0000035", "QZXU0000040", "QZXU0000056", "QZXU0000061", "QZXU0000077", "QZXU0000082", "QZXU0000098", "QZXU0000101", "QZXU0000117", "QZXU0000122", "QZXU0000138", "QZXU0000143", "QZXU0000159", "QZXU0000164", "QZXU0000170", "QZXU0000185", "QZXU0000190", "QZXU0000204", "QZXU0000210"], "proof": "no_size_compatible_allowed_footprint"}]
- brief-24 (random.small.41100014.0.8): [{"containers": ["QZXU0000035"], "proof": "no_size_compatible_allowed_footprint"}]
- brief-25 (random.small.41100018.0.2): [{"detail": "the load list needs 35 TEU of cells but only 0 remain open (116 closed by the instruction)", "proof": "open_cell_capacity"}, {"containers": ["QZXU0000009", "QZXU0000014", "QZXU0000020", "QZXU0000035", "QZXU0000040", "QZXU0000056", "QZXU0000061", "QZXU0000077", "QZXU0000082", "QZXU0000098", "QZXU0000101", "QZXU0000117", "QZXU0000122", "QZXU0000138", "QZXU0000143", "QZXU0000159", "QZXU0000164", "QZXU0000170", "QZXU0000185", "QZXU0000190", "QZXU0000204", "QZXU0000210"], "proof": "no_size_compatible_allowed_footprint"}]
- brief-28 (sim.small.40100005): [{"containers": ["QZXU0000009", "QZXU0000014", "QZXU0000020", "QZXU0000035", "QZXU0000040", "QZXU0000056", "QZXU0000061", "QZXU0000077", "QZXU0000082", "QZXU0000101", "QZXU0000122", "QZXU0000138", "QZXU0000143", "QZXU0000164", "QZXU0000170"], "proof": "no_size_compatible_allowed_footprint"}]
- brief-30 (sim.small.40100010): [{"containers": ["QZXU0000020"], "proof": "no_size_compatible_allowed_footprint"}]

## Pipeline and semantics

`brief_planner.plan_brief(instance, VoyageBrief(standing, voyage), parser=...)` always invokes the reader, including an empty brief. Each newline in the standing part is a separate sentence; voyage instructions are explicit sentence items. The common standing text is “One port per hatch.” Its accepted parse enables the port-blocked start without restricting other starts or imposing a cover allocation. Accepted voyage constraints intersect and bind construction, master search, scored candidates, final occupancy and finishing. Malformed or refused sentences are visible in per-sentence evidence and do not bind; no software guess or correction replaces their parse. The independent intended label never enters the planner.

Each accepted constraint retains the source sentence, parser/model and adapter SHA256 (null for Spark) in evidence. `pipeline.GlimmerParser(generate=callback, adapter_sha256=...)` takes the adapter's untouched JSON through the same strict schema/context validator. It has no Spark fallback. This report makes no claim that the standing preference needs language understanding or that a fine-tune beats Spark.

Every returned plan goes through Frontier 0.9 → finisher → full checker, then a fresh process checks the saved baseline and saved new plan, parsed semantics and independent intended semantics. Parsed-constraint necessary capacity/footprint screens and fixed retained conflicts may prove impossibility before search; these remain failures in the 30-map denominator. No repair or unconstrained fallback. The old single-sentence API still reads before planning; use the brief API for D220 standing/voyage semantics.

## Standing-versus-empty ablation

Stored standing-rule evidence: 29/30 maps have a passing blocked_finish; 29/30 have a passing twostep_finish. On 28 paired maps, blocked construction costs 14.68 vs 24.04 weighted nonproductive moves for two-step (mean saving 9.36). These are archived checker results, not fresh audits or an exact standing-versus-empty brief ablation. The stored portfolio always enabled the blocked construction. Removing its record afterward cannot reproduce an empty brief: it can change the fallback, reference ballast for the sight gate, candidate ranking, and search time shares. Run paired standing="One port per hatch." versus standing="" on these same maps, holding voyage instructions, parser, seed, worker count and budgets fixed; finish and freshly audit both arms without repair.

## Per-map fresh audit

| Map | Instruction | Static impossible | Before breaks | With reader pass | Obeys instruction | Δ moves | Pipeline seconds |
| --- | --- | --- | --- | --- | --- | ---: | ---: |
| random.small.40100000.0.2 | Do not load any containers in bay 01. | True | True | False | no plan | — | 3.3 |
| random.small.40100003.0.2 | In bay 02, use at most 4 occupied tiers per deck stack. | False | False | True | True | 0.0 | 348.1 |
| random.small.40100004.0.5 | Keep all IMDG class 3 containers at least 1 20-foot bays away from bay 02. | False | False | True | True | 0.0 | 424.7 |
| random.small.40100008.0.8 | Do not load any containers in bay 03. | True | True | False | no plan | — | 3.3 |
| random.small.40100011.0.8 | In bay 01, use at most 4 occupied tiers per deck stack. | False | False | False | no plan | — | 2.2 |
| random.small.40100012.0.2 | Keep all IMDG class 3 containers at least 1 20-foot bays away from bay 03. | False | False | False | no plan | — | 3.6 |
| random.small.40100016.0.5 | Do not load any containers in bay 02. | True | True | False | no plan | — | 3.2 |
| random.small.40100019.0.5 | In bay 01, use at most 2 occupied tiers per hold stack. | False | True | True | True | 8.0 | 397.4 |
| random.small.40100020.0.8 | Keep all IMDG class 3 containers at least 1 20-foot bays away from bay 02. | False | False | True | True | 0.0 | 470.7 |
| random.small.40100023.0.8 | Do not load any containers in bay 01. | True | True | False | no plan | — | 3.1 |
| random.small.40100024.0.2 | In bay 01, use at most 4 occupied tiers per deck stack. | False | False | True | True | 0.0 | 407.9 |
| random.small.40100028.0.5 | Keep all IMDG class 3 containers at least 1 20-foot bays away from bay 03. | True | True | False | no plan | — | 3.3 |
| random.small.40100032.0.8 | Do not load any containers in bay 02. | True | True | False | no plan | — | 6.6 |
| random.small.40100035.0.8 | In bay 01, use at most 2 occupied tiers per hold stack. | False | True | True | True | 14.0 | 404.8 |
| random.small.40100036.0.2 | Keep all IMDG class 3 containers at least 1 20-foot bays away from bay 03. | False | True | True | True | 0.0 | 477.1 |
| random.small.40100039.0.2 | Do not load any containers in bay 03. | True | True | False | no plan | — | 3.4 |
| random.small.40100043.0.5 | In bay 03, use at most 2 occupied tiers per hold stack. | False | True | True | True | 8.0 | 545.3 |
| random.small.40100047.0.8 | Keep all IMDG class 8 containers at least 1 20-foot bays away from bay 03. | False | True | True | True | 0.0 | 397.2 |
| random.small.40100048.0.2 | Do not load any containers in bay 03. | True | True | False | no plan | — | 3.7 |
| random.small.41100002.0.2 | In bay 01, use at most 5 occupied tiers per deck stack. | False | False | True | True | 0.0 | 500.1 |
| random.small.41100004.0.2 | Keep all IMDG class 2.2 containers at least 1 20-foot bays away from bay 01. | True | True | False | no plan | — | 4.5 |
| random.small.41100008.0.5 | Do not load any containers in bay 02. | True | True | False | no plan | — | 3.4 |
| random.small.41100010.0.5 | In bay 01, use at most 5 occupied tiers per deck stack. | False | False | True | True | 0.0 | 434.4 |
| random.small.41100014.0.8 | Keep all IMDG class 3 containers at least 1 20-foot bays away from bay 01. | True | True | False | no plan | — | 3.4 |
| random.small.41100018.0.2 | Do not load any containers in bay 02. | True | True | False | no plan | — | 3.0 |
| sim.small.40100001 | In bay 01, use at most 4 occupied tiers per deck stack. | False | False | True | True | 0.0 | 540.8 |
| sim.small.40100002 | Keep all IMDG class 3 containers at least 1 20-foot bays away from bay 02. | False | False | True | True | 0.0 | 641.9 |
| sim.small.40100005 | Do not load any containers in bay 01. | True | True | False | no plan | — | 2.8 |
| sim.small.40100009 | In bay 02, use at most 4 occupied tiers per deck stack. | False | False | True | True | 0.0 | 406.8 |
| sim.small.40100010 | Keep all IMDG class 3 containers at least 1 20-foot bays away from bay 03. | True | True | False | no plan | — | 2.5 |

## Reproduction and limits

From the repository root, with the existing offline environment:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -m experiments.mvp.reader.brief_cohort
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -m experiments.mvp.reader.run_brief prepare
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -m experiments.mvp.reader.run_brief run
.venv/bin/python -m experiments.mvp.reader.brief_report
```

The running job refreshes this report through `watch_brief.py`; `runs/brief/completion.json` records terminal evidence verification. Checkpoints do not repeat completed or claimed planner attempts. Admission before each new solve/audit uses `experiments/fleet/core-state.json`: mask bit-count ≥ 8, load1 ≤ 45, age in [0,120) seconds. There are at most two subprocess workers, one native solver worker each; admission evidence and recorded PIDs accompany rows. Search budgets match each stored baseline, finishing is 300 seconds per attempt and construction 120 seconds. Timed search on a shared machine means this is instruction compliance versus stored plans, not a matched performance or causal move-saving experiment. Only validation inputs were used; no test or test-v3 inputs were opened. Credentials come from the external parent Projects `.env` into process environment only. No key is in arguments or output.

## First-cohort execution interruption

The original foreground controller stopped between turns. brief-05, brief-06 had admitted claims but no saved result. Their claims remain, they were not rerun, and they count as failures. Original instructions, cohort/preregistration hashes, parses, solver/search settings and completed plans were preserved. This is an execution limitation in addition to the 14 statically impossible instructions; it is not evidence of instruction infeasibility. Recovery provenance is `runs/brief-followup/interruption.json`. A detached controller continues the other maps.

# D222: second cohort selected by a declared static screen

Status: complete. Same 30 maps and order; seed 22220261031. This is **selection by a stated necessary-condition screen**, not an unselected feasibility rate. The first cohort above remains the original draw; its 14/30 impossibility count is itself a finding. The complete candidate universe and draw were frozen in `runs/brief2/cohort.json` / `preregister.json` before paid reading or planning. No stored plan outcome entered selection.

1,336 declared candidates, 1,104 not proved impossible. The seeded draw contains 0 CloseBay, 25 MaxTiers and 5 DG instructions. Type balance is constrained by what survives the screen: all bay closures fail on these maps, so no CloseBay was substituted. Selected static impossibilities: 0/30. Maps with no admissible instruction: 0/30. Physical-cap/no-op fallbacks: 0/30. DG instructions with no matching active load or retained cargo: **0** in this second cohort, versus **4** in the first. Passing a static screen still requires construction, finishing and a full checker; no feasibility claim follows from admission.

| Cohort / arm | Instructions broken / assessed returned plans | Checked passes / all maps | Checked intended-compliant passes / all maps |
| --- | ---: | ---: | ---: |
| First, without reader | 19/30 | 30/30 | 11/30 |
| First, with Spark reader | 0/14 | 14/30 | 14/30 |
| Second, without reader | 17/30 | 30/30 | 13/30 |
| Second, with Spark reader | 0/27 | 27/30 | 27/30 |

Second-cohort saved planner outcomes: 30/30; fresh audits completed without audit error: 30/30. Exact voyage parses: 30/30 audited. Mean extra weighted nonproductive moves on 27 paired compliant plans: 0.25925925925925924. Mean pipeline wall time: 454.9 seconds; independent audits excluded. Measured second-cohort parsing spend: $0.07715665. No plan is repaired or retried. Spark is the reader in both rows; these are not final fine-tuned-reader results, and the checker does not detect a language misreading.

| Map | Drawn instruction | Stored baseline breaks | Reader checked pass | Intended instruction obeyed | Δ moves | Pipeline seconds |
| --- | --- | --- | --- | --- | ---: | ---: |
| random.small.40100000.0.2 | Across the vessel, use at most 2 occupied tiers per deck stack. | False | True | True | 0.0 | 399.5 |
| random.small.40100003.0.2 | Keep all IMDG class 8 containers at least 1 20-foot bays away from bay 01. | True | True | True | 0.0 | 382.2 |
| random.small.40100004.0.5 | In bay 03, use at most 3 occupied tiers per deck stack. | False | True | True | 0.0 | 386.5 |
| random.small.40100008.0.8 | In bay 02, use at most 2 occupied tiers per deck stack. | True | True | True | 0.0 | 735.5 |
| random.small.40100011.0.8 | Across the vessel, use at most 3 occupied tiers per deck stack. | False | True | True | 0.0 | 483.6 |
| random.small.40100012.0.2 | Across the vessel, use at most 0 occupied tiers per hold stack. | True | False | no plan | — | 68.5 |
| random.small.40100016.0.5 | Across the vessel, use at most 3 occupied tiers per deck stack. | False | True | True | 0.0 | 463.9 |
| random.small.40100019.0.5 | In bay 03, use at most 2 occupied tiers per deck stack. | True | True | True | 2.0 | 400.6 |
| random.small.40100020.0.8 | In bay 01, use at most 1 occupied tiers per deck stack. | True | True | True | 0.0 | 543.5 |
| random.small.40100023.0.8 | In bay 03, use at most 2 occupied tiers per deck stack. | True | True | True | 0.0 | 498.4 |
| random.small.40100024.0.2 | Across the vessel, use at most 4 occupied tiers per deck stack. | False | True | True | 0.0 | 456.4 |
| random.small.40100028.0.5 | Across the vessel, use at most 2 occupied tiers per deck stack. | False | True | True | 0.0 | 455.8 |
| random.small.40100032.0.8 | Keep all IMDG class 3 containers at least 1 20-foot bays away from bay 01. | True | True | True | 0.0 | 554.2 |
| random.small.40100035.0.8 | Across the vessel, use at most 3 occupied tiers per deck stack. | False | True | True | 0.0 | 361.4 |
| random.small.40100036.0.2 | Keep all IMDG class 3 containers at least 1 20-foot bays away from bay 03. | True | True | True | -2.0 | 604.0 |
| random.small.40100039.0.2 | Across the vessel, use at most 4 occupied tiers per deck stack. | False | True | True | 0.0 | 399.7 |
| random.small.40100043.0.5 | In bay 03, use at most 1 occupied tiers per deck stack. | True | True | True | 0.0 | 440.6 |
| random.small.40100047.0.8 | Keep all IMDG class 8 containers at least 1 20-foot bays away from bay 03. | True | True | True | 0.0 | 376.7 |
| random.small.40100048.0.2 | Across the vessel, use at most 3 occupied tiers per deck stack. | False | True | True | 0.0 | 594.0 |
| random.small.41100002.0.2 | Across the vessel, use at most 1 occupied tiers per hold stack. | True | True | True | 0.0 | 597.6 |
| random.small.41100004.0.2 | In bay 01, use at most 2 occupied tiers per hold stack. | True | True | True | 2.0 | 379.1 |
| random.small.41100008.0.5 | Keep all IMDG class 3 containers at least 1 20-foot bays away from bay 01. | False | True | True | 0.0 | 528.2 |
| random.small.41100010.0.5 | Across the vessel, use at most 2 occupied tiers per hold stack. | True | True | True | 0.0 | 469.9 |
| random.small.41100014.0.8 | Across the vessel, use at most 2 occupied tiers per deck stack. | False | True | True | 0.0 | 461.5 |
| random.small.41100018.0.2 | In bay 02, use at most 2 occupied tiers per hold stack. | True | True | True | 0.0 | 591.0 |
| sim.small.40100001 | In bay 03, use at most 1 occupied tiers per deck stack. | True | False | no plan | — | 262.5 |
| sim.small.40100002 | In bay 01, use at most 4 occupied tiers per deck stack. | False | True | True | 0.0 | 548.0 |
| sim.small.40100005 | In bay 01, use at most 0 occupied tiers per deck stack. | True | False | no plan | — | 248.3 |
| sim.small.40100009 | In bay 03, use at most 3 occupied tiers per deck stack. | False | True | True | 1.0 | 444.6 |
| sim.small.40100010 | In bay 02, use at most 1 occupied tiers per deck stack. | True | True | True | 4.0 | 510.1 |

Both cohorts share a single detached controller with at most two native-one map/audit workers and D206 admission at every new job. See `runs/brief-followup/controller.json`, `run.log` and terminal `completion.json`. Refusal explanations use separate proof evidence in `REFUSALS.md`; no checker report is fabricated for a preplanning refusal.
