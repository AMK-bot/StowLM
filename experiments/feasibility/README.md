> **Current D49 follow-up:** [fix-t4 report](../review/fixes/fix-t4-feas.md) and
> [source-pinned measurements](fix-t4-measure/report.md) supersede the historical implementation/availability
> claims below. C2 now requests `direction="necessary"`; forced hatch restows use their actual cleared state,
> and unresolved slots receive LPs regardless of the soft budget hint. The final checker still certifies plans.
> Current bounded sampling: tiny6/10 and small4/10 requests accepted, all10 reaching C2 accepted. Full-size5ms
> p95 remains unmet. These are necessary-screen results, not proof of full-checker completion or zero false pruning.

# Feasibility engine v2: measurements on instances-v2 (task 4.4, after D25)

Measured on 27 Oct 2026, 07:20–07:57 EDT, on the shared M5 Max. At most 2 processes ran at once, each under `nice`,
with BLAS limited to one thread. No GPU. Python 3.12 through uv. Other executives were also loading the machine.

The engine is `src/stowage/feasibility/_engine.py` (v2 + D25: E2 by weight class L < 10 t ≤ M < 20 t ≤ H; E1 heavy
= class H; E6 = C1f). Catalogue defaults apply and there are **no rule overrides** (`SYNTHETIC_OVERRIDES` is gone).

The previous report (stale instances, synthetic 2 t E2 tolerance) is kept as `results-v1-synthetic.json`
(`tables.py` prints its tables).

> **C2 OFF (3.7 model unavailable)** for every number below except the C2 probe section: `global_screen: 0`.



## v2.3 (27 Oct, 15:20–16:05): DG segregation capacity (C1g), index-array accessor, re-measurement after the incremental E6 fix

Same 240 fresh instances and harness as below. C2 OFF (3.7 still being fixed).

**Completion and soundness** (`study-v23-*.json`; medium and full also from `study-v23c-*.json`, final code):

| rung | random_yard random | random_yard greedy | simulated random | simulated greedy | completed plans failing 2.4 |
|---|---|---|---|---|---|
| tiny | 142/150 | 29/30 | 141/150 | 29/30 | 0 |
| small | 65/150 | 18/30 | 67/150 | 20/30 | 0 |
| medium | 0/150 | 4/30 | 1/150 | 3/30 | 0 |
| full | 0/15 | 0/5 | 0/15 | 0/5 | 0 |

C1g changed no completion count. The finer attribution (`deadend_probe2.py`) shows DG segregation explains far less than claimed below (that estimate lumped every "passes the fast L2 filters" case together):

| dead-end cause | small, random_yard | small, simulated | medium, random_yard | medium, simulated |
|---|---:|---:|---:|---:|
| fast L2 (static / E2 class / stack mass) | 22 | 17 | 4 | 4 |
| C1f (E6 departure) | 9 | 9 | 25 | 39 |
| heat | 4 | 5 | 14 | 10 |
| C1g involved | 4 | 0 | 3 | 8 |
| C1h involved | — | — | 10 | 9 |

**The next lever is E6, not DG:** random rollouts put class-H boxes on deck while hold room remains.

**Latency** (`legal_actions` cold, ms, p50 / p95 / p99 / max, final code, load average 2–7):

| rung | random_yard | simulated |
|---|---|---|
| medium | 2.0 / 6.4 / 10.8 / 354 | 2.1 / 8.0 / 13.4 / 426 |
| full | 12.3 / 58.3 / 79.5 / 112 | 13.1 / 55.2 / 72.6 / 89 |

- `push` p95 is 0.2–0.3 ms; maximum 1.9 ms on full and about 11 ms on medium.
- A quiet single run on `random_yard/full/seed-70300000` gave p50 5.7 ms and p95 8.2 ms. Its outliers (24–43 ms) are C1g after mass restows: a restowed DG box forces a full recompute of the C1g sets.
- The 21–23 s medium spikes of v2.2 are gone. The maximum is now 0.35–0.43 s, at the last box, once per plan.

**Index-array accessor** (for 5.1): `legal_index()`, `legal_pairs()`, `legal_discharges()`, `index_boxes`, `index_slots`, `n_slots`, `decode_index()`, `index_of()`.
- Evaluating a state no longer builds any action objects. `legal_actions()` materialises them lazily from the index array.
- `tests/feasibility/test_index_access.py` checks equality with `legal_actions()` on every visited state and pushes through the index path alone.

## Completion-strength study on fresh instances (27 Oct, v2.1 → v2.2) — read this first

**Instances.** 240 fresh train/val instances, 30 per rung and generator, from `gen_fresh.py`:
- 3.5 random-yard v1.4.0 and 4.3 simulated-yard v2.4.0, with the reconditioned slices;
- tiny/small on feeder_500 and medium/full on feeder_1000; laem_chabang_like and singapore_like ports; noise 0.2 or blocking ratio 0.3;
- splits from `eval.splits.split_of`, with test specs and held-out axes skipped;
- no generator failures;
- DG boxes present (DoC data now plannable), catalogue D25 defaults, no overrides.

**Settings.**
- C2 was OFF throughout. The 3.7 model built on full instances once (14:28). Twice later it failed while 3.7 was being edited ("liquid-transfer assets changed…", `KeyError 'DO'`), so there are no C2-on numbers.
- Harness: `study.py`. Per instance: 5 uniform-random rollouts plus 1 greedy rollout (no forced restow first, then heaviest box, then lowest tier).
- Per-block wall cap 420 s, so medium ran 26 instances and full only 3 (v2.1 full: 1).
- Machine load average was 10–20 from other jobs.
- Raw data: `study-v21-*.json` (frozen v2.1 copy in `baseline_v21/`) and `study-v22-*.json`.

**Completion.** Each cell is random rollouts completed (v2.1 → v2.2), then greedy:

| rung | random_yard random | random_yard greedy | simulated random | simulated greedy |
|---|---|---|---|---|
| tiny | 142/150 → 142/150 | 29/30 → 29/30 | 139/150 → 141/150 | 29/30 → 29/30 |
| small | 54/150 → 65/150 | 18/30 → 18/30 | 58/150 → 67/150 | 20/30 → 20/30 |
| medium | 0/65 → 0/130 | 0/13 → **4/26** | 0/70 → 1/130 | 0/14 → **3/26** |
| full | 0/5 → 0/15 | 0/1 → 0/3 | 0/5 → 0/15 | 0/1 → 0/3 |

**Soundness.** v2.1 produced completed plans with hard 2.4 E6 violations: 7 on random-yard small, 3 on simulated small. Its C1f skipped 40', DG and OOG deck-H boxes and the quay-buffer last box. v2.2 has **0** across all completed plans, every one verified with `apply_plan` and `check_static_rules`.

`tests/feasibility` (47 tests + 1 strict xfail) covers:
- L1∧L2 exactness against the 2.3/2.4 oracle (DG, OOG and heat fixtures);
- every C1 per-candidate screen against the post-state's own check;
- own-model soundness;
- C2 global and local shifts;
- E6 departure.

**Latency** (`legal_actions` cold, ms, p50 / p95 / p99, C2 off, load average ~10–20):

| rung | random_yard v2.1 | random_yard v2.2 | simulated v2.1 | simulated v2.2 |
|---|---|---|---|---|
| tiny | 1.4 / 2.1 / 2.5 | 2.5 / 4.3 / 6.9 | 1.7 / 2.4 / 2.7 | 2.6 / 4.3 / 7.7 |
| small | 0.9 / 1.7 / 2.3 | 2.5 / 4.2 / 6.4 | 1.8 / 3.2 / 4.2 | 2.3 / 4.2 / 7.1 |
| medium | 7.9 / 60.6 / 128 | 7.4 / 11.4 / 13.6 | 9.3 / 35.2 / 114 | 7.3 / 12.0 / 14.9 |
| full | 45 / 285 / 498 | 16.6 / 25.7 / 31.1 | 57 / 390 / 603 | 17.7 / 28.2 / 34.3 |

- **Push:** p95 0.4–1.5 ms; p99 ≤ 4 ms on full.
- **Full-rung legal set:** about 56k actions per state early in the plan. L1∧L2 alone (screens off) takes p50 about 7–10 ms there under this load.
- **The 5 ms p95 target is not met on full.** Listing about 56k action objects is itself a few ms in Python.

**Spikes.**
- The mask owner's 0.9–4 s spikes (v2.1) and v2.2's 21–23 s medium maxima came from the same place: the exact E6 departure check at the last box. It ran 2.4's `e6_departure_violations` once per final candidate, often when the last box sat on the quay buffer after a hatch restow.
- Fixed by an incremental exact version (`_e6_last`): one base computation, then per-candidate deltas. On the worst instance (`random_yard/medium/seed-70200021`): 14.2 s → 1.0 s and 5.1 s → 0.4 s, once per plan.
- The study table above predates that fix. Its other maxima on full (180–200 ms) are first states.

**Still dead.** About 55% of dead ends are "L1/L2: no box fits any open slot". `deadend_probe.py` breaks them down:
- about half are DG segregation: the last DG boxes have no segregation-legal open cell, and C1 has no DG pair model yet;
- the rest are weight class combined with compatibility or alignment.

## Headline

1. **No instances-v2 instance that carries a DG box can be planned.** Every load-list DG box, of every class, has
   *zero* statically allowed slots. The checker rejects each one in every cell with hard `checker.missing_vessel_data`:
   "DG packing_form shipment fact is missing", plus "SW2 requires clear_living_quarters data" where SW2 applies.
   - The engine therefore reports a dead end at depth 0. That verdict is correct: brute force (screens off) finds no
     complete plan on any such tiny instance (7/7).
   - This is a data-contract gap between 4.3 (instances) and 4.1 (checker), not an engine bug.
   - It hits **100% of medium and full instances**, 83% of small and 29% of tiny (census below).
   - Repro: `experiments/instances-v2/simulated_yard/tiny/seed-10000000.json`, box `QZXU0000040` (UN 2810, 6.1/A/SW2).
     Place it in any of the 6 cells, then run `check_static_rules`.
2. **D25 removed the old driver (the synthetic E2 kg tolerance), but dead ends remain.**
   - On DG-free small instances, uniform-random rollouts still dead-end 200/200 times (mean depth 24 of 36). Greedy
     completes 4/20.
   - Now the dead ends come from the *combination* of capacity screens on the candidates' post-states:
     - C1d, aggregate stack-mass room (E7);
     - C1e/C1c, 40' boxes vs aligned stack pairs, nested by mass;
     - C1a, class matching;
     - C1b, the D25 weight-order nested Hall.
   - 15% are pure L1/L2 dead ends: no physically and statically legal candidate at all.
   - The engine's `dead_end().rule_ids` cannot tell these apart, because any C1 rejection is labelled
     `expert.e2.weight_order` + `expert.physical.slot_occupancy`. The candidate profile below is the real breakdown.
3. **C2 is still unavailable** at the start and at the end of the session (details below).
4. **No soundness bug found.**
   - Every completed plan (759/759 on tiny across both samples, 4/4 on small) passed `apply_plan` and had no hard static violation. E6
     violations: 0.
   - No engine exception, no listed action that failed `push`/`is_legal`, and no valid brute-force plan rejected by
     the screens (2.09 M plans checked).
5. **New latency problem:** on the DG-stripped `random_yard/medium/seed-00200000` diagnostic instance, `legal_actions`
   cold took p95 **2.2 s**. The cause is 56,055 `exact_calls`, the exact 2.4 fallback per candidate.

## Fixes made after this measurement (27 Oct, executive)

- **OOG latency bug fixed.** Candidates near OOG cargo (and OOG candidates themselves) no longer call 2.4's full
  `placement_violations` with a cache keyed on every stack in a wide zone. They use the fast path for every rule
  plus 2.4's `special_violations` on a minimal ship (the candidate, every placed box whose envelope could come
  within the OOG clearance by a conservative horizontal-gap bound, and the boxes under each), cached on exactly
  those placements. On the repro (`random_yard/medium/seed-00200000`, DG dropped, random policy, seed 0, machine
  load ≈ 15): exact 2.4 calls 56,055 → 9,853 per rollout, rollout wall 128 s → 2.7 s, `legal_actions` p50 3.9 ms /
  p95 8.8 ms. All exactness tests still pass (the oracle covers OOG fixtures). The table below predates the fix.
- **Dead ends now name the failing completion condition.** `DeadEnd.reason` lists which of c1a–c1f / c2 rejected
  how many candidates, and `rule_ids` are the catalogue rules behind those conditions (e.g. c1d →
  `expert.physical.stack_weight`, c1e → `expert.e2.weight_order` + `expert.physical.size_fit`, c1f →
  `expert.e6.heavy_under_deck`), instead of a fixed pair.

## Instances

- Source: canonical `experiments/instances-v2/{random_yard,simulated_yard}/<rung>/seed-*.json` (first N in seed order,
  `*.audit.json` / `manifest.json` skipped), loaded with `stowage.io.load_instance`. KCS hulls with strength stations,
  DG kept.
- **Primary sample** (`results.json`, `run.log`): tiny 20+20, small 10+10, medium 4+4, full 3+3 (as requested).
- **Alive sample** (`results-alive.json`, `run-alive.log`; `--select alive`): the first 20+20 tiny and 10+10 small
  instances that are alive at depth 0. All of them are DG-free; it is a selected sample, not the canonical prefix.
  This is the only population where dead ends beyond depth 0 can be studied.
- **Latency-only diagnostic** (`results-latency-nodg.json`; `--drop-dg`): medium seed 0 of each generator with its 8
  DG load boxes **removed**. It is used only for timing, because no state exists to time on medium/full otherwise.
  The full-rung version was stopped for budget, so there is no full latency.
- **Census** (`census.json`): depth-0 status of all 1,080 tiny–full instances.

| generator / rung | instances | with DG | alive at depth 0 | dead, cause = unplaceable DG box |
|---|---:|---:|---:|---:|
| random_yard tiny | 200 | 51 | 149 | 51 |
| simulated_yard tiny | 200 | 63 | 137 | 63 |
| random_yard small | 200 | 165 | 35 | 165 |
| simulated_yard small | 200 | 169 | 31 | 169 |
| random_yard medium | 100 | 100 | 0 | 100 |
| simulated_yard medium | 100 | 100 | 0 | 100 |
| random_yard full | 40 | 40 | 0 | 40 |
| simulated_yard full | 40 | 40 | 0 | 40 |

Alive ⇔ no DG on every instance. Every DG class/category occurs among the unplaceable boxes, including 3/A, which has
no stowage code. That points to the generic `packing_form` fact.

## C2 status (global_screen 1)

`c2_probe.py`; logs `c2_probe-start.{json,log}`, `c2_probe-end.log`.

| attempt | instance | construct (s) | `diagnostics["global_screen"]` |
|---|---|---:|---|
| start | random_yard/tiny/seed-00000000 | 169.5 | unavailable: validity-band sweep accepted only 0 of 64 required conditions in 3200 attempts: no condition reached equilibrium |
| start | random_yard/small/seed-00100000 | 2.7 | same |
| start | random_yard/full/seed-00300000 | 106.0 | unavailable: … 0 of 64 … no hydrostatic LP projection for the fixed cargo and tank windows |
| end | tiny (same) | 397.3 | same as start (still unavailable) |
| end | small (same) | 6.6 | same as start |
| end | full (same) | – | not finished: stopped at the session budget |

- Every attempt failed closed: dead end at depth 0, `checker.missing_vessel_data`. So no C2-on latency or rollouts
  exist, and `lp_calls`, `c2_unresolved` and `recenters` are all 0 below because C2 is off.
- Small builds faster than tiny on the same hull, probably because the result is cached within the process.
  **Unverified.**

## Latency (ms, C2 OFF)

Every state along the uniform-random rollouts is timed:
- `legal_actions` cold: the first call on a new state;
- `legal_actions` cached: a second call on the same state;
- `is_legal` and `copy` on the chosen action;
- `push` and `pop`, timed as push/pop/push.

| sample (boxes) | construct max / median | legal cold p50 / p95 / p99 | cached p50 | is_legal p95 | push p50 / p95 / p99 | pop p95 | copy p95 | states |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| tiny alive (6) | 878 ms* / 1.6 ms | 1.58 / 4.10 / – | 0.002 | 0.019 | 0.14 / 0.32 / – | 0.008 | 0.025 | 2,800 |
| small alive (36) | 23 / 9 ms | 3.06 / 13.9 / – | 0.002 | 0.016 | 0.19 / 0.68 / – | 0.014 | 0.028 | 4,981 |
| medium primary (150, DG) | 1.0 s / 0.6 s | 0.003 (dead at 0) | – | – | – | – | – | 24 |
| full primary (280, DG) | 4.2 s / 1.5 s | 0.003 (dead at 0) | – | – | – | – | – | 12 |
| medium no-DG diag, random_yard (142) | 1.06 s | **95 / 2,168 / 3,231** | 0.003 | 0.022 | 0.25 / 0.96 / 4.6 | 0.024 | 0.034 | 334 |
| medium no-DG diag, simulated_yard (142) | 0.13 s | 2.8 / 7.5 / 8.6 | 0.002 | 0.010 | 0.14 / 0.80 / 1.5 | 0.013 | 0.018 | 646 |
| **target (full)** | < 2 s | p95 ≤ 5 | – | ≤ 0.5 | ≤ 1 | ≤ 1 | < 1 | |

\* The first construction in the process includes imports/warm-up.

- **Full latency is not measured.** No full instance has a non-dead state while DG is kept, and the no-DG full
  diagnostic was stopped for budget.
- Full construction is 1.5 s median and 4.2 s max under load, against the 2 s target.
- The 2.2 s p95 on random_yard medium seed 0 comes from the exact 2.4 fallback (`exact_calls` 56,055 in one rollout,
  against about 120 on the simulated twin). The fallback is used for boxes the vectorised tables can't represent
  (`T.fallback`) or near OOG aboard. One random rollout took 128 s.
  - Repro: `measure.py --drop-dg --per-rung medium=1 --random medium=2 --generators random_yard`, policy random,
    seed 0.
  - **To fix in 4.4:** find which boxes are marked fallback there, and vectorise them.
- `push`, `pop`, `copy`, `is_legal` and cached `legal_actions` are within target. The exception is push p99 on the
  diagnostic instance, where pushes trigger hatch restows.
- Legal-set size: tiny p50 12.5 (max 36); small p50 55 (max 450); medium no-DG p50 about 1,700–1,900 (max about
  12,200).

## Dead ends (C2 OFF)

Policies:
- **random**: uniform over `legal_actions`. 10 rollouts per instance on tiny/small, 3 on medium, 2 on full.
- **greedy**: forces_restow False first, then heaviest box, then lowest tier, then `sort_key`. 1 per instance.

Completed plans were verified with `apply_plan(...).legal` and with `check_static_rules` (hard violations; E6
counted separately).

| sample | policy | complete | dead at depth 0 | dead later (mean depth) | apply_plan legal | static hard | E6 |
|---|---|---:|---:|---:|---:|---:|---:|
| tiny primary | random | 290/400 | 110 | 0 | 290/290 | 0 | 0 |
| tiny primary | greedy | 29/40 | 11 | 0 | 29/29 | 0 | 0 |
| small primary | random | 0/200 | 170 | 30 (26.7) | – | – | – |
| small primary | greedy | 0/20 | 17 | 3 | – | – | – |
| medium primary | random / greedy | 0/24 / 0/8 | all | 0 | – | – | – |
| full primary | random / greedy | 0/12 / 0/6 | all | 0 | – | – | – |
| tiny alive | random | 400/400 | 0 | 0 | 400/400 | 0 | 0 |
| tiny alive | greedy | 40/40 | 0 | 0 | 40/40 | 0 | 0 |
| small alive | random | 0/200 | 0 | 200 (23.9 of 36) | – | – | – |
| small alive | greedy | 4/20 | 0 | 16 (26.7) | 4/4 | 0 | 0 |
| medium no-DG diag | random / greedy | 0/3 / 0/1 | 0 | 4 (315–333 random incl. restows; 140 greedy) | – | – | – |

**Rule ids at the dead end** (`dead_end().rule_ids`):
- All depth-0 dead ends (primary): `expert.e2.weight_order` + `expert.physical.slot_occupancy`. `completion_check()`
  gives `c1a` False, and every L1∧L2 candidate's post-state fails C1a. The real cause is the unplaceable DG box.
- Small alive, random: that same pair 171/200, and `()` 29/200. In the `()` cases no L1∧L2 candidate exists (reason:
  "Every candidate breaks a placement rule…").
- Small alive, greedy: the pair 9/16, `()` 7/16.
- Medium no-DG: `()` 4/4, no L1∧L2 candidate.

**`completion_check()` at the dead-end state itself** was all True in 200/200 random and 16/16 greedy small-alive
dead ends. The necessary conditions pass on the state; no candidate's post-state does.

**Rejected-candidate profile, small alive, random** (≤ 60 L1∧L2 candidates per dead end: a shadow engine with
`completion_screens: 0` pushes each one and reads the post-state's `completion_check()`). Count of candidates whose
post-state fails each key, alone or with others:

| key | candidates failing it | share of 3,323 |
|---|---:|---:|
| c1e (40' vs aligned pairs, nested by mass) | 2,252 | 68% |
| c1d (aggregate stack-mass room) | 2,029 | 61% |
| c1a (class matching) | 1,398 | 42% |
| c1b (D25 weight-order nested Hall) | 1,253 | 38% |
| c1c (40' vs aligned pairs, count) | 643 | 19% |
| c1f (E6 departure), c2 | 0 | 0% |

**Did D25 remove the dead-end driver? Partly.**
- The v1 driver, `expert.e2.weight_order` under a synthetic 2 t kg tolerance, is gone. C1b (weight order) is now
  involved in only 38% of the rejected candidates, and never alone in most dead ends. E6 (C1f) never fires.
- But uniform-random rollouts still dead-end 100% on small. The binding constraints are now mixed-length pairing
  (C1c/C1e) and stack-mass room (C1d), plus a 15% share of pure L1/L2 dead ends.
- On medium/full the question can't be answered with DG kept, because everything is dead at depth 0. On the no-DG
  medium diagnostic, all 4 dead ends were L1/L2-only, with C1 all True. These are deep dead ends (2 boxes left for
  greedy) that the necessary conditions don't foresee.
- The engine's rule-id label is uninformative. **Suggested 4.4 change:** report the failing C1 sub-check ids in
  `DeadEnd.rule_ids` (C1d → `expert.e7…`/stack mass; C1c/e → 40' pairing).

## Completeness vs brute force on tiny (`completeness.py`, `completeness.json`)

**Setup:**
- Instances: the first 15 + 15 tiny instances in seed order.
- Enumeration: memoised DFS over an engine U with `completion_screens: 0, global_screen: 0` (L1∧L2). Actions with
  `forces_restow` are skipped.
- A plan is **valid** iff `apply_plan` is legal and `check_static_rules` reports no hard violation. **C2 /
  stability / strength cannot be checked** (3.7 model unavailable).
- The screened engine S (`completion_screens: 1`, C2 OFF) follows each path in lock-step.
- Per-instance cap: 30 s.

**Results:**
- 23 instances have plans; 7 have none, and on all 7 S is dead at depth 0 (the DG instances). U explores them
  without finding any complete plan, so the root verdict is right.
- All 23 instances with plans hit the 30 s cap under contention. The counts below are therefore lower bounds:
  truncated subtrees count 0. In a quiet pilot, a full enumeration took 15 s per instance: 518,400 plans.

| quantity | value |
|---|---:|
| complete restow-free plans enumerated | 2,089,873 |
| of which valid | 2,089,873 (every complete plan is valid) |
| (a) valid plans S rejects (soundness bugs) | **0** |
| (b) DFS edges (distinct states, S alive) S prunes | 0 / 926,229 |
| (c) S optimum = true optimum | 23/23 (optima: 0 ×18, 1 ×3, 2 ×2 weighted lifts) |
| optimum re-checked by replaying the plan through `apply_plan` | 23/23 equal |

- **Caveat: this test is weak.** Tiny vessels in instances-v2 are 6 single-tier deck stacks for 6 boxes. Every
  complete placement is a permutation; E1/E2/E6/stack weight never bite, and the only pruning possible is the
  depth-0 DG verdict. A meaningful completeness test needs a tiny rung with multi-tier stacks or a hold, or brute
  force on small, which is too big for plain DFS.
- **Exact solver:** `stowage.solver.exact.solve_exact(time_limit_s=5)` matched brute force on a dead instance
  (INFEASIBLE, 1.7 s). On an alive one it returned TIME_LIMIT after **219 s** of wall time, against its 5 s limit:
  it presumably builds the 3.7 model first. It is not cheap here, so it was skipped (`--exact-time 0`). Report to the
  4.6 owner.

## How solvers should use the engine (4.6 / 4.7 / 4.8)

- **Generate only through `legal_actions()`** (or check `is_legal` before `push`). Gold plans then replay through the
  5.1 mask by construction. `push` raises `IllegalActionError` for anything else.
- **Beam search: `copy()` freely.** It copies O(depth) pointers to shared, immutable snapshots: p95 ≤ 0.035 ms here.
  The cached `legal_actions` on a visited state costs about 2–3 µs. Only the first `legal_actions` on a new state
  costs anything: about 3 ms p50 on small/medium, C2 OFF. On instances that hit the exact fallback it can take
  seconds; see the latency section.
- **Check `dead_end()` at depth 0 first.** On instances-v2 today, any instance with a DG box is dead at depth 0
  (missing checker facts). Don't spend search on it, and report it as an instance-data failure.
- **Dead ends are normal; plan for them.**
  - On `dead_end()` (empty `legal_actions`, not complete), `pop()` and try another candidate (backtracking), or hand
    the prefix to 5.7 repair.
  - Uniform-random choice almost never completes a small instance. Rank actions:
    - place 40' boxes while aligned stack pairs remain;
    - keep stack-mass room for the heavy boxes still to come;
    - place hold boxes under a hatch before its deck stacks;
    - respect the D25 class order per stack.
  - Use beam, restarts or LDS rather than local backtracking.
- **Use `forces_restow(action)` to rank actions,** not to filter them.
- **`to_plan()` has no ballast.** The 4.2 finisher adds it; then run the full checker. With C2 off (as here), the
  engine does not check stability and strength at all.

## Reproduce

```bash
export UV_CACHE_DIR=$PWD/.uv-cache
nice uv run python experiments/feasibility/c2_probe.py --tag start --out experiments/feasibility/c2_probe-start.json  # ~5 min
nice uv run python experiments/feasibility/measure.py --census tiny small medium full --per-rung tiny=0 --random tiny=0 \
    --out experiments/feasibility/census.json                                                        # 6.3 min
nice uv run python experiments/feasibility/measure.py --out experiments/feasibility/results.json      # 1.5 min
nice uv run python experiments/feasibility/measure.py --select alive --per-rung tiny=20 small=10 \
    --random tiny=10 small=10 --out experiments/feasibility/results-alive.json                        # 1.3 min
nice uv run python experiments/feasibility/measure.py --drop-dg --per-rung medium=1 --random medium=2 \
    --out experiments/feasibility/results-latency-nodg.json                                           # ~4 min
nice uv run python experiments/feasibility/completeness.py --n 15 --out experiments/feasibility/completeness.json  # 12 min
```

## Compute used

- Estimate: 2 cores / 4 GB / no GPU / 35 min.
- Actual: 37 min of wall time with at most 2 niced processes, about 54 CPU-minutes, peak RSS 0.46 GB, no GPU.
- Overruns:
  - the census was not piloted (378 s);
  - C2 constructions took 169–397 s each;
  - the no-DG full diagnostic and the C2-on full end attempt were stopped (own PIDs) to stay near budget.
- Logged in `experiments/fleet/history.md`.

D55 follow-up: the necessary visibility roof cap now applies to local masks,
including diagnostic C1/C2-off masks. Both attributed bay002tier90 offenders
reject while a just-under-cap placement passes. See the D55 section of the fix
log, `d55-targeted.log/xml`, `d55-latency.json` and `d55-compute.md`.
