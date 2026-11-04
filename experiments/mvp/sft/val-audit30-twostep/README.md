# Two-step on the 30 validation audit maps

**Validation only, not 9.1. Test v2.1 was not touched.** The write-up's missing comparison for the 8.1 vs 8.2 parent audits
(D147): `experiments/mvp/valsplit/audit_small30.txt`.

**Recipe:** live `TwoStepPlanner` (`twostep.py` sha256 `78dec70b…`, the v4.4.0 hash in `docs/evaluation.md`), D79 C2 off,
V4 finisher scoped to finalize, one finisher attempt (D139), full checker, no repair, via
`experiments/mvp/sft/ablation/twostep_val.py`. CPU only, 4 workers, one thread each. The 8.1 and 8.2 rows come from
`experiments/mvp/exit/deployment-d137/parent-audit-{8.1,8.2}/audit-baseline/summary.json`. Both report the checker's
`weighted_nonproductive` (`src/llm/exit/final_recipe.py` holds them equal).

**Budgets:** 90 s (same as the parent audits) and 120 s. Results are identical map by map: the longest finisher call was
23 s in the 90 s run and 43 s in the 120 s run, so neither budget binds. Each run took 3.5 min wall.

## Result

| Arm | Passed / 30 | Mean on own passes | Mean on the 20 maps all three pass |
|---|---:|---:|---:|
| 8.1 | 22 | 35.32 | 36.20 |
| 8.2 | 26 | 32.85 | 33.05 |
| two-step v4.4 | 29 | 26.03 | 26.40 |

| Pair (A vs B) | Passes: only A / only B (sign p) | Mutual maps | Mean A vs B | Fewer moves A / B / equal (sign p) |
|---|---:|---:|---:|---:|
| 8.1 vs 8.2 | 2 / 6 (0.289) | 20 | 36.20 vs 33.05 | 7 / 11 / 2 (0.481) |
| 8.1 vs two-step | 0 / 7 (0.016) | 22 | 35.32 vs 26.32 | 2 / 16 / 4 (0.001) |
| 8.2 vs two-step | 0 / 3 (0.250) | 26 | 32.85 vs 26.15 | 7 / 16 / 3 (0.093) |

Two-step passes every map either adapter passes. Its one failure, `sim.small.40100038`, is a planner dead end, and neither
adapter passes that map. On the 26 maps both pass, 8.2 uses 25 % more weighted moves. Exact two-sided binomial on
discordant maps, ties dropped, as in the D147 correction; the 8.1 vs 8.2 row reproduces that correction. No multiple-test
adjustment. 8.2 vs two-step is **not significant** at n = 30 on either measure.

Limits: 8.1 and 8.2 were audited with greedy decode at the 90 s budget, and the loop's own gate uses 120 s. n = 30 small
maps; nothing here says anything about tiny or test.

## Files

`run.py <finalize_s> [workers]`, `paired.py <finalize_s>`, `90/` and `120/` (one JSON per map), `paired-90.json`,
`paired-120.json`.
