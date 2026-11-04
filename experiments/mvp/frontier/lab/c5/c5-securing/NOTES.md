# Cycle 5: why securing breaks, and a cold method that passes dense ships

Read: CONTRACT through Cycle 4, lab README, c4/c2-layout (NOTES, FAILURES, method_dense, pages), c3-feasproof rod notes,
c4/c3-feasproof NOTES, c4/c2-benders NOTES, and `src/stowage/checker/lashing.py` (read only). All writing is in this
folder. No sealed data opened. No checker, engine or `src/` edit. All maps are TRAIN development data.

Judgement calls, recorded because nobody could be asked:
- Budgets are 240 s proposal and 120 s finish, against c2-layout's 120 s/45 s. Passing maps stop at about 30–80 s; the
  larger budget matters only on maps that fail.
- The frozen run covers all 48 maps. The 42 not used for development are reported apart from the 6 that were.
- The c2-layout baseline of 13 contains warm plans; this run is entirely cold.
- The 48-map run exceeds 20 minutes because it is a whole predeclared ladder; no single map takes more than about 8.

## A. Diagnosis

**The brief's premise needed correcting.** The "ratios of 1.01 to 1.12" are the *first* violation printed for each map,
not the worst. Across the closest saved stow of each of the 35 failed maps (`diagnosis/chains.json`, recomputed with the
checker's own load graph; `FAILURES.md` lists the same maxima as "largest saved securing ratio"):

| Measure | Value |
|---|---|
| Worst securing ratio per map | min 1.73, median 3.50, max 5.68 |
| Stack end-chains over 1.0 per map | min 18, median 44, max 102 |
| Which limit binds, of 1,610 chains over | twistlock tension 1,284; rod 322; racking 4; post 0 |
| Which joint | tension: joint 0, the base of the stack, in every one of the top categories |
| Stack height, of 1,610 | 4-high or more 1,512 (94 %), 5-high or more 1,202 (75 %) |
| Mean stack mass, sum of box masses | 57.6 t over the limit, 28.2 t for planned stacks within it |
| Background cargo | 0 chains over: every failing chain is entirely planned boxes |
| Worst voyage condition | departure, in 81 of 81 failed stows |

Position barely matters. `at` depends on height only, not on bay or row, and the 1,610 are spread across rows 1–8 evenly
(192–216 per row) and across every 40-foot bay. Bases are the three ships' deck heights.

**Actual GM, against what the page oracle priced.** Failed stows have GM 1.27–2.90, passing ones 1.41–2.40, so GM does not
separate them. Re-evaluating every failed stow at GM 0.5 m cuts the worst ratio by a factor of 1.31–1.69 (median 1.48), and
35 of 35 still exceed 1.0, median 2.34. The same stow with wind removed entirely still exceeds 1.0 on 34 of 35. So GM is a
real 30–70 % multiplier, but **no failed stow can be rescued by GM or ballast**. Acceleration is not the driver; mass high
in tall stacks is.

**What actually drives it.** A deck stack's tension at the base joint is a linear function of tier masses at fixed motion
(see `rows.py`). On `feeder_800` at GM 1.9, 10 t added at each tier of a 5-high 40-foot stack changes that row by:

| Tier | 1 | 2 | 3 | 4 | 5 | Wind, no mass at all |
|---|---:|---:|---:|---:|---:|---:|
| Ratio per 10 t | −0.09 | −0.03 | +0.09 | +0.29 | +0.50 | +0.58 |

Mass in the two lashed tiers helps. Mass at tier 5 costs five times tier 3. Wind alone uses 58 % of the row on a 5-high
stack. Largest *uniform* box mass the checker allows, same stand: 2-high 33.7 t, 3-high 16.0, 4-high 8.2, 5-high 1.6, 6-high
none. The failed 5-high stacks carry 13–20 t on every tier. The passing ones read like 16, 14, 6, 2.5, 2.5.

**Why the pipeline built those stacks: it never priced securing in any stow it emitted.** This is the real cause.

| c2-layout constructor attempts on failed maps | Attempts | Median LP boxes uncovered | Attempts yielding a stow |
|---|---:|---:|---:|
| Securing oracle on | 70 | 41.8 (max 139.8) | **0** |
| Securing oracle off | 70 | 0 | 29 |

All 81 failed stows came from oracle-off attempts or the oracle-free fallback. The reference motion was not the problem
either. The loss is in pricing: `knapsack` fits one line through `Fast.weights` at a single typical column (22, 17, 12,
7, 4 t), clips negative weights to zero and refuses what fails, so it cannot see that tiers 1–2 relieve the row. On
`800/f70-high/101` the page LP left 44 boxes uncovered; the compact LP below, same motion and same pages, covers all 189.

## B. The price of securing

Arms on three maps, same reference GM, one dive then exchange, full check. `price.py`, `price.json`. Each cell is lifts,
then pass or fail with the exact worst ratio. These are single runs: arms differ by an LP vertex and a dive, so read a gap
under about 10–25 lifts as noise. Lifts count D15 projected later-port work.

| Arm | 500/f70-low/001 | 800/f70-high/101 | 800/f80-low/102 |
|---|---|---|---|
| Securing unpriced | 91 fail 1.76 | 127 fail 1.82 | 165 fail 2.31 |
| Limit 1.00 | 93 pass | 129 fail 1.009 | 167 fail 1.021 |
| Limit 0.95 | 93 pass | 159 pass | 169 fail 1.002 |
| Limit 0.90 | 85 pass | 133 pass | 163 fail 1.05 |
| Limit 0.85 | 91 pass | 135 pass | 167 fail 1.012 |
| Deck capped at 4 tiers | 83 pass | 141 pass | 179 fail 1.002 |
| Deck capped at 3 tiers | 91 pass | 159 pass | no stow |
| No port layout, covers spread | 117 pass | 197 pass | 243 pass |

**Securing itself is cheap.** Best legal plan against the illegal unpriced one: 83 against 91 and 133 against 127, so at
most about 6 lifts on these two maps, within noise. On f80 the only pass in this table costs +78 through spread covers, but
the frozen method passed that same map at 159 with a reference GM of 2.25 (161 at dev), below its unpriced 165. The hatch
objective is not the villain the brief suspected: it does not need to give way. Spreading covers buys securing slack at
+24 to +78 lifts and was not needed on two maps of three. Tier caps were unnecessary on all three: the LP chooses tall
stacks with light tops. Limits under 1.0 are needed for a margin against GM and true heights, not because they cost lifts.
Sensitivity worth stating: at f80 the same map passes or misses by 0.2 % depending on reference GM.

## C. Method

### The graph, on `feeder_800/f70-high/seed-192200101`

Bipartite graph: 189 box vertices on the left, one slot vertex per tier of 174 stack pages on the right, 50,792 eligible
edges. A stow picks one edge per box. Cells are a second kind of vertex, because a 40-foot slot covers two: each cell takes
at most one chosen edge. Each deck stack is a **machine** and its boxes are **jobs**. The machine carries dozens of capacity
rows, one for each joint, stiffness and limit. An edge's load on a row is `A[r,k]*mass + Bt[r,k]*wind + Bl[r,k]*wind`.

Plain words: instead of listing whole stacks and asking which to use (the page master), ask of each box which slot it
takes, and let the LP see the true capacity rows directly.

### The fact that makes it work

Every load the checker computes on a deck chain is linear in (tier mass, tier occupied): `_end_response` solves a fixed
linear system and moments are forces times fixed levers. An empty upper tier is mass 0 and wind 0, so **one row set serves
every height from 2 up**. `test_rows.py` shows the rows equal the exact chain terms to 1e-9 relative, including with empty
tiers. Fiedler and Pták's M-matrix result underlies the positive rod response; rows have mixed signs, which is the point:
the tension row is not a positive system, and the earlier clipped fit threw that away.

The LP also carries, as linear rows: one box per cell, tier support, E1/E2 weight order (a box above class r+tolerance needs
a lower box above class r), stack closers, one DG per stack, stack mass and height ratings, sight roofs at true heights,
E9 envelope conflicts, LCG/TCG/KG targets, and soft restow rows. It solves in about 2 s on 800-TEU maps with HiGHS, one thread.
That one LP is why visibility, trim and draught stopped failing on these maps too.

### Steps (`method.py`, `gap.py`)

1. Assignment LP at a reference GM, securing rows at 0.95, overage soft.
2. Dive on stacks: fix what the LP is surest of, bottom-up, re-solve, finish with an exact CP-SAT tail (one worker).
3. If the dive fails, a cover-first page stow.
4. Exact exchange: swap a box of an overloaded stack with a lighter one, or move it to a free top. This is a heavier change
   than fixed-topology permutation: moves change stack heights and hence layout.
5. CP-SAT large neighbourhood repair on overloaded stacks plus light deck and hold stacks.
6. Replay. Actual GM sets the next round's reference (outer loop, 5 rounds at most), failed rules shift targets.
7. Full checker, then the checked ballast search of `method_dense.py`.

### Papers

- Lenstra, Shmoys and Tardos (1990), *Approximation algorithms for scheduling unrelated parallel machines*, Math.
  Programming 46, 259–271, https://doi.org/10.1007/BF01585745. A basic LP solution has a pseudoforest of fractional jobs, so
  rounding adds at most one job to each machine.
- Shmoys and Tardos (1993), *An approximation algorithm for the generalized assignment problem*, Math. Programming 62,
  461–474, https://doi.org/10.1007/BF01585178. Same load bound at no more than the LP's cost.
- Danna, Rothberg and Le Pape (2005), *Exploring relaxation induced neighborhoods to improve MIP solutions*, Math.
  Programming 102, 71–90, https://doi.org/10.1007/s10107-004-0518-7. Use the relaxation to choose what to re-solve.
- Shaw (1998), *Using constraint programming and local search methods to solve vehicle routing problems*, CP98, LNCS 1520,
  417–431, https://doi.org/10.1007/3-540-49481-2_30. Free related items, re-insert exactly.
- Fiedler and Pták (1962), via `c3/c3-feasproof/rod_notes.md`, for inverse-positive spring chains.

**What these theorems do not give us.** Both GAP theorems assume one capacity row per machine. We have dozens, plus side
rows, so no approximation guarantee carries over and none is claimed. They supply the design: LP first, rows set below
the true capacity because rounding adds roughly one job's load (hence 0.95), and expect few fractional jobs. A direct
LST/Shmoys–Tardos matching round was not built: variable-by-variable and whole-stack fixing both showed that fractions safe
alone break a row together (LP infeasible after fixing 40 of 118 boxes), which is exactly the additive job of the theorem,
and exact exchange plus CP-SAT were the practical answer. The method is a heuristic.

## D. Validation

Frozen code: `FROZEN-SOURCES.txt`, verified unchanged after the run. Through `lab/score.py`, 240 s proposal, 120 s finish,
2 tries, 2 workers, seed 0. One-minute load 22–37 during the run, so wall times are load-dependent. Every run is cold.

| Rung | Maps | Complete replay-legal | Full pass, this method | c2-layout | New passes | Lost | Mean lifts of passes |
|---|---:|---:|---:|---:|---:|---:|---:|
| f60 | 12 | 12 | **11** | 8 | 3 | 0 | 97.2 |
| f70 | 12 | 12 | **12** | 4 | 8 | 0 | 113.2 |
| f80 | 12 | 12 | **5** | 0 | 5 | 0 | 156.8 |
| D185 medium12 | 12 | 11 | **0** | 1 (warm) | 0 | 1 | — |
| All | 48 | 47 | **28** | 13 | 16 | 1 | |

Maps not used in development: **25 of 42** against c2-layout's 13 (f60 11/12, f70 10/10, f80 4/10, D185 0/10). The six dev
maps gave 3 of 6. Union with the warm D185 witness is 29 of 48.

- All 28 rechecked by `recheck.py` in a fresh process: replay legal, every box loaded, zero violations, lifts equal to the
  scorecard. Smallest securing margin 0.005. `RECHECK.json`.
- Mean wall 58 s on passing maps, 314 s on failing ones.
- Lifts, 12 maps both passed: 88.0 against 99.0. f70: 91.5 against 112.5, better on 4 of 4. f60: 86.25 against 92.25, but
  worse on 6 of 8, where c2-layout's warm fixed-charge search beats one cold stow. Passing-only means of the rungs use different
  map sets and are not comparable. All lifts are D15 projections. No fresh 0.9.0 arm was run, so nothing here is a
  comparison with 0.9.0, and V6 is not used as a benchmark. c2-layout already had the E9 fix, so none of the 16 new passes
  is an E9 bug-fix credit. 20 maps remain unknown, not infeasible.

**Where it fails.** 20 maps, `summary.py`:
- Mostly the stacking of two gaps. 13 of the 20 fell to the page fallback in at least half their rounds because the dive
  gave no stow: that stow ignores sight and moments, and exchange cannot rescue it (draught, trim, visibility, ratios
  1.3–5.4). Three of the four 428-box maps are here; the fourth returned no candidate. On the dev 428-box map the first round alone took 113 s even with `PAIRS_PER_BOX` sampling.
- Five are securing-only at 1.06–1.17 (500/f80-high/002, 500/f80-low/000, 800/f80-high/102, random 185100100 at 106 lifts,
  sim 185100111). Close, and all in the same pattern as the f80 maps that passed.
- 800/f60-high/100 fails only E6 heavy-under-deck, ratio 0.68; 800/f80-high/101 fails only E9 width, ratio 0.96. E6 is not in
  the LP. The E9 envelope rows did not catch that one.
- The warm 121-box D185 pass is not reproduced cold: best cold stow is at ratio 1.46 and also misses draught, trim, sight.

**Regression: this method must not run on small maps.** `quick`, frozen code, against stored rows:

| Set | This method | Stored 0.9.0 | Lifts |
|---|---:|---:|---|
| tiny | 8/8 | 8/8 | 7.375 against 6.375, +1.0 (95 % [+0.25, +2.0]), 0 wins, 5 ties, 3 losses |
| small | **0/10** | 9/10 | none paired |
| medium | 2/3 | not measured | |

Small maps fail on draught in all 10, and on securing and visibility in 8. The likely cause, not measured, is that the targets (cargo LCG at 48 % of LPP, KG from hull
depth) were set for dense medium ships and don't fit these. Stored rows are not a fair comparator for lifts, but a loss of 9 passes needs no fair arm. **Decision: route by
size. This is a medium lane, and the engine keeps tiny and small.** The frozen method has no such guard, so the caller must
apply it; `method.py` is not a whole-range replacement.

**Verdict: keep, as the medium lane.** Tests: 9 passed (`test_rows.py`, `test_gap.py`).

Next, in order:
1. Make the dive always complete: put E6 in the LP and fix the dive's end-game, since fallback rounds lose nearly every D185 map.
2. Spend spare budget on the five securing-only maps: exchange that also moves GM down.
3. Speed the 428-box LP by column generation over the box-slot edges.
4. Fixed-charge LNS from c2-layout on these stows, for lifts on the f60 maps where warm search still wins.
5. Size guard, then rerun `quick`.

## What failed along the way

- CP-SAT restricted to the LP support plus each box's 10 cheapest reduced costs: infeasible on 4 of 5 dev maps.
- Bottom-up variable dive: stalled at 60 LPs with coverage lost.
- Closing a stack once fixed: lost capacity fast at f80. Stacks now stay open above their fixed tiers.
- Hard securing rows inside the dive: infeasible once a fixed stack read 0.97 at true heights. Rows are now soft and repair
  enforces them.
- Rows at one nominal height: high-cube stacks came out at 1.05–1.20. High-cube boxes now carry their own coefficients.

## Reproduce

```bash
. experiments/mvp/frontier/lab/c5/c5-securing/env.sh; C=experiments/mvp/frontier/lab/c5/c5-securing
nice -n 5 $PY experiments/mvp/frontier/lab/score.py --set $C/all48.txt --method $C/method.py:propose \
  --finish $C/method.py:finish --budget-s 240 --finalize-s 120 --tries 2 --workers 2 --seed 0 --out $C/results/frozen-48
```

`diagnose.py` and `diagnose2.py` rebuild `diagnosis/`. `tiers.py` prints the tier table. `price.py` measures part B.
`FROZEN-SOURCES.txt` holds the runtime hashes from before the frozen run.
