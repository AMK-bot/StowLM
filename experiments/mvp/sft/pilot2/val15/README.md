# Pilot 2 + pass-rate ablation on val15 (fix-t8-sft, 29 Oct 2026; D110: validation maps, never test)

**Pilot 2** (`../adapter`, clean `mvp_gold` lineage; pilot, not 8.1):
- **Training:** 585 tiny + 267 small train gold rows, 36 tiny val rows, 1 epoch (852 steps), 43.5 min, 257 tok/s,
  18.8 GB.
- **Loss:** val (tiny only) 3.73 → 0.21; train loss in the last reports ≈ 0.58 (they mix tiny and small).
- **Dataset build:** 132 s with `--workers 8`. 888/888 rows replay with D79 + the securing layer.

**Ablation:** 15 small val maps (`valsplit/ablation_small15.txt`).
- **All rows:** mask + D95 securing, V4 finisher scoped to finalize, finisher-aware 90 s per candidate, no repair,
  per-map limit 300 s (600 s for d).
- **Two-step:** `../val15-twostep` (live TwoStepPlanner, same pipeline).

| row | config | shipped (own pass) | outcomes | mean weighted lifts (shipped) | paired vs two-step (n; ours vs two-step) | backtracks | s/map mean / max |
|---|---|---|---|---|---|---|---|
| a | greedy | **8/15** | 5 dead end, 2 finisher time limit | 41.6 | 7: 36.6 vs 23.4 (+13.1; worse on 7/7) | 0 | 127 / 331 |
| c | greedy + backtracking 32 + look-ahead | **10/15** | 3 dead end, 2 finisher time limit | 39.5 | 9: 35.3 vs 22.4 (+12.9; worse on 9/9) | 38 | 139 / 361 |
| d | best-of-2 (greedy + 1 × T 0.7), c's decoding | 3/4 (11 not run; stopped per D116) | 1 dead end | 126.3 | 2: 151 vs 35 | 42 | 247 / 286 |
| e | a / c + 15 s LNS polish | same as a / c | — | unchanged: 0 of 18 plans improved (≈ 12 s each) | — | — | +12 |
| two-step | live, same pipeline | 12/15 | | 24.7 | | | |

**Row c and the rev-v4-h P2 backtracking bug:** unaffected.
- **The bug:** banning one placement could also drop that box's other slots. It lives only in the lazy box-head trie
  (`mask._StateView._build_from_boxes`).
- **Why row c is unaffected:** that trie is built only when the rule-freeze switch is ON. Row c ran with it OFF, so
  the mask used the index path, whose trie counts are per-action counts.
- **Fixed regardless:** in `backtrack.py` (D127).

**Corrected under the one-finisher-attempt rule (D139; `rescored-D139.json`, from the saved records, no new decoding):**
the table above counts plans rescued by a second finisher run, which two-step never gets.

| row | shipped as run | shipped, one attempt | changed maps |
|---|---|---|---|
| a | 8/15 | **7/15** | 40100007: passed only via the last resort after its 90 s budget ran out |
| c | 10/15 | **8/15** | 41100019: last resort (150 s) after its 90 s budget ran out; 40100040: checker rejected the first finisher plan, passed only on the pipeline's second finalize |

- Paired vs two-step on the corrected rows: row a 37.2 vs 22.2 (6 maps); row c 34.3 vs 21.4 (7 maps).
- These maps ran at a 90 s budget. 9.1 uses 120 s, so 40100007 (about 122 s in total) might pass there; that is unmeasured.
- Backtracking + look-ahead still ship 1 map more than greedy alone (8 vs 7).

**Reading:**
- **Backtracking with look-ahead lifts shipped plans from 8 to 10 of 15**, turning 2 of the 5 dead ends into passes.
  Cost: +9 % time per map.
- **Moves remain ~13 lifts worse than two-step** on every paired map, and the polish doesn't close the gap.
- **Row d is too small to judge (n = 4).**

**MLX cache-limit check** (`a-cachelimit-1map/`, same map and config as row a's first map):
- **With `STOWLM_MLX_CACHE_LIMIT_GB=2`:** process footprint 17 GB (`footprint`), against 31–38 GB per process
  without the limit (rows a/c/d).
- **Same plan** (77 lifts). Decode 45 s vs 49 s; MLX peak 16.8 GB vs 17.7 GB.
- **Pipeline 283 s vs 203 s:** that difference is the CPU finisher under machine load, not the decode.
