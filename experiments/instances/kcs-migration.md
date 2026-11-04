# Task 3.5: KCS / D20–D25 migration

Generator **1.1.0** selects physical regions from surviving KCS-based geometry.
Tiny/small use the first mixed hold/deck pair (005/007 on feeder_500); medium
uses the central half of bay pairs; full retains the whole vessel. Shared slice
accounting retains whole-ship physics and fixed loads, with DG Document of
Compliance references clipped to retained stacks. Full demand is now **260**
boxes, replacing 280; this is an explicit size-specification change.

Tiny retains four hold and two deck cells, six targets and one hatch. Its dry20
conditioning preserves the task 2.5 loaded/empty mix and DG sampling. Targets
keep task 2.5's D25 weight/category semantics; synthetic later-cohort background
uses catalogue weight classification. Family identity keys and SplitMembership's
canonical key selection are unchanged; generator version is 1.1.0.

The executive verified full-physics generation, clean provenance and save/load
round-trips at seed 1:

| Rung | Physical cells | Targets | Candidate draws | Cached elapsed seconds |
|---|---:|---:|---:|---:|
| tiny | 6 | 6 | 1 | 0.068 |
| small | 76 | 36 | 1 | 0.053 |
| medium | 244 | 150 | 34 | 0.267 |
| full | 440 | 260 | 1 | 0.073 |

Times exclude cold vessel-physics setup. Medium's single-instance rejection rate
is 33/34 (97.1%); this small sample does not estimate true feasibility. Screening
only tests necessary conditions; checker acceptance remains separate.

Additional geometry diagnostics over seeds 0–9 completed without generation
failures in **1.215 s**:

| Rung | Accepted | Total candidate draws | Screening rate |
|---|---:|---:|---:|
| tiny | 10 | 10 | 0% |
| small | 10 | 10 | 0% |
| medium | 10 | 81 | 87.65% |
| full | 10 | 12 | 16.67% |

Medium draw counts were 8, 34, 3, 2, 6, 3, 5, 4, 3, 13. Full required three
draws for seed 0 and one each for seeds 1–9. These are geometry diagnostics,
not additional full-physics or checker acceptance measurements.

Reported verification command: **151 tests passed, exit 0**, including structural
and split-identity checks for all three fleet vessels across all four rungs:

`UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run pytest -q tests/generate tests/yard`

**Owner action required:** task 4.3's
`instances_v2_random.select_tiny_region` still overrides tiny with a deck-only
region. Its owner must adapt that override to the mixed hold/deck selection.
No task 4.3 files were changed here.

The existing `verification.json` and `geometry-diagnostic.json` record pre-D20
1.0.0 experiments; they are historical, not evidence for this migration.
