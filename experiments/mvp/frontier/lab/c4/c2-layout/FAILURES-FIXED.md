# Cycle 4: complete fixed cohorts and failure gates

All 48 fixed TRAIN development maps are listed below. f60/f70/f80 each contain all 12 V6 metadata maps, independent of whether that constructor passed; medium12 is the fixed D185 cohort. A full pass is the final unchanged checker gate saved by `lab/score.py`. This reporter runs no checker.

V6 is a constructor built to pass, not a lift benchmark. C3 passed 8/12 f60 and 3/12 f70. Its four warm primary maps were preselected from V6-passing maps; historical results are context, not a fresh controlled comparison. Lift totals include the D15 fixed-stow, no-later-loads projection.

“Latest” uses the last supplied run per input. “Portfolio” keeps the lowest-lift full pass across supplied runs. Missing maps remain visible. Wall-clock budgets are load dependent; row workload/source information is in [REPORT.json](REPORT.json).

**Arm A `results/ladder60-70` is excluded from resource-compliant comparisons:** one fallback lacked an explicit HiGHS thread cap. A nine-thread scheduler mismatch was confirmed in the separate f80 smoke; individual A thread violations are not inferred. Its checked plans remain physical witnesses, but its timing/search arm is conservatively excluded. JSON separately counts passes excluding annotated arms. This JSON-only reporter does not independently audit resource use.

| Rung | Selected | Latest measured / replay / full | Portfolio replay / full | Mean lifts of latest passes | Mean latest wall s |
|---|---:|---:|---:|---:|---:|
| f60 | 12 | 12 / 12 / 8 | 12 / 8 | 92.25 | 69.17 |
| f70 | 12 | 12 / 12 / 4 | 12 / 4 | 112.5 | 70.41 |
| f80 | 12 | 12 / 12 / 0 | 12 / 0 | — | 92.21 |
| medium12 | 12 | 12 / 12 / 1 | 12 / 1 | 114 | 82.36 |

Codes: **S** securing; **D** draft; **T** trim; **L** list; **V** visibility; **E6** heavy-under-deck; **E9** OOG width/length clearance; **W** weather; **A** containership second check. Full IDs and measured/limit details are retained below and in JSON. A alone does not identify area-to-flood.

## Every map

| Map | Latest status | Replay / full | Latest lifts | Portfolio lifts | Closest checked failure (latest) | Source |
|---|---|---:|---:|---:|---|---|
| feeder_500-f60-high-s192200000 | passed | 1 / 1 | 71 | 71 | — | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f60-high-s192200000.json) |
| feeder_500-f60-high-s192200001 | passed | 1 / 1 | 94 | 94 | — | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f60-high-s192200001.json) |
| feeder_500-f60-high-s192200002 | passed | 1 / 1 | 70 | 70 | — | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f60-high-s192200002.json) |
| feeder_500-f60-low-s192200000 | passed | 1 / 1 | 55 | 55 | — | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f60-low-s192200000.json) |
| feeder_500-f60-low-s192200001 | passed | 1 / 1 | 62 | 62 | — | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f60-low-s192200001.json) |
| feeder_500-f60-low-s192200002 | passed | 1 / 1 | 58 | 58 | — | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f60-low-s192200002.json) |
| feeder_800-f60-high-s192200100 | failed | 1 / 0 | — | — | S, V | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-high-s192200100.json) |
| feeder_800-f60-high-s192200101 | failed | 1 / 0 | — | — | S | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-high-s192200101.json) |
| feeder_800-f60-high-s192200102 | failed | 1 / 0 | — | — | S | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-high-s192200102.json) |
| feeder_800-f60-low-s192200100 | failed | 1 / 0 | — | — | S, V | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-low-s192200100.json) |
| feeder_800-f60-low-s192200101 | passed | 1 / 1 | 106 | 106 | — | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-low-s192200101.json) |
| feeder_800-f60-low-s192200102 | passed | 1 / 1 | 222 | 222 | — | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-low-s192200102.json) |
| feeder_500-f70-high-s192200000 | passed | 1 / 1 | 121 | 121 | — | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f70-high-s192200000.json) |
| feeder_500-f70-high-s192200001 | passed | 1 / 1 | 103 | 103 | — | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f70-high-s192200001.json) |
| feeder_500-f70-high-s192200002 | passed | 1 / 1 | 91 | 91 | — | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f70-high-s192200002.json) |
| feeder_500-f70-low-s192200000 | passed | 1 / 1 | 135 | 135 | — | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f70-low-s192200000.json) |
| feeder_500-f70-low-s192200001 | failed | 1 / 0 | — | — | S | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f70-low-s192200001.json) |
| feeder_500-f70-low-s192200002 | failed | 1 / 0 | — | — | S | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f70-low-s192200002.json) |
| feeder_800-f70-high-s192200100 | failed | 1 / 0 | — | — | E9, S | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-high-s192200100.json) |
| feeder_800-f70-high-s192200101 | failed | 1 / 0 | — | — | S, V | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-high-s192200101.json) |
| feeder_800-f70-high-s192200102 | failed | 1 / 0 | — | — | S, V | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-high-s192200102.json) |
| feeder_800-f70-low-s192200100 | failed | 1 / 0 | — | — | E9, S, V | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-low-s192200100.json) |
| feeder_800-f70-low-s192200101 | failed | 1 / 0 | — | — | S, V | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-low-s192200101.json) |
| feeder_800-f70-low-s192200102 | failed | 1 / 0 | — | — | S, V | [ladder60-70-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-low-s192200102.json) |
| feeder_500-f80-high-s192200000 | failed | 1 / 0 | — | — | S, A, D, T, V | [f80-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-high-s192200000.json) |
| feeder_500-f80-high-s192200001 | failed | 1 / 0 | — | — | S, A, D, T, V | [f80-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-high-s192200001.json) |
| feeder_500-f80-high-s192200002 | failed | 1 / 0 | — | — | S, D, T, V | [f80-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-high-s192200002.json) |
| feeder_500-f80-low-s192200000 | failed | 1 / 0 | — | — | S, T, V | [f80-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-low-s192200000.json) |
| feeder_500-f80-low-s192200001 | failed | 1 / 0 | — | — | S, A, V | [f80-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-low-s192200001.json) |
| feeder_500-f80-low-s192200002 | failed | 1 / 0 | — | — | S, A, T, V | [f80-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-low-s192200002.json) |
| feeder_800-f80-high-s192200100 | failed | 1 / 0 | — | — | E9, S | [f80-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-high-s192200100.json) |
| feeder_800-f80-high-s192200101 | failed | 1 / 0 | — | — | S, L | [f80-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-high-s192200101.json) |
| feeder_800-f80-high-s192200102 | failed | 1 / 0 | — | — | S | [f80-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-high-s192200102.json) |
| feeder_800-f80-low-s192200100 | failed | 1 / 0 | — | — | S, V | [f80-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-low-s192200100.json) |
| feeder_800-f80-low-s192200101 | failed | 1 / 0 | — | — | E9, S, V | [f80-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-low-s192200101.json) |
| feeder_800-f80-low-s192200102 | failed | 1 / 0 | — | — | S, V | [f80-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-low-s192200102.json) |
| random.medium.185100200.0.2 | failed | 1 / 0 | — | — | S, D, V | [medium12-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100200.0.2.json) |
| random.medium.185100201.0.5 | failed | 1 / 0 | — | — | S, D, V | [medium12-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100201.0.5.json) |
| sim.medium.185100210 | failed | 1 / 0 | — | — | E9, S, D, V | [medium12-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100210.json) |
| sim.medium.185100211 | failed | 1 / 0 | — | — | S, D, V | [medium12-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100211.json) |
| random.medium.185100000.0.2 | passed | 1 / 1 | 114 | 114 | — | [medium12-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100000.0.2.json) |
| random.medium.185100001.0.5 | failed | 1 / 0 | — | — | S, A, D, T, V | [medium12-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100001.0.5.json) |
| sim.medium.185100010 | failed | 1 / 0 | — | — | S, A, D, T, V | [medium12-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100010.json) |
| sim.medium.185100011 | failed | 1 / 0 | — | — | E9, S, A, D, T, V | [medium12-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100011.json) |
| random.medium.185100100.0.2 | failed | 1 / 0 | — | — | S, V | [medium12-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100100.0.2.json) |
| random.medium.185100101.0.5 | failed | 1 / 0 | — | — | E9, S, D, V | [medium12-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100101.0.5.json) |
| sim.medium.185100110 | failed | 1 / 0 | — | — | E9, S, D, T, V | [medium12-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100110.json) |
| sim.medium.185100111 | failed | 1 / 0 | — | — | S | [medium12-dense](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100111.json) |

## Every failed latest outcome

No-candidate/construction outcomes and full-checker failures are separate layers. A method may full-check and discard complete trial stows before returning zero candidates; its trial failures remain real evidence about those stows. Restricted page pools, cell screens, bounded searches and timeouts never establish physical infeasibility.

### D192.medium-feeder_800-f60-high-s192200100

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].local[0].status`=local_stop; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].local[0].status`=local_stop; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 185; rules: `iacs.member_irs.container_securing`, `solas.v22.visibility`.

Largest saved securing ratio: **3.6987** (limit 1).

First saved violation per distinct rule is displayed; all 1044 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_lashing_ratio exceeds the declared synthetic securing limit. | 1.0416264378922828 | 1.0 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 540.5315196262178 | 268.88 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-high-s192200100.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-high-s192200100.json); SHA-256 `40760b4e62693eb4c14ce3a18ca28e554b4bea1fed58786cf35c1af120811dea`.

### D192.medium-feeder_800-f60-high-s192200101

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].local[0].status`=local_stop; `method_metadata.construction.attempts[0].rmp[0].status`=OPTIMAL; `method_metadata.construction.attempts[3].local[0].status`=local_stop; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 182; rules: `iacs.member_irs.container_securing`.

Largest saved securing ratio: **3.6413** (limit 1).

First saved violation per distinct rule is displayed; all 762 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_lashing_ratio exceeds the declared synthetic securing limit. | 1.031060532964806 | 1.0 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-high-s192200101.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-high-s192200101.json); SHA-256 `4a47812fe01fc779abe4a2c6593a67b5efee688f7fe9231fa30f8cde5b3f9eb4`.

### D192.medium-feeder_800-f60-high-s192200102

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].local[0].status`=improved; `method_metadata.construction.attempts[0].rmp[0].status`=OPTIMAL; `method_metadata.construction.attempts[3].local[0].status`=improved; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 194; rules: `iacs.member_irs.container_securing`.

Largest saved securing ratio: **4.3992** (limit 1).

First saved violation per distinct rule is displayed; all 868 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.1013615634651464 | 1.0 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-high-s192200102.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-high-s192200102.json); SHA-256 `9e65abba57a14c7d947276fce43f7e89884f9ce5e2e8c9a2f2497d478125dc64`.

### D192.medium-feeder_800-f60-low-s192200100

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].local[0].status`=local_stop; `method_metadata.construction.attempts[3].local[0].status`=improved; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 163; rules: `iacs.member_irs.container_securing`, `solas.v22.visibility`.

Largest saved securing ratio: **3.3916** (limit 1).

First saved violation per distinct rule is displayed; all 1438 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_fitting_ratio exceeds the declared synthetic securing limit. | 1.1707820396741027 | 1.0 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 806.4812291394035 | 268.88 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-low-s192200100.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f60-low-s192200100.json); SHA-256 `3ff695a15996af704aaf39d7e38f679047dba8c1a291d89973b031f65ce4a704`.

### D192.medium-feeder_500-f70-low-s192200001

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].local[0].status`=improved; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[1].dive.reason`=time; `method_metadata.construction.attempts[1].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[2].dive.reason`=time; `method_metadata.construction.attempts[2].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].local[0].status`=improved; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 149; rules: `iacs.member_irs.container_securing`.

Largest saved securing ratio: **2.1629** (limit 1).

First saved violation per distinct rule is displayed; all 170 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_lashing_ratio exceeds the declared synthetic securing limit. | 1.0342013832634886 | 1.0 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f70-low-s192200001.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f70-low-s192200001.json); SHA-256 `2041d9444eb99ba6a3fb65008f763a0df2532febfce9e51f924fd2e58d03340a`.

### D192.medium-feeder_500-f70-low-s192200002

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].local[0].status`=improved; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[1].dive.reason`=time; `method_metadata.construction.attempts[1].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[2].dive.reason`=time; `method_metadata.construction.attempts[2].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].local[0].status`=improved; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 143; rules: `iacs.member_irs.container_securing`.

Largest saved securing ratio: **2.3288** (limit 1).

First saved violation per distinct rule is displayed; all 276 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.0287491734096874 | 1.0 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f70-low-s192200002.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_500-f70-low-s192200002.json); SHA-256 `9ca7e52f7ee64f5d7839f532c42f0bb99c6ee351b7ebb28b094ceeed2e1c9e35`.

### D192.medium-feeder_800-f70-high-s192200100

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].local[0].status`=improved; `method_metadata.construction.attempts[3].local[0].status`=local_stop; `method_metadata.construction.attempts[3].rmp[0].status`=FEASIBLE; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 192; rules: `expert.e9.over_width_length`, `iacs.member_irs.container_securing`.

Largest saved securing ratio: **3.7505** (limit 1).

First saved violation per distinct rule is displayed; all 915 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| expert.e9.over_width_length | OOG cargo or its required clearance blocks an occupied neighbouring position. | None | None |
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.2343537605377217 | 1.0 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-high-s192200100.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-high-s192200100.json); SHA-256 `2c9c3c48ea0d901c74c5b26c07c5779e88d70224d9ce8916264a2ac1ba5ceab6`.

### D192.medium-feeder_800-f70-high-s192200101

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].local[0].status`=local_stop; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].local[0].status`=local_stop; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 221; rules: `iacs.member_irs.container_securing`, `solas.v22.visibility`.

Largest saved securing ratio: **3.6202** (limit 1).

First saved violation per distinct rule is displayed; all 1151 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_lashing_ratio exceeds the declared synthetic securing limit. | 1.0137009262454986 | 1.0 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 531.8324769217359 | 268.88 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-high-s192200101.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-high-s192200101.json); SHA-256 `f0177cb8b5067123d7a26ece8157474994df34ec75f53b64fc8ecf0443badeec`.

### D192.medium-feeder_800-f70-high-s192200102

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].local[0].status`=local_stop; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].local[0].status`=local_stop; `method_metadata.construction.attempts[3].rmp[0].status`=FEASIBLE; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 207; rules: `iacs.member_irs.container_securing`, `solas.v22.visibility`.

Largest saved securing ratio: **3.7408** (limit 1).

First saved violation per distinct rule is displayed; all 1571 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_corner_tension_ratio exceeds the declared synthetic securing limit. | 1.1075958039095253 | 1.0 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 631.0545271225108 | 268.88 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-high-s192200102.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-high-s192200102.json); SHA-256 `7b10d50316a5de91c4d0c75a0a0caaf0e66f9d581e654d5867838eefcfdf3e96`.

### D192.medium-feeder_800-f70-low-s192200100

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 136; rules: `expert.e9.over_width_length`, `iacs.member_irs.container_securing`, `solas.v22.visibility`.

Largest saved securing ratio: **3.1426** (limit 1).

First saved violation per distinct rule is displayed; all 1500 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| expert.e9.over_width_length | OOG cargo or its required clearance blocks an occupied neighbouring position. | None | None |
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.5296889322466256 | 1.0 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 659.0512582440999 | 268.88 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-low-s192200100.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-low-s192200100.json); SHA-256 `0b09702c8878e2f216f0e5a3d27c6c8f9e27beece276ba967b399cec492b8a77`.

### D192.medium-feeder_800-f70-low-s192200101

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].local[0].status`=improved; `method_metadata.construction.attempts[3].local[0].status`=local_stop; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 211; rules: `iacs.member_irs.container_securing`, `solas.v22.visibility`.

Largest saved securing ratio: **3.4146** (limit 1).

First saved violation per distinct rule is displayed; all 1343 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.63380045762851 | 1.0 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 490.5080582599469 | 268.88 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-low-s192200101.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-low-s192200101.json); SHA-256 `4a2b693800868755c854c46f4e358c9e267e577e9c8d186909770b8c3aa9df28`.

### D192.medium-feeder_800-f70-low-s192200102

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[3].local[0].status`=local_stop; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 211; rules: `iacs.member_irs.container_securing`, `solas.v22.visibility`.

Largest saved securing ratio: **3.1313** (limit 1).

First saved violation per distinct rule is displayed; all 1501 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_corner_tension_ratio exceeds the declared synthetic securing limit. | 1.5814628370109736 | 1.0 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 755.7315014108233 | 268.88 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-low-s192200102.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense/D192.medium-feeder_800-f70-low-s192200102.json); SHA-256 `ce430d1bc583d33569cf5f0c9720b4eea3d31c6c7cd7d936acb17e91548409e5`.

### D192.medium-feeder_500-f80-high-s192200000

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].dive.reason`=time; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 161; rules: `iacs.member_irs.container_securing`, `iscode.b2_3.containership_second_check`, `iscode.loading.draft`, `iscode.loading.trim`, `solas.v22.visibility`.

Largest saved securing ratio: **1.9497** (limit 1).

First saved violation per distinct rule is displayed; all 327 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.2186679476646116 | 1.0 |
| iscode.b2_3.containership_second_check | [straits:call-0:THLCH:departure departure] Containership second-check minimum not met | 0.05391657304501041 | 0.05632223410135405 |
| iscode.loading.draft | [straits:call-0:THLCH:departure departure] draught_fwd_m exceeds permissible loading limit | 7.727863516825534 | 7.05 |
| iscode.loading.trim | [straits:call-0:THLCH:departure departure] trim_min_m exceeds permissible loading limit | -1.799442148329506 | -1.078 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 270.36578916746913 | 232.8 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-high-s192200000.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-high-s192200000.json); SHA-256 `221d0f18e0901ce66a3054ea12d80a451c8a309dc3672571be9029d5d4187ca4`.

### D192.medium-feeder_500-f80-high-s192200001

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].dive.reason`=time; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 129; rules: `iacs.member_irs.container_securing`, `iscode.b2_3.containership_second_check`, `iscode.loading.draft`, `iscode.loading.trim`, `solas.v22.visibility`.

Largest saved securing ratio: **2.0417** (limit 1).

First saved violation per distinct rule is displayed; all 324 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.2419831169126427 | 1.0 |
| iscode.b2_3.containership_second_check | [straits:call-0:THLCH:departure departure] Containership second-check minimum not met | 0.2733092067964997 | 0.2737596249608297 |
| iscode.loading.draft | [straits:call-0:THLCH:departure departure] draught_fwd_m exceeds permissible loading limit | 7.4489272126394654 | 7.05 |
| iscode.loading.trim | [straits:call-0:THLCH:departure departure] trim_min_m exceeds permissible loading limit | -1.3447352736875935 | -1.078 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 429.5937896986709 | 232.8 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-high-s192200001.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-high-s192200001.json); SHA-256 `4e5cdc7fc1230c588d5bbf985fed3b1dc3b87c73c7497c36ee3cd4ba18c9ddfd`.

### D192.medium-feeder_500-f80-high-s192200002

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].dive.reason`=time; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[2].dive.reason`=time; `method_metadata.construction.attempts[2].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 141; rules: `iacs.member_irs.container_securing`, `iscode.loading.draft`, `iscode.loading.trim`, `solas.v22.visibility`.

Largest saved securing ratio: **2.0930** (limit 1).

First saved violation per distinct rule is displayed; all 396 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.345947130115091 | 1.0 |
| iscode.loading.draft | [straits:call-0:THLCH:departure departure] draught_fwd_m exceeds permissible loading limit | 7.296112228521414 | 7.05 |
| iscode.loading.trim | [straits:call-0:THLCH:departure departure] trim_min_m exceeds permissible loading limit | -1.1333054391372812 | -1.078 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 456.5423574786046 | 232.8 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-high-s192200002.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-high-s192200002.json); SHA-256 `ab3f30c89103c9b586b134f8f750926eaf3eb4e86171e61c8942f1ccf13a5e59`.

### D192.medium-feeder_500-f80-low-s192200000

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].dive.reason`=time; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[1].dive.reason`=time; `method_metadata.construction.attempts[1].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[2].dive.reason`=time; `method_metadata.construction.attempts[2].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 151; rules: `iacs.member_irs.container_securing`, `iscode.loading.trim`, `solas.v22.visibility`.

Largest saved securing ratio: **2.9155** (limit 1).

First saved violation per distinct rule is displayed; all 540 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_corner_tension_ratio exceeds the declared synthetic securing limit. | 1.3793847205623306 | 1.0 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 534.7004509213372 | 232.8 |
| iscode.loading.trim | [straits:call-1:SGSIN:arrival arrival] trim_min_m exceeds permissible loading limit | -1.178857876702855 | -1.078 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-low-s192200000.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-low-s192200000.json); SHA-256 `747c0a83e83d3c18ef61082b28286342d3ab28867cdfb95851da6e46adb9e53b`.

### D192.medium-feeder_500-f80-low-s192200001

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].dive.reason`=time; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[1].dive.reason`=time; `method_metadata.construction.attempts[1].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[2].dive.reason`=time; `method_metadata.construction.attempts[2].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 167; rules: `iacs.member_irs.container_securing`, `iscode.b2_3.containership_second_check`, `solas.v22.visibility`.

Largest saved securing ratio: **2.6386** (limit 1).

First saved violation per distinct rule is displayed; all 598 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.324346732543868 | 1.0 |
| iscode.b2_3.containership_second_check | [straits:call-0:THLCH:departure departure] Containership second-check minimum not met | 0.29559244738224544 | 0.30335428919345897 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 616.7409636071504 | 232.8 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-low-s192200001.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-low-s192200001.json); SHA-256 `f1ad2e700379cb04cc8dddf467f1775ee7dd548b92e813ce2be8f7d7d5dd2b74`.

### D192.medium-feeder_500-f80-low-s192200002

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].dive.reason`=time; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[1].dive.reason`=time; `method_metadata.construction.attempts[1].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[2].dive.reason`=time; `method_metadata.construction.attempts[2].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 165; rules: `iacs.member_irs.container_securing`, `iscode.b2_3.containership_second_check`, `iscode.loading.trim`, `solas.v22.visibility`.

Largest saved securing ratio: **2.5472** (limit 1).

First saved violation per distinct rule is displayed; all 554 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.2318274148729211 | 1.0 |
| iscode.loading.trim | [straits:call-0:THLCH:departure:mid_voyage mid_voyage] trim_min_m exceeds permissible loading limit | -1.2928418357896039 | -1.078 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 435.7991809047406 | 232.8 |
| iscode.b2_3.containership_second_check | [straits:call-1:SGSIN:arrival arrival] Containership second-check minimum not met | 0.2860919623597588 | 0.31611669681384363 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-low-s192200002.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_500-f80-low-s192200002.json); SHA-256 `c7b41f1493394e55653f16998f4e428df7a7ef31306ff14683b1315847703c97`.

### D192.medium-feeder_800-f80-high-s192200100

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].rmp[0].status`=FEASIBLE; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 231; rules: `expert.e9.over_width_length`, `iacs.member_irs.container_securing`.

Largest saved securing ratio: **3.9723** (limit 1).

First saved violation per distinct rule is displayed; all 1062 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| expert.e9.over_width_length | OOG cargo or its required clearance blocks an occupied neighbouring position. | None | None |
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_lashing_ratio exceeds the declared synthetic securing limit. | 1.0933693682123795 | 1.0 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-high-s192200100.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-high-s192200100.json); SHA-256 `db3a688ba4918251650505137de3a309a070135fa55c5ca05a16a143ab908a43`.

### D192.medium-feeder_800-f80-high-s192200101

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].local[0].status`=local_stop; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].local[0].status`=local_stop; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 248; rules: `iacs.member_irs.container_securing`, `iscode.loading.list`.

Largest saved securing ratio: **3.4997** (limit 1).

First saved violation per distinct rule is displayed; all 988 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_lashing_ratio exceeds the declared synthetic securing limit. | 1.0291762367592658 | 1.0 |
| iscode.loading.list | [straits:call-0:THLCH:departure:mid_voyage mid_voyage] list_rad exceeds permissible loading limit | 0.036370196946058655 | 0.03490658503988659 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-high-s192200101.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-high-s192200101.json); SHA-256 `5de49468c723c6a1dd676e84b284e17b46518e27cde92a8dfc48ff6f05013517`.

### D192.medium-feeder_800-f80-high-s192200102

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].local[0].status`=improved; `method_metadata.construction.attempts[3].local[0].status`=local_stop; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 239; rules: `iacs.member_irs.container_securing`.

Largest saved securing ratio: **3.7292** (limit 1).

First saved violation per distinct rule is displayed; all 1118 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.0036615683092145 | 1.0 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-high-s192200102.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-high-s192200102.json); SHA-256 `2d84269d94c725b6cc1091d486eb01140ad4425b7d454961478ad289afb7e2fa`.

### D192.medium-feeder_800-f80-low-s192200100

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].dive.reason`=time; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 241; rules: `iacs.member_irs.container_securing`, `solas.v22.visibility`.

Largest saved securing ratio: **3.6998** (limit 1).

First saved violation per distinct rule is displayed; all 1748 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.1344922109701896 | 1.0 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 729.005395245712 | 268.88 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-low-s192200100.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-low-s192200100.json); SHA-256 `146b24e970c40184f5f358c52c9ca5281f3e4d44fc30d739b38f109b55739de8`.

### D192.medium-feeder_800-f80-low-s192200101

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[3].local[0].status`=improved; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 256; rules: `expert.e9.over_width_length`, `iacs.member_irs.container_securing`, `solas.v22.visibility`.

Largest saved securing ratio: **3.4466** (limit 1).

First saved violation per distinct rule is displayed; all 1534 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| expert.e9.over_width_length | OOG cargo or its required clearance blocks an occupied neighbouring position. | None | None |
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.7492724192323428 | 1.0 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 560.7144271422765 | 268.88 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-low-s192200101.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-low-s192200101.json); SHA-256 `97d6f7ec05e2969b33ea07beb5daf76e8087674ac014187427a3b4dadffcf638`.

### D192.medium-feeder_800-f80-low-s192200102

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 291; rules: `iacs.member_irs.container_securing`, `solas.v22.visibility`.

Largest saved securing ratio: **3.3616** (limit 1).

First saved violation per distinct rule is displayed; all 1720 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_corner_tension_ratio exceeds the declared synthetic securing limit. | 1.0090397464217995 | 1.0 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 555.9866758978559 | 268.88 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-low-s192200102.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense/D192.medium-feeder_800-f80-low-s192200102.json); SHA-256 `1cf1f3022cf0f91b00726d6b82fc116f74ef9309bd5630a7a9e848d0b18e0c1b`.

### random.medium.185100200.0.2

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 336; rules: `iacs.member_irs.container_securing`, `iscode.loading.draft`, `solas.v22.visibility`.

Largest saved securing ratio: **5.4164** (limit 1).

First saved violation per distinct rule is displayed; all 4101 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.8000115504389664 | 1.0 |
| iscode.loading.draft | [straits:call-0:THLCH:departure departure] draught_fwd_m exceeds permissible loading limit | 9.704550153488023 | 9.52 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | inf | 343.98 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100200.0.2.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100200.0.2.json); SHA-256 `63d508846239579cc2583c38a4edbc03618cd5034dca528fb16514ce3c74f6b1`.

### random.medium.185100201.0.5

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 458; rules: `iacs.member_irs.container_securing`, `iscode.loading.draft`, `solas.v22.visibility`.

Largest saved securing ratio: **5.6405** (limit 1).

First saved violation per distinct rule is displayed; all 4277 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:SGSIN:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.8508575721221545 | 1.0 |
| iscode.loading.draft | [straits:call-0:SGSIN:departure departure] draught_fwd_m exceeds permissible loading limit | 9.702589459609007 | 9.52 |
| solas.v22.visibility | [straits:call-0:SGSIN:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | inf | 343.98 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100201.0.5.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100201.0.5.json); SHA-256 `301bffb16ec1a063fc64f4a48a8259d98f1b9e623f266acead6935be4c100a48`.

### sim.medium.185100210

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 477; rules: `expert.e9.over_width_length`, `iacs.member_irs.container_securing`, `iscode.loading.draft`, `solas.v22.visibility`.

Largest saved securing ratio: **5.6806** (limit 1).

First saved violation per distinct rule is displayed; all 3448 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| expert.e9.over_width_length | OOG cargo or its required clearance blocks an occupied neighbouring position. | None | None |
| iacs.member_irs.container_securing | [voyage:vietnam_straits:call:0:THLCH:departure:0b9cb15f7cb71f05e726fbc1cc04d6a057ae9b9828833c6f7621b9269a3f4d6f departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.7881408613922125 | 1.0 |
| iscode.loading.draft | [voyage:vietnam_straits:call:0:THLCH:departure:0b9cb15f7cb71f05e726fbc1cc04d6a057ae9b9828833c6f7621b9269a3f4d6f departure] draught_fwd_m exceeds permissible loading limit | 9.646686963996844 | 9.52 |
| solas.v22.visibility | [voyage:vietnam_straits:call:0:THLCH:departure:0b9cb15f7cb71f05e726fbc1cc04d6a057ae9b9828833c6f7621b9269a3f4d6f departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | inf | 343.98 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100210.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100210.json); SHA-256 `95cacfaa4c1eccba743189a18399842d9d1a4d9b6237bbc8087e8130931e4b71`.

### sim.medium.185100211

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 356; rules: `iacs.member_irs.container_securing`, `iscode.loading.draft`, `solas.v22.visibility`.

Largest saved securing ratio: **5.4730** (limit 1).

First saved violation per distinct rule is displayed; all 4376 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [voyage:south_china_vietnam:call:0:SGSIN:departure:7e710fbaba709bfbf1ea786615085e00fba88430488e9ea94d823d408c6f94c7 departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.6837168207696382 | 1.0 |
| iscode.loading.draft | [voyage:south_china_vietnam:call:0:SGSIN:departure:7e710fbaba709bfbf1ea786615085e00fba88430488e9ea94d823d408c6f94c7 departure] draught_fwd_m exceeds permissible loading limit | 9.86694979792383 | 9.52 |
| solas.v22.visibility | [voyage:south_china_vietnam:call:0:SGSIN:departure:7e710fbaba709bfbf1ea786615085e00fba88430488e9ea94d823d408c6f94c7 departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | inf | 343.98 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100211.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100211.json); SHA-256 `665e7d47156f150258834dc713b9697cb12af9da250c4edc190bbea7b0f144a9`.

### random.medium.185100001.0.5

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].dive.reason`=time; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 141; rules: `iacs.member_irs.container_securing`, `iscode.b2_3.containership_second_check`, `iscode.loading.draft`, `iscode.loading.trim`, `solas.v22.visibility`.

Largest saved securing ratio: **1.8361** (limit 1).

First saved violation per distinct rule is displayed; all 304 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:SGSIN:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.2330948404931876 | 1.0 |
| iscode.b2_3.containership_second_check | [straits:call-0:SGSIN:departure departure] Containership second-check minimum not met | 0.2343300569281941 | 0.27336887521555225 |
| iscode.loading.draft | [straits:call-0:SGSIN:departure departure] draught_fwd_m exceeds permissible loading limit | 7.522030837785184 | 7.05 |
| iscode.loading.trim | [straits:call-0:SGSIN:departure departure] trim_min_m exceeds permissible loading limit | -1.348783495156141 | -1.078 |
| solas.v22.visibility | [straits:call-0:SGSIN:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 346.66047668298654 | 232.8 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100001.0.5.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100001.0.5.json); SHA-256 `7988df28c0e9a7876261de5ccb2215df10c57146c5c81484f4474835869c856a`.

### sim.medium.185100010

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].dive.reason`=time; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 139; rules: `iacs.member_irs.container_securing`, `iscode.b2_3.containership_second_check`, `iscode.loading.draft`, `iscode.loading.trim`, `solas.v22.visibility`.

Largest saved securing ratio: **2.4738** (limit 1).

First saved violation per distinct rule is displayed; all 414 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [voyage:south_china_vietnam:call:0:THLCH:departure:48c1d4412abcb1ed2403d20fe601ed421ac85d967ffa7986d0fdf8a6ea2ce632 departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.2157604625401817 | 1.0 |
| iscode.b2_3.containership_second_check | [voyage:south_china_vietnam:call:0:THLCH:departure:48c1d4412abcb1ed2403d20fe601ed421ac85d967ffa7986d0fdf8a6ea2ce632 departure] Containership second-check minimum not met | 0.25782010347141676 | 0.28020349182138465 |
| iscode.loading.draft | [voyage:south_china_vietnam:call:0:THLCH:departure:48c1d4412abcb1ed2403d20fe601ed421ac85d967ffa7986d0fdf8a6ea2ce632 departure] draught_fwd_m exceeds permissible loading limit | 7.195394511220752 | 7.05 |
| iscode.loading.trim | [voyage:south_china_vietnam:call:0:THLCH:departure:48c1d4412abcb1ed2403d20fe601ed421ac85d967ffa7986d0fdf8a6ea2ce632:mid_voyage mid_voyage] trim_min_m exceeds permissible loading limit | -1.230733681758693 | -1.078 |
| solas.v22.visibility | [voyage:south_china_vietnam:call:0:THLCH:departure:48c1d4412abcb1ed2403d20fe601ed421ac85d967ffa7986d0fdf8a6ea2ce632 departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 287.50332576593013 | 232.8 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100010.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100010.json); SHA-256 `d805f5fa8d3175617420fae899e7ede8987922b568652be12bc0c40ab3b1b9f7`.

### sim.medium.185100011

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].dive.reason`=time; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 104; rules: `expert.e9.over_width_length`, `iacs.member_irs.container_securing`, `iscode.b2_3.containership_second_check`, `iscode.loading.draft`, `iscode.loading.trim`, `solas.v22.visibility`.

Largest saved securing ratio: **1.7305** (limit 1).

First saved violation per distinct rule is displayed; all 324 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| expert.e9.over_width_length | OOG cargo or its required clearance blocks an occupied neighbouring position. | None | None |
| iacs.member_irs.container_securing | [voyage:straits:call:0:SGSIN:departure:2ed6109ff1470c05bbf95a3806806df6f39c6839d0efc7084e7cea545979eb59 departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.1426614486979962 | 1.0 |
| iscode.b2_3.containership_second_check | [voyage:straits:call:0:SGSIN:departure:2ed6109ff1470c05bbf95a3806806df6f39c6839d0efc7084e7cea545979eb59 departure] Containership second-check minimum not met | 0.23664677427877806 | 0.280109094206482 |
| iscode.loading.draft | [voyage:straits:call:0:SGSIN:departure:2ed6109ff1470c05bbf95a3806806df6f39c6839d0efc7084e7cea545979eb59 departure] draught_fwd_m exceeds permissible loading limit | 7.275317860344955 | 7.05 |
| iscode.loading.trim | [voyage:straits:call:0:SGSIN:departure:2ed6109ff1470c05bbf95a3806806df6f39c6839d0efc7084e7cea545979eb59 departure] trim_min_m exceeds permissible loading limit | -1.1035625934766493 | -1.078 |
| solas.v22.visibility | [voyage:straits:call:0:SGSIN:departure:2ed6109ff1470c05bbf95a3806806df6f39c6839d0efc7084e7cea545979eb59 departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 460.6079970886569 | 232.8 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100011.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100011.json); SHA-256 `3899d18a6fd930b010fa603f38fb536918ed66c743e0389efebb3c4ff2499e7e`.

### random.medium.185100100.0.2

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].dive.reason`=time; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 200; rules: `iacs.member_irs.container_securing`, `solas.v22.visibility`.

Largest saved securing ratio: **3.5441** (limit 1).

First saved violation per distinct rule is displayed; all 1168 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [straits:call-0:THLCH:departure departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.3137540596263941 | 1.0 |
| solas.v22.visibility | [straits:call-0:THLCH:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 533.3993608938831 | 268.88 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100100.0.2.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100100.0.2.json); SHA-256 `2a49d97fd325a7ae261dc2f42041457166b3d457ddc360dcdc463c9a94e5ece2`.

### random.medium.185100101.0.5

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].dive.reason`=time; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 259; rules: `expert.e9.over_width_length`, `iacs.member_irs.container_securing`, `iscode.loading.draft`, `solas.v22.visibility`.

Largest saved securing ratio: **3.7983** (limit 1).

First saved violation per distinct rule is displayed; all 1234 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| expert.e9.over_width_length | OOG cargo or its required clearance blocks an occupied neighbouring position. | None | None |
| iacs.member_irs.container_securing | [straits:call-0:SGSIN:departure departure] max_lashing_ratio exceeds the declared synthetic securing limit. | 1.1215156873837515 | 1.0 |
| iscode.loading.draft | [straits:call-0:SGSIN:departure departure] draught_fwd_m exceeds permissible loading limit | 8.716625947218295 | 8.7 |
| solas.v22.visibility | [straits:call-0:SGSIN:departure departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 478.0538186784711 | 268.88 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100101.0.5.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/random.medium.185100101.0.5.json); SHA-256 `7878c3aca9dc07a0ff14611adf08a32ddc7847e7bb078a7489ea016e068ffd6e`.

### sim.medium.185100110

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].dive.reason`=time; `method_metadata.construction.attempts[0].rmp[0].status`=UNKNOWN; `method_metadata.construction.attempts[3].dive.reason`=time; `method_metadata.construction.attempts[3].rmp[0].status`=UNKNOWN; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 239; rules: `expert.e9.over_width_length`, `iacs.member_irs.container_securing`, `iscode.loading.draft`, `iscode.loading.trim`, `solas.v22.visibility`.

Largest saved securing ratio: **3.5823** (limit 1).

First saved violation per distinct rule is displayed; all 1694 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| expert.e9.over_width_length | OOG cargo or its required clearance blocks an occupied neighbouring position. | None | None |
| iacs.member_irs.container_securing | [voyage:south_china_vietnam:call:0:THLCH:departure:a53e2c2190469c72adf464c6a17d021b3e1791345ef42ff93e1354918ca9053f departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.785408101273802 | 1.0 |
| iscode.loading.draft | [voyage:south_china_vietnam:call:0:THLCH:departure:a53e2c2190469c72adf464c6a17d021b3e1791345ef42ff93e1354918ca9053f departure] draught_fwd_m exceeds permissible loading limit | 8.793586640110586 | 8.7 |
| solas.v22.visibility | [voyage:south_china_vietnam:call:0:THLCH:departure:a53e2c2190469c72adf464c6a17d021b3e1791345ef42ff93e1354918ca9053f departure] SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow. | 513.5877369951322 | 268.88 |
| iscode.loading.trim | [voyage:south_china_vietnam:call:1:HKHKG:arrival:c14df577b84af82b4b5ae02c3f095d989456f4473587dcffc42078bd3249bd75 arrival] trim_min_m exceeds permissible loading limit | -1.2449057658776637 | -1.2441 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100110.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100110.json); SHA-256 `795d3a3f1b8ca0009a746e601428181bc0e9395c886e8d2460b9e25da0e4aab5`.

### sim.medium.185100111

Outcome: **failed**; layer: `finishing_or_candidate_rejection`. Replay observed: **True**. finisher returned no plan

Construction/solver reasons: `method_metadata.construction.attempts[0].local[0].status`=improved; `method_metadata.construction.attempts[0].rmp[0].status`=OPTIMAL; `method_metadata.construction.attempts[0].rmp[1].status`=OPTIMAL; `method_metadata.construction.attempts[3].local[0].status`=improved; `runner.failure_reason`=finisher returned no plan; `runner.tries[0].failure_reason`=finisher returned no plan; `runner.tries[1].failure_reason`=finisher returned no plan.

Closest checked trial: `method_full_checker`; lifts 216; rules: `iacs.member_irs.container_securing`.

Largest saved securing ratio: **3.8685** (limit 1).

First saved violation per distinct rule is displayed; all 1034 violations remain in REPORT.json and the evidence row.

| Rule | Message | Measured | Limit |
|---|---|---|---|
| iacs.member_irs.container_securing | [voyage:vietnam_straits:call:0:SGSIN:departure:5a0cc9ea2d16a684c10357e9d66d2f4e747adfd7e69fbf82936ce82c57f43b29 departure] max_racking_ratio exceeds the declared synthetic securing limit. | 1.0766639048482334 | 1.0 |

Evidence: [experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100111.json](/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense/sim.medium.185100111.json); SHA-256 `9fbbd3eabb4198f5082d5e2c388e0ff5429f1516dde030fe5ab0a260d26edb6b`.

## Primary lift bounds

| Map | C3 lifts | Current supplied-run best | Parts | Lower bound | Gap |
|---|---:|---:|---|---:|---:|
| feeder_500-f60-high-s192200000 | 71 | 71 | {'hatch_later': 12, 'hatch_now': 12, 'relocations': 47, 'restow_later': 0, 'restow_now': 0} | 65 | 6 |
| feeder_800-f60-low-s192200101 | 106 | 106 | {'hatch_later': 12, 'hatch_now': 10, 'relocations': 84, 'restow_later': 0, 'restow_now': 0} | 90 | 16 |
| feeder_500-f60-high-s192200002 | 70 | 70 | {'hatch_later': 12, 'hatch_now': 12, 'relocations': 46, 'restow_later': 0, 'restow_now': 0} | 70 | 0 |
| feeder_500-f60-low-s192200000 | 55 | 55 | {'hatch_later': 4, 'hatch_now': 4, 'relocations': 47, 'restow_later': 0, 'restow_now': 0} | 51 | 4 |

Any zero gap refers to fewest lifts among checker-passing plans under the D15 fixed-stow, no-later-loads projection. A bound solver’s own optimal status refers to its relaxation. Necessary physics rows may leave the bound unchanged; the reporter takes only saved lower-bound certificates, never an incumbent objective as a bound.
