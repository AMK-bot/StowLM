# D152 final validation (validation only; no test or showcase input)

**Sets:** `experiments/mvp/valsplit/reserve_small25.txt`, `tiny_val48.txt`, and audit30 (hashes in `SPLIT.json`).
**Recipe:** `run_adapter.py`, the parent-audit code path (`llm.exit.runner.evaluate`): batched greedy, mask + securing +
look-ahead, no rewinds, V4 finisher 120 s, full checker, mask replay. Two-step: `run_twostep.py` (`ablation/twostep_val.py`, 120 s).
**audit30:** 8.2 at 120 s is `deployment-d137/audit-baseline`, identical on all 30 maps to the 90 s parent audit.

## 8.2 and two-step so far (30 Oct, 10:44 EDT)

| Set | Arm | Passed | Mean moves (own passes) | Not passed |
|---|---|---:|---:|---|
| reserve25 | 8.2 | 18/25 | 26.94 | 6 dead ends, 1 finisher time limit |
| tiny val48 | 8.2 | 47/48 | 9.40 | 1 dead end |
| tiny val48 | two-step v4.4 | 47/48 | 6.55 | 1 dead end |

Tiny, 47 mutual maps: 9.40 vs 6.55, 8.2 fewer on 2, two-step fewer on 30, equal 15 (exact sign p ≈ 2.5e-7).

**Reserve25, two-step v4.4 (loop stopped):** 21/25, 22.57 mean on own passes (3 dead ends, 1 finisher time limit). On the 18
mutual maps: 8.2 26.94 vs two-step 21.72, 8.2 fewer on 4, two-step fewer on 9, equal 5 (sign p = 0.27). Passes: only two-step 3,
only 8.2 0 (p = 0.25). Not significant at n = 25.

**Finisher diagnostic (not part of the rule):** 8.2 lost `random.small.40100047.0.8` to the 120 s finisher limit while sharing the
machine with the loop. Re-verifying the same plan after the loop stopped timed out again at 120 s
(`8.2/reserve25/diagnostic-40100047.json`), so that row isn't a load artefact and the D152 count stands.

## Step 5

When `8.3/{audit30,reserve25,tiny48}/rows.json` exist: `.venv/bin/python experiments/mvp/sft/final-validation/apply_rule.py`.
It writes `experiments/mvp/sft/FINAL-CHOICE.md`, or a NOT DECIDED file with exit code 2 if a set is incomplete, a check was
unavailable, securing failed or the epoch differs. Tests: `pytest --noconftest test_apply_rule.py` in this folder.
