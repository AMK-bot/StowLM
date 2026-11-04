# Compute evidence

All work was local CPU work; no GPU or paid calls. CPU/RAM figures are reservations, not measured peak RSS. Process inspection was denied. `nice` was invoked; the sandbox denied `setpriority`, so reduced OS priority was unavailable.

## Completed qualified C1+C2 epoch

Admitted physics is `2497bc54df98534644eee1b1e3da469bdda74068599f89ef076b4563354ab7ae`, background `c3-fixed-background-5`, linear model 3.4. All 2,240 specifications were processed: 595 accepted instances and 1,645 rejected requests. Verification passed and the accepted subset is released, with incomplete rung/family coverage. Rejected requests are not solver inputs.

| Job | Reserved CPUs / RAM GB | Estimated seconds | Actual seconds | Outcome |
|---|---:|---:|---:|---|
| Qualified restart cache warm | 3 / 6 | 90.0 | 34.303888167 | exit 0; whole-fleet C2 qualification complete |
| Five/profile C1+C2 pilot, cap 5 | 3 / 7 | 600.0 | 232.920209334 | exit 0; 60 requests, 18 accepted, 42 rejected; two generator workers plus writer reservation |
| Main C1+C2 inventory, cap 20 | 7 / 17 | initial 25185.99; refined 10129.138; latest 6797.89 | **5780.958** | complete in 96.35 min; six single-thread generator workers plus writer; 2240 requests, 595 accepted, 1645 rejected |
| Current release tests, scoped then whole suite | 1 / 3 | 1200 combined ledger estimate | **1399.656 combined** | both exit 0: tests/generate 194.400 s; whole pytest 1205.256 s; test CPU reservation released and history recorded |
| Final complete-inventory verification | 1 / ≤ 4 | 90.0 from five-instance benchmark with safety factor | **6.7197595** | exit 0; all 2240 requests, accepted/rejected partition, checksums, physics, capacity, splits and aggregate audits verified |
| Read-only tiny C2 diagnostic | ≤ 1 / ≤ 4 | few minutes maximum | 19.125755875 | one C1-passing candidate; independent root LP infeasible; no artifacts/source edits; see C2-YIELD.md |

The initial cold-inclusive main estimate was 25,185.99 s (about seven hours), extrapolating cap-5 exhaustion to cap 20 with contention allowance. The runner refined it to 10,129.138 s (about 2.81 hours) after measuring the first 104 main requests, and later to 6,797.89 s. Actual main wall time was **5,780.958 s (96.35 min)**. The sum of per-request generation timings was 34,728.484 s across workers; this is not elapsed wall time. Full inventory verification took **6.7197595 s** versus the 90 s estimate. The runner/parent owns completion metrics and ledger/history closure; this document does not alter that accounting.

`release-test-results.json` records scoped tests at 194.400223732 s and whole-suite tests at 1205.255965948 s, both exit 0. Their combined measured wall time is 1399.656189680 s, exceeding the combined ledger estimate of 1200 s. Five accepted-instance inspection checks passed across small, medium, full and both realistic profiles; `verification-sample-timings.json` records the preliminary benchmark. Final verification supersedes its timing extrapolation, not its audit evidence.

## Historical superseded epochs

The rows below predate the current qualification/background/source pin. They are retained as historical compute evidence and do not establish acceptance or timing for the current campaign.

| Job | Reserved CPUs / RAM GB | Estimated seconds | Actual seconds | Outcome |
|---|---:|---:|---:|---|
| Initial cache warm | 1 / 2 | 250.0 | 221.92 | exit 2: stale linear qualification |
| Five/profile C1 pilot | 1 / 3 | 600.0 | 624.32 | 60 completed; prior background epoch; prototype ZIP |
| Final cache warm | 3 / 6 | 80.0 | 72.61 | exit 2: stale linear qualification |
| Canonical attempt + validation reservation | 6 / 15 | 3700.0 | 145.92 | source pin interrupted; 307 committed shards archived |
| Balanced-background pilot | 3 / 7 | 600.0 | 142.85 | source pin interrupted; 2 committed shards archived |
| Whole pytest, first epoch | 1 / 3 | 1500.0 | 798.92 | exit 0; vessel sources changed during run |
| Fresh tests/generate | 1 / 3 | 240.0 | 183.71 | exit 1: six upstream background LP failures |
| Repeated tests/generate --lf | 1 / 3 | 240.0 | 175.20 | exit 1: same six failures; shared pytest cache caused complete scoped run |

An earlier two-shard pilot was also interrupted by a linear-source update (estimated 600 s; total wall time was not captured). Its evidence is archived in `_benchmarks/pre-active-source-pin.zip`. The real-hull diagnostic took 3.548 s against a conservative 120 s estimate (1 CPU / 2 GB).

The historical 60-shard pilot extrapolated to 9,865.56 serial seconds, or 3,699.59 seconds at four workers with a 1.5 safety factor. Those timings predate the active balancing changes and are not a current-epoch estimate. The runner owns the shared fleet ledger and completion history for the completed current jobs.
