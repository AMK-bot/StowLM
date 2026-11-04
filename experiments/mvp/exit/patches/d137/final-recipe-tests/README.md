# D139 one finisher attempt

`../final_recipe.py` follows the immutable D132 base. The accompanying
`../final-recipe-single-finisher.diff` includes the implementation and eight
regressions for installation under `tests/llm/exit`.

The gate matches `experiments/eval-c1/run_c1.py:second_finalize_refused`: an OK
result with no ballast, at least one checked candidate, and zero feasible
candidates cannot receive another finalizer budget. The final audit explicitly
checks this **before** converting the plan to a Candidate (which loses planner
metrics). The scoped V4 wrapper also refuses retries carrying those metrics.

A refused retry never creates checker failure evidence. Only structured
finisher timeout evidence or the planner's explicit `finisher_budget_timeouts`
list classifies it as a finisher time limit. A completed checker rejection
without timeout evidence is `candidate_not_qualified`; checker unavailability
remains unavailable, never physics FAIL. Zero/missing checked counts allow the
first attempt. Successfully ballasted plans remain eligible for full admission.

New cache recipe version: `d139-greedy-single-finisher-v1`, with
`finisher_attempts_per_candidate=1`. Historical double-budget audit rows cannot
reuse this identity. Rows explicitly retain `second_finalize_refused`,
`finisher_timeout`, and mutually exclusive `finisher_outcomes`.

Verification:
`OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run pytest experiments/mvp/exit/patches/d137/final-recipe-tests -q`
→ **8 passed**, 6.97 s tool wall; CPU only, no live source or tests changed.
