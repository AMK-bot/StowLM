# TRAIN layover comparison

Six seeds `331101000–331101005`, fixed before solver measurements. One map per size and occupancy; each pass cell is a single observation. Non-DG dry20 demand retains generated masses/loaded–empty mix on small and medium; tiny uses the generator’s native empty-cargo option. Total demand is about 70% of physical cells. The lighter boxes form arrival prefixes; one current discharge lies below retained later cargo.

**Pass** means the unchanged full checker accepted the finished plan. **Replay** separately means a complete legal loading/discharge replay; `?` means the as-is entry point exposed no finished plan, so its internal replay gate is unavailable. Failed rows have no reported objective. Restows are lifts, split current/projected future. Arrival restows count only boxes already aboard at input.

| Size | Arrival occupancy | Method | Replay | Pass | Lifts | Restows now/future | Arrival restows now/future | Wall s |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| tiny | 30% (33.3% actual) | 0.9.0 | ? | 0/1 | — | — | — | 19.25 |
| tiny | 30% (33.3% actual) | Engine default | 1 | 1/1 | 8 | 2/0 | 2/0 | 11.82 |
| tiny | 30% (33.3% actual) | Arrival pages | 1 | 1/1 | 5 | 1/0 | 1/0 | 9.62 |
| tiny | 50% (50.0% actual) | 0.9.0 | ? | 0/1 | — | — | — | 14.8 |
| tiny | 50% (50.0% actual) | Engine default | 1 | 1/1 | 8 | 4/0 | 4/0 | 9.66 |
| tiny | 50% (50.0% actual) | Arrival pages | 1 | 1/1 | 8 | 4/0 | 4/0 | 7.06 |
| small | 30% (29.4% actual) | 0.9.0 | ? | 0/1 | — | — | — | 29.2 |
| small | 30% (29.4% actual) | Engine default | ? | 0/1 | — | — | — | 40.51 |
| small | 30% (29.4% actual) | Arrival pages | 0 | 0/1 | — | — | — | 44.86 |
| small | 50% (50.0% actual) | 0.9.0 | ? | 0/1 | — | — | — | 5.44 |
| small | 50% (50.0% actual) | Engine default | ? | 0/1 | — | — | — | 16.55 |
| small | 50% (50.0% actual) | Arrival pages | 1 | 0/1 | — | — | — | 363.32 |
| medium | 30% (30.0% actual) | 0.9.0 | ? | 0/1 | — | — | — | 20.4 |
| medium | 30% (30.0% actual) | Engine default | ? | 0/1 | — | — | — | 100.22 |
| medium | 30% (30.0% actual) | Arrival pages | 1 | 0/1 | — | — | — | 416.91 |
| medium | 50% (50.0% actual) | 0.9.0 | ? | 0/1 | — | — | — | 92 |
| medium | 50% (50.0% actual) | Engine default | ? | 0/1 | — | — | — | 97.55 |
| medium | 50% (50.0% actual) | Arrival pages | 1 | 0/1 | — | — | — | 413.94 |

0.9.0 uses its native search floor `min(120, max(20, 6 × new loads))` and 300 seconds per native finisher; two candidate tries. The frozen engine uses default switches and `budget_s=None`: 120 seconds additive work when new loads ≤60, 375 seconds otherwise, plus its native search/finishing floors. Arrival pages request 90 seconds construction, 300 seconds finishing and three tries. All use seed 0, one numerical thread and the same full checker; wall time includes failures. Nice 5 was requested but denied by the sandbox.

Budget-management limitation: medium 30%/50% construction took 109.11/95.00 seconds against 90-second requests because atomic setup/pricing overran. Their outer finishing took 304.67/316.59 seconds; small 50% outer finishing took 330.95 seconds against 300-second native-finalize requests. The larger candidates timed out and are not passes or checked upper bounds. These runs establish no 90-second performance claim.

There is no paired lift comparison with 0.9.0: its two tiny calls raise `AttributeError: Discharge has no attribute slot` in `alns.search`, and the four larger calls return no finished plan. The original raw exception replay flags are preserved; this report marks their unexposed internal gate `?`. Engine nonpasses also expose no finished plan. Its native replay metadata is unreliable here: it says false even on both independently replayed tiny passes because archive retention compares all action box IDs with only new-load IDs.

| Size | 0.9 passes | Engine passes | Arrival-page passes | Mutual engine/pages maps | Paired engine lifts | Paired page lifts | Mean page − engine |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| tiny | 0/2 | 2/2 | 2/2 | 2 | 8 | 6.5 | -1.5 |
| small | 0/2 | 0/2 | 0/2 | 0 | — | — | — |
| medium | 0/2 | 0/2 | 0/2 | 0 | — | — | — |

Evidence: [joined rows](results/comparison.json), [0.9.0 rows](results/run1/frontier09/), [default engine rows](results/engine-defaults/engine/), [final method rows](results/final-method/layover/), [cohort manifest](cohort/MANIFEST.json), [prefix validation](results/cohort_validation.json), [generation decisions](results/generation_decisions.json). Earlier zero-additive engine rows under `results/run1/engine/` are an ablation and excluded from the primary comparison. Initial runner import-path failures remain separately under `results/setup-import-errors/`; they are excluded. Some earlier method-development rows share that setup directory and are also excluded.

Certified arrival-aware lower bounds and relaxation statuses are recorded in [the bound table](bound-results/TABLE.md). Tiny 30% has a checked 5-lift plan matching its 5-lift lower bound.

These are structural Stage B snapshots from a thin generator wrapper, with original POL/provenance retained and selected onboard POD changed to current/later calls. They are not prior-voyage simulations or representative mixed-cargo benchmarks. The all-empty medium transformation failed the generator’s synthetic-background construction before solver measurement; native masses were retained for the fixed final six-map cohort.
