# D137 candidate-pool patch (staged only)

`candidate-pool.diff` follows D132's staged `selection.py`, and the frozen live
`preferences.py` / `reverify.py`. It retains D132's checker epoch acknowledgement
hook. `candidate-pool-tests.diff` adds the 15 new regressions to `tests/llm/exit`
at installation; the code diff also changes the old baseline-budget expectation
from 0.3 seconds to 120 seconds. Nothing under live src/ or tests/ was edited.

Runner integration (owned by the executive):

1. In each per-map CPU process, set native thread limits to one. Call
   `prepare_teacher_candidates(instance, gold, planning_s=120., ballast_s=120., verifier=...)`
   regardless of whether gold exists. This returns `(candidates, cache, teacher_records)`.
   The verifier must use the current outcome epoch/ACK exactly as for model plans.
   A whole-map ProcessPool parallelizes teachers without nested worker pools.
2. Cache only these current, content/epoch-bound verification results. Successful
   candidate plans are already replaced with their finalized plans. Cache keys
   are each candidate's unchanged `candidate_id`. Failed proposals still have
   per-map diagnostics (status, message, planning budget, measured wall/overrun,
   planner metrics); missing gold is explicit. Persist these records even if
   a cheaper model plan already exists. Teacher attempts must not disappear due
   to the lazy move ordering.
3. Merge teacher and greedy verification caches. Set `select_winner(...,
   verify_all=True, ballast_s=120., polish_s=15.)`. This verifies every complete
   candidate, chooses the fewest measured moves among valid plans, and polishes
   the best verified incumbent. Every admitted LNS output is checked again.
   Equal-cost ties preserve input order. No optimality is claimed.
4. Set `prepare_training_pairs(..., prefer_model_costlier_only=True)`.
   New pairs prefer **fully checked, strictly costlier own-model plans**, then
   actual own-model dead ends; gold/two-step/unchecked/uncertain plans cannot
   become rejected examples. Complete rejected traces are canonicalized from
   the checked plan; unrelated raw token metadata is ignored. Dead-end loss
   continues to blame only actions after the last good point.
5. Re-verification recognizes `evidence.fully_checked=True` and repeats full
   checker + mask admission under the training epoch. A formerly valid costlier
   plan that becomes invalid/unavailable is dropped, never silently re-labelled.
   Historical pairs retain their original conservative legacy policy.
6. Record planning wall time separately from teacher verification wall time.
   The 120-second planner and finisher budgets are cooperative, not hard wall
   promises; indivisible oracle overruns remain recorded. The source/adapter
   identity checks and owned full-check watchdog remain required.

## Verification performed (CPU, one native thread)

- `UV_CACHE_DIR=$PWD/.uv-cache uv run pytest experiments/mvp/exit/patches/d137/candidates/test_candidates.py experiments/mvp/exit/patches/d137/candidates/test_reverify.py -q`
  → **15 passed**, 2.47 seconds tool wall time.
- Staged module overlay: existing selection tests (with the new 120-second
  default assertion), preferences tests, and D129 re-verification tests →
  **79 passed**, 2.90 seconds tool wall time. The new three training-epoch
  regressions execute the staged reverify implementation directly.
- `git apply --check ../candidate-pool.diff` in `check-overlay/` copied from
  exact D132/current source inputs → passed. This check writes no source files.

No GPU work; no heavy job or compute charge. Real teacher planning/finishing
measurements and generation capacity sizing are performed by the executive.
