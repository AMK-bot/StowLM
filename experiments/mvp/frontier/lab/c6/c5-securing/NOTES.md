# Cycle 6: securing failures, GM ceilings and ballast shaping

Cold throughout. All maps are TRAIN development data. Code copied from `lab/c5/c5-securing/`, which is untouched. No checker,
`src/` or `engine/` edit. Budgets 300 s proposal, 180 s finish, 2 tries, 2 workers (D167). New dev maps were declared in
`DEV-MAPS.md` before tuning. `FROZEN-SOURCES.txt` was verified unchanged after the final run.

## Results (frozen, `results/frozen-48/`)

| Set | Maps | T5 | T4 | T3 | T2 | T1 | T0 only | Not completed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Cycle 6 | 48 | 36 | 0 | 0 | 0 | 0 | 0 | 12 |
| Cycle 5 | 48 | 28 | 0 | 0 | 0 | 0 | 0 | 20 |

This table counts plain scorecard rows: no peel was run, so "not completed" here means no full pass, not that a lower tier failed.

| Rung | Maps | Passes | Cycle 5 |
|---|---:|---:|---:|
| f60 | 12 | 12 | 11 |
| f70 | 12 | 12 | 12 |
| f80 | 12 | 9 | 5 |
| D185 | 12 | 3 | 0 |

- **No pass lost.** Gained 8: 500/f80-low/000 (141), 500/f80-low/001 (131), 800/f60-high/100 (135), 800/f80-high/101 (176),
  800/f80-high/102 (197), random 185100100 (112), random 185100101 (239), sim 185100111 (197). These are the first cold D185 passes.
- Not used in development: 29 of 36 (25 in cycle 5). The 12 dev maps: 7 (3 in cycle 5).
- Mean lifts on the 28 maps that passed both times: **116.7, against 114.7**: 14 better, 11 worse. Lifts are D15 projections.
- All 36 rechecked in a fresh process: `RECHECK.json`. Smallest securing margin is 0.00014, so some passes are narrow.
- Mean wall 69 s on passes, 558 s on failures. Load 12–42, so wall-bounded search varies: 185100100 read 108 in dev, 112 frozen.

Still failing, 12: 500/f80-high 000, 001, 002; 185100000, 185100001; sim 185100010, 011, 110; all four 428-box maps. "No plan
found", not impossible. The warm 114-lift plan for 185100000 is still not reproduced cold.

## What was actually wrong

1. **A rounding bug, mine.** CP-SAT slack on tonne-metre moment rows rounded to 0, so soft moment targets were hard in the tail.
   That is most of why the dive "failed to complete". Fixed in `gap.py:tail`.
2. **Ballast was never a decision, and this is the big one.** Maps start with full double bottoms, the stiffest condition.
   On one 229-box stow, draining them took GM from 2.18 to 1.35 and the ratio from 1.55 to 1.31 with no stability margin among the
   worst seven going negative. `method.py:shape` drains in eighths, keeps the ship level with the heel pair and under the draught
   limit, and stops at the most ballast that passes. 18 of the 35 passes decided in the proposal needed some draining; the 36th, 500/f80-low/000, passed through the ballast finisher. The dive reference GM changed to
   suit: 7.5 % of beam.
3. **The GM loop was not the near-miss problem.** Reference and actual GM were within 0.05. Exchange stalled. A motion reference per
   voyage condition wasn't needed: departure is worst on all 81 failed stows of cycle 5.
4. **E6 and E9.** Heavy-on-deck cost in the LP plus `fill_hold` moves a heavy box to the free hold slot the rule names. Exchange now
   leaves OOG boxes and the cells they reach alone.

New move: `gap.py:sweep`, a very large neighbourhood (Ahuja, Ergun, Orlin and Punnen 2002, *A survey of very large-scale
neighborhood search techniques*, Discrete Applied Mathematics 123, 75–102, https://doi.org/10.1016/S0166-218X(01)00338-9).
Hold all stacks fixed but one tier, or deck tops with the hold; each box then has a cost at each position, and one assignment
problem picks the best permutation of hundreds of boxes at once. Plain words: instead of swapping two boxes, re-deal a whole tier.

The new tradeoff: drain too far and the ship goes tender. On the failing 121-box and 500/f80-high maps the stows clear securing
(0.93–1.00) at GM 0.5–1.05, then fail the containership second check, 0.28 against about 0.30, or weather. The stows are too
top-heavy to hold GM. These maps need GM of about 1.3 with securing still under 1.

## Aggregate capacity bound (`capacity.py`, `capacity.json`)

**Question:** is there enough deck capacity under the securing polytope? Stack columns are grouped by (base, tiers, standing height);
each stack's rows are summed over its group, which any real stow must satisfy. Box classes go in 0.5 t buckets, and every
coefficient is a lower bound. LP of a few thousand variables, about 1 s, HiGHS one thread.

**Finding: zero map-level infeasibility proofs. 14 of the 20 maps have a certified GM ceiling instead.** On those 14 the LP is
short of room for boxes (0.47 to 32.9, 44 bands of 148) above a band edge, safe dual bounds within 5e-5 of the LP values:

| Band edge GM above which no securing-legal stow exists | Maps |
|---|---|
| 1.5 | 500/f80-high/000, sim 185100011, sim 185100110, sim 185100210 |
| 2.0 | random 185100001, random 185100200, sim 185100010 |
| 2.5 | 500/f80-high/001, 002, random 185100000, random 185100201 |
| 3.0 | 800/f80-high/102, sim 185100111, sim 185100211 |
| None below the D56 cap | 500/f80-low/000, 001, 800/f60-high/100, 800/f80-high/101, random 185100100, 185100101 |

So the answer is yes at low GM on every map, and no at high GM on 14. Of those six with no ceiling, all six now pass; of the 14 with
a ceiling, 2 pass. The 428-box stows sat at GM 2.6–2.9, far above their ceilings, which the capacity numbers explain.
A map proof would need a certified GM floor from stability, and we only have the 0.15 m statutory floor, where every map stays open.

**Assumptions of the band statement:** (1) T5 plan: all boxes aboard, departure mean draught under `max_draught_m`, GM in the band.
(2) The engine's static eligibility and E1/E2 semantics are necessary conditions. (3) Each cell lies in exactly one long slot with
both ends at one tier, checked per map (`aligned`, true on all 20), so a chain never changes column. (4) Transverse acceleration on
deck rises with GM and falls with draught, from `_motions`. (5) Only tension and rod rows are used, each a true lower bound of a
checker ratio; lever arms range over all height combinations including OOG envelopes. (6) Single-box stacks carry no row. (7) HiGHS
floating arithmetic; the safe dual bound charges reduced costs at variable limits but isn't exact-rational.

**Controls:** `proofcheck.py` puts all 28 cycle 5 passing plans through the per-stack rows in their own GM band: every row holds,
worst 0.962. None of the 28 maps has a ceiling (plain-height run, `capacity-plain.json`). 8 maps from the 20 have since passed, each
inside its band.

**Not built: stack height as a decision.** The aggregate's height groups already are that decision in relaxed form. The LP steers
GM; it doesn't yet steer the dive by tier targets. That is the natural next step.

## 428-box maps

Not solved: 0 of 4. `rolling_dive` resamples free slots each step and fixes about 330 of 428, then coverage is lost and the tail is
infeasible, so rounds drop to the page fallback (ratios 4+). LP root takes about 15 s at 80 pairs per box, so the LP is no longer the
blocker. The end-game is.

## Small-map regression, closed

Cause in one line: fixed background cargo plus full double bottoms left small ships about 0.1 m over the draught limit, and the
method never touched ballast. `shape` fixes it without a change aimed at it: `quick_small` 9 of 10 at 120 s/60 s/1 try, against 0 of
10, and stored 9 of 10, mean 16.1 lifts against 13.9 (+2.2, 95 % [−2.7, +6.4]) on stored rows, which aren't a fair comparator.
Tiny wasn't rerun. Keep routing tiny and small to the engine: it is cheaper in lifts.

## Next

1. Tender maps: steer stows to GM of about 1.3, using aggregate tier targets and a KG-lowering sweep.
2. End-game for 428 boxes: soft coverage in the tail, then insertion.
3. A certified GM floor would turn ceilings into map proofs.

## Reproduce

```bash
. experiments/mvp/frontier/lab/c6/c5-securing/env.sh; C=experiments/mvp/frontier/lab/c6/c5-securing
nice -n 5 $PY experiments/mvp/frontier/lab/score.py --set $C/all48.txt --method $C/method.py:propose \
  --finish $C/method.py:finish --budget-s 300 --finalize-s 180 --tries 2 --workers 2 --seed 0 --out $C/results/frozen-48
```
Tests: 9 pass (`test_rows.py`, `test_gap.py`). `summary.py` compares with cycle 5. `capacity.py` and `proofcheck.py` give the bound.
