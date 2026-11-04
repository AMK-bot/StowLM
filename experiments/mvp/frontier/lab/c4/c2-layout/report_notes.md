# Fixed-cohort reporting

`report.py` is a JSON-only report builder. It opens saved score rows, the fixed 48-row `MANIFEST.json`, and optional saved bound result files; it never opens instance bytes or runs numerical/checker work. All output paths are fixed within this Cycle 4 folder. Python bytecode is disabled in reproduction commands.

Run after measurements:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 experiments/mvp/frontier/lab/c4/c2-layout/report.py \
  --runs experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70-dense \
         experiments/mvp/frontier/lab/c4/c2-layout/results/f80-dense \
         experiments/mvp/frontier/lab/c4/c2-layout/results/medium12-dense \
         experiments/mvp/frontier/lab/c4/c2-layout/results/adaptive-targets
```

Supply only directories that exist. Additional patch-run directories go last. `--bounds` can explicitly name saved certificates; default files are `bound_results.json` and, if present, `bound_tiers_results.json`. Strongest `lower_bound` per input is retained; the input hash is checked when available. Bound solver `optimal` statuses are identified as relaxation statuses, not proof that their incumbent is a checker-passing stow. If no new bound row exists, the saved C3 primary certificate is clearly marked as historical.

`REPORT.json` contains every selected map regardless of outcome, both latest and best-passing supplied-run observations, source row SHA-256 hashes, real replay/full-check gates, lifts, parts, source/warm-reference/workload metadata, and all failed checked trials. Each score row must match the fixed input and hash and be complete. Out-of-cohort rows fail closed. C3 plans are not silently imported into the new portfolio: C3's 8/12 and 3/12 passes, and four primary lifts 71/106/70/55, are historical context. V6 constructor passes 5/0/0 are context only; V6 lift values are not used as a comparator. The primary mean is reported only when all four supplied-run primary maps pass.

Latest means the last supplied directory with a row for this input. The portfolio keeps the lowest-lift full pass among all supplied directories, breaking ties in favor of later runs. The report also retains failed newer outcomes when an earlier pass remains in the portfolio. Missing maps remain visible, including in denominators. `FAILURES.md` lists every map by rung, then every failed latest map, distinguishing construction/no-returned-candidate, finishing rejection and runner full-check rejection. A method may itself check a replay-legal trial and discard it before returning zero candidates; both layers remain explicit.

Closest checked failure means fewest distinct failed rule IDs, then lifts, then trial index. This is only a reproducible diagnostic ranking, never a measure of physical distance to feasibility. The first saved violation per rule and the largest securing ratio are displayed; all detailed violations are preserved in JSON; the parent containership-second-check ID alone never identifies the area-to-flood submetric. Construction pool deficiencies/timeouts never become physical infeasibility claims.

Medium lift counts use the D15 projection: fixed stow, no later loads. A zero gap is scoped to fewest lifts among checker-passing plans under that projection. No fresh 0.9.0 comparator is fabricated from stored rows. The inherited E9 fix is identified separately from solver improvements. Workload metadata is preserved because wall-clock budgets are load dependent.

Verification: AST syntax check and lightweight in-memory synthetic checks passed. They verified 48 fixed-map visibility and missing-map denominators, newer-failure/older-pass separation, closest-rule selection, measured/limit preservation, and every-map Markdown rendering. These checks require only Python's standard library and perform no numerical/checker work. Root will regenerate final report artifacts after the actual measurements.

## Arm A thread-initialization defect and revised runs

The earlier `results/ladder60-70` fallback omitted an explicit HiGHS thread cap. Its actual compliance was not established, so the reporter conservatively excludes it from compliant-arm comparisons. The confirmed nine-thread initialization and LP status-4 mismatch occurred in the separate first f80 smoke. Neither excess threads nor status 4 are inferred on any individual A map. Passing plans remain checker-validated physical witnesses. No result from that arm is described as resource-compliant.

Supply A first, then the complete revised B ladder and medium12 run directories, then any later patches:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 experiments/mvp/frontier/lab/c4/c2-layout/report.py \
  --runs experiments/mvp/frontier/lab/c4/c2-layout/results/ladder60-70 \
         <revised-B-whole-36-directory> <revised-B-medium12-directory>
```

Additional explicitly invalid arms may be passed with `--invalid-runs <directories>`. These annotations affect reporting only and never mutate source rows. Latest takes B wherever a B row exists. Portfolio retains lowest-lift checked witnesses across A+B, with `best_passing_protocol_eligible` and `protocol_eligible_portfolio_full_passes` excluding known invalid arms. Eligible means not explicitly invalid; the reporter does not independently audit thread counts or resource compliance.

Per-arm outcomes and resource annotations are retained in `REPORT.json.arms`. If any measured latest rows are from an invalid arm, `mean_wall_s_measured` is suppressed (null); its `mean_wall_s_descriptive` remains available with an explicit invalid-arm caveat. Best-of-runs portfolios have no single runtime. Thus an incomplete B rerun cannot accidentally make an A/B mixed runtime appear to be a valid controlled measurement.

`software_errors` and `construction_reasons` extract saved LP status, exception, timeout and search messages from construction metadata, alongside but separate from real failed full-check rule IDs. Status 4 is a solver/software condition, never evidence of physical infeasibility. A recorded `UNKNOWN`, time limit, uncovered column pool or infeasible restricted master remains a search outcome, not a software error or a global physical proof. An arm-level known defect is explicitly distinguished from a recorded map-specific error.

Further lightweight checks passed: invalid A timing suppressed while retaining its verified witness, protocol-eligible portfolio excludes A, LP status 4 remains separate from an actual trim rule violation, and real A metadata parses without changing source rows. No numerical jobs, checker runs or resource-compliance claims were added.
