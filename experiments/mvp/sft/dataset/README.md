# 7.1 MVP SFT dataset tooling (v4; fix-t8-sft, 29 Oct 2026)

`build.py` turns checker-passed plans into `train.jsonl` / `val.jsonl` / `stats.json` / `build_report.json` for
`llm.stowlm.train`. The dataset kind is **`mvp_gold`**, which train.py admits as clean (no `--diagnostic`), so the
adapter can ship.

**Admission, per row** (`llm.stowlm.sft_data.build_mvp_example`; the trainer re-checks with `mvp_row_failure`):
1. The full-checker report passed, carries no stubs, and is bound to this exact (instance, plan)
   (`report_matches_inputs`).
2. The plan is serialised in format stowlm@1.0, prompt 2.2 (rule freeze OFF).
3. **Every completion token replays through the mask on the D79 engine** (`FeasibilityConfig(extra={"global_screen":
   0})`, no stub) and ends `complete`, with no masked token. The replay is hash-bound to the row.
4. Length is at most the 5.9 cap (10,240). Longer rows are dropped and counted, never truncated.
5. Inputs from the frozen test split are refused (`corpus/test/SHA256SUMS`), and an input can't sit in both train
   and val.

**APPROXIMATION:** no v3 split-v2 pins or published-shard lineage. The MVP corpus's seed-disjoint splits are the split
authority (D78/D79).

**Inputs:**
- mvp-gold case folders (`experiments/mvp/gold/<split>/<rung>/...`: `result.json` + `plan.json` + `checker.json`
  + `case.lock`). Failed cases count as `not_gold`.
- Harness/refs `record.json` files.

`--holdout-val K` takes K passing train cases as val when no val gold exists yet.

```
uv run python experiments/mvp/sft/dataset/build.py --out <dir> --train experiments/mvp/gold/train \
  --val experiments/mvp/gold/val
uv run python experiments/mvp/sft/plan_run.py --data-dir <dir> --adapter-dir <out>   # 8.1 command, ≤ 8 h check
```

**Verified (29 Oct):**
- **15 tiny two-step test reference plans** (stubbed fixture under `../sft-pilot/fixture-twostep-tiny`): 15/15 D79
  replay; 7.27 completion tokens per action; about 600 tokens per row.
- **The first 4 real tiny gold cases:** 4/4 admitted and replayed.
- **Tests:** `tests/test_mvp_gold.py` (7) cover admission, the hash binding, a failed report, the test split, the
  taint refusal, and trainer admit/refuse. The existing SFT and train suites pass.
