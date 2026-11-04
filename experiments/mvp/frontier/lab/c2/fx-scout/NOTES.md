# fx-scout, cycle 2: what a pattern is, and which physics it carries

Scope: only this folder is written. `pages.py`, `method.py` and `lash_oracle.py` were copied from `c1/fx-scout/` and changed here. `physics.py` is new. `lab/c1/leader/e9fix.py` is imported and installed. `c1/fx-chains/mixed_patterns.py` gave the geometry of a mixed column; its code wasn't copied. Nothing in `src/` changed.

## What changed, in plain words

**OOG reservation, instead of a repair.** The E9 lookup fix alone didn't solve the 116-lift stow. The failing box there was a width-OOG box placed in the hold, where E9 refuses it, and hard against neighbours. E9 is geometry: the box's expanded envelope may not meet a neighbour at the same height. So each horizontal OOG box now gets one deck slot before pricing starts, and the stacks it would touch are capped below it. Neighbours come from real rectangles. `clear_oog` is gone.

**Per-stack sight roofs.** SOLAS V/22 as the checker writes it: a point at height h above the sea, at horizontal distance r from the conning eye, hides sea beyond R when h_eye × r + R × h > R × h_eye. So a stack with its whole roof under h_eye × (1 − r/R) adds no blind sector. Every stack gets its own roof from its own position. On the 162-box map that reads 4.4 tiers at bay 1 and 6.3 at bay 27. This is sufficient, not necessary: the rule tolerates small sectors. It replaces the global tier cap. One margin, 0.7 m after the plain stow fails sight, handles other voyage draughts.

**Mixed pages.** A deck pair with 1 or 2 levels of paired 20-ft boxes under long boxes. Pricing fills positions in a fixed order (left, right, left, right, then longs), so the same knapsack works. Only standard-height 20-ft boxes go in, which keeps both sides level. A long box stands on the outer end frames, so each side is its own securing chain, and the column's ratio is the larger of the two.

**Securing read from the current stow.** Each rung rebuilds the oracle and the roofs from the ship the last rung built, not from one reference.

**Tail and polish.** The end of a dive can now top up columns already chosen. When strict securing fails, it finishes with securing off and `polish` repairs the columns on the finished ship, by swapping a box for a lighter one or shifting it elsewhere.

**Not done: exact securing in the knapsack (step 5).** The ratio is a max of nonlinear pieces, and the knapsack still uses a fitted linear form plus an exact check on each priced pattern. So the LP's "proved" flag is still no bound. Mixed pages made this less urgent than the securing excess, so I left it.

## Results

Gate wording: a **complete stow** means every box loaded and legal in real replay. A **pass** means the full checker on the finished plan.

Code hashes: `results/source.sha256`. Seed 0, budget 300 s, 2 workers. Runs: `results/quick20-1` (quick less the D185 map), `results/medium14-1`. The three plans below were checked again in a separate process (`audit_passes.py`, `results/audit.json`): passed, 0 violations, all boxes loaded, input hashes match.

| Set | Complete stows | Full-checker passes | 0.9.0 passes | Mean lifts, paired with 0.9.0 | Mean wall |
|---|---:|---:|---:|---|---:|
| Tiny, 8 | 8 | 8 | 8 | 6.88 vs 6.38, W/T/L 0/6/2 | 22 s |
| Small, 10 | 10 | **10** | 9 | 14.00 vs 13.89 on 9 (95 % −3.33 to +2.67), W/T/L 1/4/4 | 74 s |
| f60 medium, 2 | 2 | 2 | no row | none paired | 224 s |
| D185, 8 of 12 run | 8 of 8 | **0** | no row | none | about 300 s |

The four 428-box D185 maps weren't run on the final code: I stopped the run at 86 minutes to fit the regression in, and they stalled on time in cycle 1.

| Map | V6 | Cycle 1 | Cycle 2 |
|---|---:|---:|---:|
| 88 boxes, f60 | 131 | 79 | **71** |
| 162 boxes, f60 | 214 | 184 | **116** |

`sim.small.40100034` passes at 10 lifts through the V4 finisher (25 s): a map 0.9.0 fails.

**The medium budget matters.** At 300 s under load 18–30, only 1 of the 8 D185 maps finished a stow past the plain rung, and the last rungs ended with their LP unconverged (22–185 boxes uncovered when time ran out). With mixed pages on, the LP needs about twice as long. So this 8-map run says little about the method's reach, and the budget-480 runs on `random.medium.185100000.0.2` are the better evidence.

## D185: how close

`random.medium.185100000.0.2`, 121 boxes, 80 % fill, default tanks.

| Stow | Lifts | Securing, worst | GM second check | Draught | Trim |
|---|---:|---|---|---|---|
| Pure tail, lash 1.0, LCG 55.97 | 114 | 1.015 lashing | 0.277/0.280 departure, 0.26/0.296 arrival | 7.08/7.05 fails | −1.186/−1.078 fails |
| Relaxed tail, LCG 54.47 | 106 | 1.20 racking, after polish | fails | passes | passes |

So the two kinds of stow fail in different places. Moving the cargo aft by 1.5 m fixes draught and trim, but the LP is then 2.1–2.5 boxes short on securing, and the relaxed tail overloads a few columns that polish can't clear. `polish` left 2 of the columns over limit. Reading: the combination of securing, fore-aft balance and fill is the wall, with only about 2 boxes of slack missing in the fitted model.

No D185 stow reached the finisher, because the method sends one only when every failing rule is `iscode.*`.

## What failed

- **E9 lookup alone:** no help on the 116 stow. The fault was placement.
- **`polish`:** clears small excess, not the 1.2–1.5 ratios a relaxed tail leaves.
- **`lift_weight` ladder:** multiplying the height cost tenfold per failed GM check didn't clear the GM rule; at 0.2 the dive stopped closing.
- **Mixed pages on D185:** LP shortfall 2.36 → 1.75 on the first rung, a real gain, but no pass.

## Long-run note

The medium run may pass 20 minutes because it covers the fixed cohort.

## Next

1. Securing-aware repair: move two boxes at once, or re-price the few bad columns and their neighbours with the LP, in place of `polish`.
2. Fix cargo at the LCG that clears trim, and let securing decide fill by map: try 20-ft bases on 3 levels, and high-cube 20s in a base.
3. Send near-miss stows to a finisher that can trade GM and trim (`c2-benders`).
