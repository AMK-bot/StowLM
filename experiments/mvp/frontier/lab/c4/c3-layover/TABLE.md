# Cycle 4 layover comparison

TRAIN: 72 fresh predeclared seeds, twelve maps per size/arrival-occupancy cell. Native non-DG dry20 masses, about 70% total cargo occupancy; structural Stage B snapshots. No solver-outcome selection. No sealed data. Occupancy labels are targets: the 30% cells are actually tiny 33.3%, small 29.4%, medium 30%; all 50% cells are exactly 50%.

**Pass** is independent unchanged full-checker acceptance. **Replay** is a complete legal current-call loading/discharge replay, including retained arrival cargo. `?` is an unavailable internal gate when an as-is comparator returns no plan. Lifts and restows are means among passing plans; later-port lifts follow the fixed-stow D15 projection with no later loads. Wall time includes failures.

| Size | Occupancy | Arm | Measured | Replay / ? | Pass | Lifts | Restow now/later | Arrival restow now/later | Wall s |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| tiny | 30% | Fresh 0.9.0 | 12/12 | 0 / 12 | 0/12 | — | —/— | —/— | 20.14 |
| tiny | 30% | Frozen engine | 12/12 | 12 / 0 | 12/12 | 7.83 | 2/0.5 | 2/0.5 | 12.39 |
| tiny | 30% | Arrival pages + LP | 12/12 | 12 / 0 | 12/12 | 5 | 1/0 | 1/0 | 10.02 |
| tiny | 50% | Fresh 0.9.0 | 12/12 | 0 / 12 | 0/12 | — | —/— | —/— | 21.47 |
| tiny | 50% | Frozen engine | 12/12 | 12 / 0 | 12/12 | 12.5 | 4/3 | 4/3 | 12.84 |
| tiny | 50% | Arrival pages + LP | 12/12 | 12 / 0 | 12/12 | 8 | 4/0 | 4/0 | 10.6 |
| small | 30% | Fresh 0.9.0 | 12/12 | 0 / 12 | 0/12 | — | —/— | —/— | 30.1 |
| small | 30% | Frozen engine | 12/12 | 0 / 12 | 0/12 | — | —/— | —/— | 42.51 |
| small | 30% | Arrival pages + LP | 12/12 | 9 / 0 | 0/12 | — | —/— | —/— | 484.13 |
| small | 50% | Fresh 0.9.0 | 12/12 | 0 / 12 | 0/12 | — | —/— | —/— | 19.77 |
| small | 50% | Frozen engine | 12/12 | 0 / 12 | 0/12 | — | —/— | —/— | 32.38 |
| small | 50% | Arrival pages + LP | 12/12 | 9 / 0 | 0/12 | — | —/— | —/— | 748.23 |
| medium | 30% | Fresh 0.9.0 | 12/12 | 0 / 12 | 0/12 | — | —/— | —/— | 29.05 |
| medium | 30% | Frozen engine | 12/12 | 0 / 12 | 0/12 | — | —/— | —/— | 111.68 |
| medium | 30% | Arrival pages + LP | 12/12 | 10 / 0 | 0/12 | — | —/— | —/— | 652.87 |
| medium | 50% | Fresh 0.9.0 | 12/12 | 0 / 12 | 0/12 | — | —/— | —/— | 97.02 |
| medium | 50% | Frozen engine | 12/12 | 0 / 12 | 0/12 | — | —/— | —/— | 109.5 |
| medium | 50% | Arrival pages + LP | 12/12 | 9 / 0 | 0/12 | — | —/— | —/— | 394.75 |

Mutual full-checker passes only (paired lift comparison):

| Size | Occupancy | Comparator | Pairs | Comparator lifts | Method lifts | Comparator / method wall s |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| tiny | 30% | Fresh 0.9.0 | 0 | — | — | — / — |
| tiny | 30% | Frozen engine | 12 | 7.83 | 5 | 12.39 / 10.02 |
| tiny | 50% | Fresh 0.9.0 | 0 | — | — | — / — |
| tiny | 50% | Frozen engine | 12 | 12.5 | 8 | 12.84 / 10.6 |
| small | 30% | Fresh 0.9.0 | 0 | — | — | — / — |
| small | 30% | Frozen engine | 0 | — | — | — / — |
| small | 50% | Fresh 0.9.0 | 0 | — | — | — / — |
| small | 50% | Frozen engine | 0 | — | — | — / — |
| medium | 30% | Fresh 0.9.0 | 0 | — | — | — / — |
| medium | 30% | Frozen engine | 0 | — | — | — / — |
| medium | 50% | Fresh 0.9.0 | 0 | — | — | — / — |
| medium | 50% | Frozen engine | 0 | — | — | — / — |

Comparators run freshly in isolated Python processes with native behavior. Fresh 0.9.0 receives `search_s=min(120,max(20,6×new loads))`, `finalize_s=60`, two tries, seed 0. The frozen engine receives `budget_s=None`, `finalize_s=60`, seed 0, retaining native default extra budgets (120 seconds for ≤60 new loads, 375 otherwise) and native search/finishing floors (effective finishing 300 seconds at ≤60 new loads, 60 otherwise). This is an as-is comparison; work budgets differ and are recorded on every row. The method uses deterministic construction work counts and local ballast-LP finishing; its exact work configuration and completion counts are recorded in row metadata. All arms use one numerical thread and one master worker. Primary jobs use at most two admitted shards after BENCH-RUNNING cleared (one active shard is allowed at the tail); other shared-machine load can vary and is recorded. The six arm orders are counterbalanced twice within each twelve-map cell; every row records start/end system load averages. The baseline retains the native E9 bug; any E9-only gain must be labelled a bug fix.

Rows present: 216/216. No cached stored baseline rows are used. Source/input hashes and exact solver configuration are checked on resume. Failed objectives are never treated as upper bounds.
