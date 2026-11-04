# Evaluation harness validation demo

`val-placeholder-demo/` evaluates the existing UI first-fit placeholder through
`run_planner`, ballast finishing, and the full checker. It makes no optimisation
or feasibility claim. Only a passed full checker makes an output shippable.

Reproduce from the repository root:

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run python experiments/eval-runs/demo_val_placeholder.py
```

Each invocation uses one CPU core, estimates at most 1 GB RAM, uses no GPU,
and stops after 90 seconds with completed instances checkpointed. Repeat until
it prints `completed: true`. Actual chunk timings and peak memory are recorded
in `val-placeholder-demo/compute.jsonl`. The 0.2-second planner/ballast budget is
cooperative; checker time is measured separately and never bypassed.

The frozen canonical campaign contains **9 distinct tiny val** instances and
11 small val instances. The requested 20 tiny + 10 small val demo is therefore
unavailable within this scope. This run uses all 9 tiny and the first 10 small
by frozen specification key. It never repeats examples or substitutes train/test
cases. Its `summary.json` includes overall and per-rung aggregates; `ROW.md` is
ready to copy into `docs/evaluation.md` (its run link is relative to that file).

Repair task 5.7 remains unimplemented: failures explicitly record
`repair unavailable` and count toward fallback-needed rate. Missing solver
references and objectives of failed plans remain unavailable, rather than zero.
`attempted_moves` retains counts from failing plans. Unbounded checker margins
are encoded as JSON strings `Infinity`/`-Infinity`/`NaN` and restored by model
validation; they are excluded from finite aggregate statistics.

Verification: the full `uv run pytest -q` suite passed. Targeted harness and
frozen-interface tests additionally cover pipeline order, repair rechecking,
provenance refusal/taint, split isolation, resume pinning, batch generation,
paired bootstrap, nonfinite diagnostics and fallback accounting.
