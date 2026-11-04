# Best verified teacher per train map (D150, D151, D152)

**Train only. No GPU, no training, no `src/` edits; test v2.1, showcase and val inputs are never opened.** The map list is 8.2's
train rows, and `identity_guard` refuses non-train inputs.

**Result (30 Oct):** `experiments/mvp/sft/stowlm-v3/data`, 1,252 rows (8.2's 1,253 minus one listed map) and 8.2's `val.jsonl`.
Per map the fewer verified weighted moves of {stored gold re-verified now, two-step v4.4}; a tie keeps gold; small
`gold_warmstart` maps keep gold.

| Maps | n | Two-step better / equal / gold better | Mean gold vs two-step | Mean best |
|---|---:|---:|---:|---:|
| tiny | 713 | 276 / 268 / 169 | 9.31 vs 6.74 | 6.27 |
| small, not warm-start | 72 | 32 / 5 / 35 | 24.76 vs 24.43 | 21.22 |

Two-step wins per ship, tiny: feeder_500 72/220, feeder_800 104/243, feeder_1700 100/250. Small: 9/25, 10/22, 13/25. The 467 small
`gold_warmstart` rows passed re-verification and have no two-step plan here. **The labels are best of two, not optimal.**

**Pipeline:** `prepare_teacher_candidates` + `verify_candidate`, the loop's own teacher path: two-step v4.4 (120 s), one V4
finisher attempt (120 s), full checker, layered mask replay, for two-step and stored gold alike.

**Approximation:** the checker runs in the worker, not in a fresh watchdog child per plan, and the audit epoch is hashed every 25
maps and at each worker's end. Same checker, deadline and availability rules. `d151.change_proof` shows nothing changed during
the run, and 30 random winners through the unmodified path agreed 30/30 (`audit-sample.json`).

**Dropped:** `train/small/feeder_500/simulated_yard/seed-030100118`: the two-step finisher hit its 120 s limit twice, so no
best-of verdict exists. Gold verified at 18 on it, but D151 says list and drop.

**Not done:** two-step on the small maps outside 8.2's training set. Rows from before D151 stay in `rows/`; `best/` is an unused
smoke output. `summary.md` covers whatever is in `rows/`.

**Run it:** `d151.py manifest`, `launch.py`, `finish.py` (admit + export, blocking audit, summary, build). Gates and their
regression tests: `d151.py`, `build_83.py`, `audit_sample.py`, `test_d151.py`. Review status: `REVIEW-STATUS.md`.

## D172 all-small completion

Complete (30 Oct 18:28 EDT): [best-d172/summary.md](best-d172/summary.md), **1,446 maps**
(733 small + 713 tiny). The 735 eligible small maps include 26 beyond the original snapshot;
exactly two finisher-limit maps are excluded with leader authorization. The 194 completed new comparisons
add 11 two-step wins; the full export has 1,127 gold and 319 two-step winners, with ties keeping gold.
Fresh blocking audit **30/30**, 65 gate tests, unchanged epoch/source proof, and exact coverage passed.
CPU only; 3 workers for the final audit; ledger released. Existing D151 outputs remain intact.
Run evidence, exclusions, and provenance: [d172/](d172/README.md).
