# Engine snapshot

`snapshot_engine.py` copied the engine's Python runtime modules and the frozen
Frontier 0.9 planner into `frozen_engine/`. It excludes tests, conftest, benchmark
tools, results, and provenance stamping. The copied source may include optional
runtime modules added by the simultaneous engine work before the snapshot.
`engine-snapshot.json` lists every original path, source hash, copied hash, and
rewrite. The source files were reread after copying to detect changes during
the snapshot; none changed. Do not rerun the snapshot after measurement starts.

The only engine changes are namespace isolation (`engine` becomes
`frozen_engine`), repository-root discovery, and loading the copied 0.9 planner
from this folder under a unique module name. The algorithms and budgets are
unchanged. Native `src/stowage`, `params`, and frozen Frontier helper imports
remain shared, as allowed by the contract; their hashes are recorded under
`unchanged_native_dependencies`. There are no imports from the live `engine/`.

Entry points, with this folder plus `src` and `experiments/mvp/frontier` on
`PYTHONPATH`:

- `frozen_engine.planner.plan_and_check(instance, budget_s=..., finalize_s=...,
  seed=...)` returns `(PlannerResult or None, diagnostics)`.
- `frozen_engine.lab_method.propose` and `finish` implement the standard lab
  adapter and reuse its internally finished plan. The runner must independently
  call the full checker.
- `frozen_engine.frozen.planner.plan_and_check` is the copied 0.9 planner. Its
  keyword arguments are `search_s=60`, `finalize_s=600`, `seed=0`, `tries=3`,
  and `feasibility_config=None`. It returns `(PlannerResult or None, diagnostics)`.

The engine's `budget_s` is extra work. On at most 60 boxes it preserves baseline
search `min(120, max(20, 6*n_loads))` and at least 300 seconds of native finishing;
a zero extra budget does not remove these floors. On larger maps the default
extra budget is 375 seconds, pages receive 80%, and the large frozen fallback
requires at least 300 seconds remaining. Construction and finalization budgets
are separate and atomic replay/checker calls can overrun. All internal CP-SAT
calls use one worker as in the engine. When running the 0.9 planner directly,
use the existing reference adapter's scoped single-worker `solve_master`
wrapper, restoring it in `finally`. Its large-planner column calls also need
one worker if invoked. No algorithmic switches were changed in this snapshot.

Verification: bytecode-free import of all 32 runtime modules, adapter, copied
0.9 planner, and AST parsing of all copied Python sources succeeded in 1.08 s.
No `engine` or `engine.*` module was present in `sys.modules` after import.
No solving, inputs, sealed data, or raw evaluation data were opened for this
check. Snapshot digest:
`3d88b3b2a4ccbf19c527c2953c548ba24bf7629c91f8edfc35a6b958cc6bdbbd`.
