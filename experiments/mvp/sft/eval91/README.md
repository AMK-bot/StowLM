# 9.1 one-command evaluation (v4; fix-t8-sft, 29 Oct 2026)

`zsh experiments/mvp/sft/eval91/run_91.sh <adapter> <out_dir> [time_limit_s=600]`. Set `RUNGS=small` to restrict
the rungs; the default is all 30 test v2.1 instances (tiny + small). It runs:

1. **`greedy/`:** T = 0, 1 sample → pass@1.
2. **`sampled/`:** T = 0.7, 3 samples, checker-selected.
3. **`combine91.py`:** best-of-4 = the fewer weighted lifts of the greedy and sampled picks (greedy on ties). It
   counts StowLM's own passes only. 5.7 repaired passes go in their own column. It pairs results with two-step v4.4
   on the same inputs. Writes `SUMMARY.md` and `summary.json`.
4. **`polish91.py`:** the separately labelled row "StowLM + 15 s LNS polish". It warm-starts
   `stowage.solver.gold_warmstart.improve_checked` from StowLM's own checked best-of-4 plan, re-checks the result,
   and is never worse than StowLM's plan.

All runs: D79 (C2 off by config), D95 securing layer, V4 finisher scoped to finalize, per-instance resume.

**D157 equal-polish comparison:** the default evaluation covers 30 maps, tiny + small. `combine91.py` reports
"StowLM + 15 s solver polish (hybrid)" and "two-step + 15 s solver polish (hybrid)" as adjacent, separate rows;
neither enters the model's own-plan selection. Two-step's hybrid is admitted only after every rerun outcome matches
its original outcome and objective, all passing plans are polished, and the complete equality guard matches the
original and polished file hashes. StowLM polish caches bind the selected result's hash and requested budget;
legacy unbound caches are recomputed. Partial or mismatched optional hybrids are labelled unavailable while the
original rows remain usable. The hybrid pair reports mutual-map means, fewer/equal/more counts and an exact
two-sided sign test with ties excluded. Use `--sealed` to suppress result values from the combiner's output;
redirect the polish invocation's full stdout/stderr into its sealed output directory as well.

**Tooling check (29 Oct, on the tiny pilot's plans):** `combine91` reproduces the pilot's numbers (8.53, +2.07). The
polish improved 4/15 tiny plans by 1 lift each (mean 8.53 → 8.27, about 6 s per plan, CPU only). When nothing
improves, `improve_checked` reports "Retained checked two-step fallback": the retained plan is StowLM's own
(incumbent), despite the wording.

8.1: `zsh experiments/mvp/sft/run_81.sh` (tiny + small gold; 2 epochs if ≤ 8 GPU-h, else 1). Pilot 2:
`zsh experiments/mvp/sft/pilot2.sh` (≤ 1.5 GPU-h, then this eval with `RUNGS=small`).
