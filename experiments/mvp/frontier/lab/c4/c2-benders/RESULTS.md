# Initial frozen medium12 cohort — complete

All 12 approved TRAIN development maps resolved. **12/12 maps have complete replay-legal stows (27 distinct constructor stows); 0/12 full-checker passes; 26 fixed-stow securing certificates.** Passing mean lifts are unavailable. The remaining strengthened first-map stow and its repaired descendants remain unknown; no whole-map or whole-family infeasibility proof was obtained.

Mean map wall time: **412.2221 s**; sum of map walls: **4946.6655 s**. The sum is not batch elapsed time because the remaining eleven maps ran with two admitted workers. Profile: seed 0, construction 300 s, finalize request 1140 s within a shared cooperative 1200 s map allocation. One numerical thread per worker; wall budgets are load-dependent. Earlier adaptive first-map attempts and later corrected profiles are excluded.

| Approved map / seed | Loaded / requested | Complete constructor stows | Full-checker pass | Binding failure / certificate | Wall s | Passing lifts |
|---|---:|---:|---|---|---:|---:|
| `random.medium.185100000.0.2` | 121 / 121 | 2 | No (0; rejected) | Arrival `containership_area_0_flood_mrad`: −0.006907757 m·rad | 1061.3 | — (no pass) |
| `random.medium.185100001.0.5` | 121 / 121 | 3 | No (0; not run) | 3 fixed-stow securing proofs; rod lower bounds 1.088627–1.118699 | 301.1 | — (no pass) |
| `sim.medium.185100010` | 121 / 121 | 3 | No (0; not run) | 3 fixed-stow securing proofs; rod lower bounds 1.128684–1.179785 | 301.2 | — (no pass) |
| `sim.medium.185100011` | 121 / 121 | 3 | No (0; not run) | 3 fixed-stow securing proofs; rod lower bounds 1.100586–1.128757 | 301.1 | — (no pass) |
| `random.medium.185100100.0.2` | 229 / 229 | 2 | No (0; not run) | 2 fixed-stow securing proofs; rod lower bounds 1.254510–1.589553 | 301.2 | — (no pass) |
| `random.medium.185100101.0.5` | 229 / 229 | 2 | No (0; not run) | 2 fixed-stow securing proofs; rod lower bounds 1.293654–1.390579 | 300.4 | — (no pass) |
| `sim.medium.185100110` | 229 / 229 | 2 | No (0; not run) | 2 fixed-stow securing proofs; rod lower bounds 1.340341–1.492655 | 301.2 | — (no pass) |
| `sim.medium.185100111` | 229 / 229 | 2 | No (0; not run) | 2 fixed-stow securing proofs; rod lower bounds 1.394000–1.676634 | 301.4 | — (no pass) |
| `random.medium.185100200.0.2` | 428 / 428 | 2 | No (0; not run) | 2 fixed-stow securing proofs; rod lower bounds 1.890552–1.962960 | 302.3 | — (no pass) |
| `random.medium.185100201.0.5` | 428 / 428 | 2 | No (0; not run) | 2 fixed-stow securing proofs; rod lower bounds 1.838520–1.993337 | 298.3 | — (no pass) |
| `sim.medium.185100210` | 428 / 428 | 2 | No (0; not run) | 2 fixed-stow securing proofs; rod lower bounds 1.730255–1.981882 | 582.9 | — (no pass) |
| `sim.medium.185100211` | 428 / 428 | 2 | No (0; not run) | 2 fixed-stow securing proofs; rod lower bounds 1.832004–1.986198 | 594.2 | — (no pass) |

Every fixed-stow certificate rejects only its unchanged cargo arrangement for all tank fills; another arrangement on that map remains unknown. The eleven proof-only maps ran zero numerical ballast finishes and returned no plan for the runner’s full check. These are not eleven failed full-checker evaluations. The first map’s repaired candidate ran all 23 checker parts without errors and failed only arrival area: 0.2853143784 measured versus 0.2922221349 m·rad required. Securing/loading pass that report. Its 116 diagnostic lifts are excluded from passing statistics; lifts count D15 projected later-port work for a fixed stow with no later loads.

Construction is cooperative, not a hard stage cap: sim210 used 580.766887 s and sim211 592.189198 s against 300 s requested. Actual wall times above are untruncated. Native balancing/imports/opaque calls can overrun a sub-deadline. No map exceeded the 1200 s shared allocation in this cohort.

## Preserved evidence

`results/frozen-medium12-01/` contains byte-identical copies of all twelve raw score rows, `manifest.json` with original paths and hashes, and `audit.json` with per-map input/plan coverage, frozen source checks, worker PIDs, binding rules, times and passing-lift gates. Original artifacts were not moved. All 21 root Python files—including tests and `audit.py`—are preserved under `results/frozen-cohort-source-01/`; its manifest binds this initial profile even after current code changes. Every row’s implementation/imported-source endpoint checks match. The sole checked candidate’s native canonical input/plan bindings, protected cargo context and tank vector match its saved plan/result. This consolidation used standard-library JSON/hash inspection and added no numerical checks.

## Fresh E9 reference — separate

Fresh `c1/leader/method_e9_val.py` (0.9.0 plus authorized E9 mask fix), seed 0: capped 120 s search, 300 s V4 finishing, three tries. All twelve approved rows resolve as `no_candidates`, returning zero candidates to the shared runner and zero passes. This does not rule out complete or checked internal attempts that the proposal routine discarded.

| Approved map / seed | Returned candidates | Full-checker passes | Wall s |
|---|---:|---:|---:|
| `random.medium.185100000.0.2` | 0 | 0 | 69.8 |
| `random.medium.185100001.0.5` | 0 | 0 | 58.5 |
| `sim.medium.185100010` | 0 | 0 | 67.9 |
| `sim.medium.185100011` | 0 | 0 | 65.5 |
| `random.medium.185100100.0.2` | 0 | 0 | 524.1 |
| `random.medium.185100101.0.5` | 0 | 0 | 530.5 |
| `sim.medium.185100110` | 0 | 0 | 106.6 |
| `sim.medium.185100111` | 0 | 0 | 516.7 |
| `random.medium.185100200.0.2` | 0 | 0 | 172.9 |
| `random.medium.185100201.0.5` | 0 | 0 | 564.6 |
| `sim.medium.185100210` | 0 | 0 | 168.5 |
| `sim.medium.185100211` | 0 | 0 | 176.8 |

Fresh-reference mean map wall: **251.8736 s**; 0/12 passes, no passing lift mean. Its independent audit is `results/fresh-e9-medium12-01/independent-json-audit.json`. Allocations differ and matched machine load is unverified: no speed claim, paired passing lift change or stored-row comparison is made. With zero reference passes there are no mutual passing lift pairs. E9 effects are bug-fix effects, not solver gains.

## Separate warm archived-witness result

`results/warm-restows-01-independent/` independently accepts the explicitly warm plan: **121/121 boxes, 100 projected lifts, zero violations, 25.9362 s**. Count: 24 relocations + 12 current hatch + 44 later restow + 20 later hatch lifts. All 23 parts ran without errors/stubs; all 319 native Python files stayed unchanged, and canonical report/input bindings match. This begins from an archived C3 accepted witness and contributes **zero cold passes**. Its 100 lifts are not a paired cold/reference result.

## Later profiles

The root plans a separate corrected cold attempt with coordinate-consistent fresh C1 longitudinal targets and TCG search/repair changes. Such results require a fresh output/source identity and are excluded from every initial-cohort count and timing above. They do not overwrite the frozen evidence.

The corrected source profile passed static review: its C1 target uses the current-run plain stow's first-support-cell coordinate at an explicit zero offset; repair removes the obsolete old-tank TCG band and still hands candidates to native rebalancing/exact checking. The ballast stencil and linear TCG rows guard all three voyage phases against the absolute-TCG cusp while retaining directions that leave zero TCG unchanged. Native root nonconvergence pins a direction. No archive input or pass-gate change was found. That source review preceded the separate first-map attempt below. Frozen initial totals and evidence above remain unchanged.

## Corrected first-map profile 01 — separate failure

`results/corrected-first-01` resolved in **1006.3782 s**, with 2 complete replay-legal constructor stows, one fixed-stow securing rejection and **0/1 full-checker passes**. Actual propose time was 267.7362 s; finishing plus the independent runner check took 738.4056 s. Both saved constructor plans contain all 121 requested boxes exactly once. Independent standard-library geometry aggregation confirmed the new target 60.4094185035 m equals this run's plain first-support-cell LCG, while its physical mean is separately 58.5524042177 m. Construction provenance is in `independent-construction-audit.json`.

| Map | Loaded / requested | Complete constructor stows | Full-checker pass | Binding failure | Wall s | Passing lifts |
|---|---:|---:|---|---|---:|---:|
| `random.medium.185100000.0.2` | 121 / 121 | 2 | No (0; rejected) | Arrival `containership_area_0_flood_mrad`: −0.016167056 m·rad | 1006.4 | — (failed) |

The repaired final candidate has 118 diagnostic projected lifts, excluded from passing statistics. All 23 native parts ran without errors. The sole hard failure is arrival area 0.2795420957 against 0.2957091515 m·rad required; securing/loading pass (worst rod ratio 0.999528033). The independent runner rejects the same second-check rule. Finishing used 75 exact physics evaluations, 450 stability/lashing part calls, 16 SLP iterations and 96 recorded TCG stencils. The strengthened stow and its family remain unknown, not proved infeasible.

`independent-final-audit.json` verifies the approved input, constructor/final plan hashes and coverage, canonical native input/plan report bindings, final tank map/protected cargo identity, unchanged source endpoints and all 23 preserved Python files in `results/corrected-profile-source-01/`. The snapshot declares the root test suite passed; the reviewer neither reran tests nor infers an independently bound test count from that flag. This JSON audit added no numerical check. A subsequent consistent-TCG-projection profile is planned under a new source identity; it is excluded from both this first-map attempt and the initial twelve-map cohort. The independently accepted warm 100-lift witness remains separate.

## Projected profile — separate first-map failure

The projection patch passed static review: stage initial/FD/trial evaluations use the actual native-balanced vector, with effective projected directions used consistently in LP capacities and trial generation. Accepted/local/global/protected records retain the same cargo, tank vector and exact report; all-phase branch guards and ordinary full checks remain. Its source is separately frozen at `results/projected-profile-source-01/`; the initial-cohort and corrected-first01 source identities and counts are unchanged.

| Map | Loaded / requested | Complete constructor stows | Full-checker pass | Binding failure | Wall s | Passing lifts |
|---|---:|---:|---|---|---:|---:|
| `random.medium.185100000.0.2` | 121 / 121 | 2 | No (0; rejected) | Arrival `containership_area_0_flood_mrad`: −0.006299729 m·rad | 1111.4 | — (failed) |

`results/projected-first-01` completed in **1111.4097 s**: propose 265.8622 s, finishing plus independent check 845.3364 s, worker PID 89490. It emitted the same two constructor plans byte-for-byte as corrected-first01, with one fixed-plain-stow certificate and one strengthened unknown. The final repaired candidate has 112 diagnostic lifts (24 relocations + 12 current hatch + 54 later restow + 22 later hatch), excluded from passing statistics. All 23 parts ran without errors. The sole hard failure is arrival area 0.284925904362 against 0.291225632914 m·rad required; securing/loading pass, with worst rod ratio 0.994792647. The shared runner independently rejects the same area rule.

The run used 88 physics evaluations, 520 native stability/lashing part calls and 12 SLP iterations. Projection restored direction 0: 12 selected stencils, 11 completed valid full-native differences and one unavailable trial. Directions 0–4 have respectively 11, 11, 11, 11 and 10 completed valid differences; direction 5 is projected-null ten times. There are 90 distinct cached projection inputs across the three stages and 80 full-sample projection records. The best exact raw TCG is 2.30–2.47e-9 m across all phases. These counters exclude some auxiliary loading/root work; they are not a bound on all hydrostatic queries.

`independent-final-audit.json` verifies approved input and constructor/final plan hashes/coverage, native canonical report bindings, protected cargo/tank identity, all 23 snapshot source files and unchanged source endpoints. It also records FD/projection telemetry and the exact failed metric. The audit used only standard-library artifact inspection; it added no checker run. **No cold goal was achieved.** This failure remains unknown physical feasibility, not a certificate against the strengthened stow or its family.

## Final scope and open issues

| Experiment scope | Maps with returned complete stows | Full-checker passes | Wall time | Passing lifts |
|---|---:|---:|---|---:|
| Initial frozen cold `medium12` | 12 / 12 (27 constructor stows) | 0 / 12 | 412.2221 s mean map wall | unavailable |
| Fresh E9 reference `medium12` | 0 returned to runner / 12 | 0 / 12 | 251.8736 s mean map wall | unavailable |
| Corrected cold first-map profile | 1 / 1 (2 constructor stows) | 0 / 1 | 1006.3782 s | unavailable |
| Projected cold first-map profile | 1 / 1 (2 constructor stows) | 0 / 1 | 1111.4097 s | unavailable |
| Archived-witness warm refinement, independently rechecked | 1 / 1 | 1 / 1 | 25.9362 s independent recheck only | 100 |

The rows have different budgets, search histories and unverified machine-load matching; this is a status table, not a speed or paired-lift comparison. The warm timing is verification only, not its search cost. All maps are TRAIN development data. No cold passing lift mean or cold full-checker pass was obtained.

Open issues are physical and constructive: arrival positive righting-area remains below its native minimum while securing is protected; none of the local LP/projection failures is an outward infeasibility proof. The 26 initial-cohort certificates apply only to their fixed stows, leaving alternative cargo patterns undecided. A cold algorithm must produce a different viable cargo/tank combination or a valid broader family proof. Cooperative construction overshoots also remain a timing limitation. The warm accepted 100-lift witness proves that this particular development map admits a checker-passing plan, while its archived start prevents counting it as cold construction.
