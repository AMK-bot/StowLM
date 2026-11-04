# Showcase artifact tooling

From the repository root, package any `PlannerResult` or `MovePlan`:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache \
nice -n 10 uv run python experiments/mvp/showcase_tools/package_showcase.py \
  --instance INSTANCE.json --result RESULT.json --report CHECKER.json \
  --run-folder experiments/mvp/runs/showcase-solver \
  --bound BOUND.json --method solver.scalable --command 'EXACT SOLVER COMMAND'
```

Use `showcase-twostep` for two-step references. `--report`, `--bound`, and `--command`
are optional. A supplied checker report is reused only if both input-content
bindings match; otherwise packaging runs the full checker. Supplying a checked
result avoids a duplicate check. Packaging does not solve, finish ballast, or repair.
Fresh checker calls also redirect numerical-cache writes to the owned sibling
`<run-name>-numerical-cache`.

Each `instances/<actual-instance-id>/` contains the byte-identical original
instance, `result.json`, `report.json` when available, `outcome.json`, optional
`bound.json`, and `SHA256SUMS`. `result.json` retains canonical `PlannerResult`
fields with the app-supported `input_sha256`/`report` envelope. Consumers using
`stowage.io.load_result` directly must remove these two envelope fields; the demo's
`app.inputs.load_inputs` handles them and is verified during packaging. A raw
`MovePlan` becomes a `supplied-plan` result with original planning time unavailable
(`null` in the outcome), rather than an invented runtime.

The ordinary run manifest has no model identity or decoding trace claims.
Load a particular entry explicitly:

```sh
PYTHONPATH=.:src uv run python -m app \
  --run-folder experiments/mvp/runs/showcase-solver/instances/ACTUAL_INSTANCE_ID \
  --entry result.json
```

`--status partial` labels only the manifest/outcome; the original planner status
is preserved and departure approval stays false. Physical replay legality,
executed moves, loaded/required cargo, checker verdict, and first hard violation
remain separate measured fields. A failed result without any plan is retained
but cannot be replayed by the app's ordinary solver importer; `outcome.json`
states that limitation. The app always runs its own checker before approval.
Selecting the self-contained instance directory also keeps startup discovery
from scanning execution archives or numerical caches elsewhere in the run.

The packaging command prints the exact physics timeline command, for example:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache \
nice -n 10 uv run python experiments/mvp/showcase_tools/precompute_timeline.py \
  --run-folder experiments/mvp/runs/showcase-solver \
  --entry instances/ACTUAL_INSTANCE_ID/result.json --workers 1 \
  --cache-dir experiments/mvp/runs/showcase-solver/timeline-cache \
  --numerical-cache-root experiments/mvp/runs/showcase-solver-numerical-cache
```

Reserve that physics job in the fleet ledger before executing it.
`--precompute-timeline` runs the printed command immediately;
`--max-frames N` limits it to a resumable timing probe. The official UI tool owns
keyframe selection, measured physics, cache identity, and approximation notes.
The wrapper delegates to the official UI tool in-process and redirects numerical
table caches with `run_hero.isolate_caches`. Physics stays inside the run folder;
numerical tables use its sibling `<run-name>-numerical-cache`, outside UI plan
discovery. Exactly one worker preserves these in-process redirects. Pass
`--run-folder` even when using the official `--instance`/`--plan` selection flags.
Numerical caches live under `<numerical-cache-root>/cache/{linear,stability}`;
existing common tables are linked for reading and any replacements stay local.
No UI files are written. **Integration required:** the current UI defaults to
`experiments/mvp/ui/cache`; its owner must wire in or import the run-local cache
before the server can serve these precomputed timelines.

Cheap contract verification:

```sh
UV_CACHE_DIR=$PWD/.uv-cache nice -n 10 uv run pytest -q \
  experiments/mvp/showcase_tools/test_package_showcase.py
```

The tests reuse the existing passing tiny smoke fixture and skip if unavailable.
They cover self-contained import, input hash tamper rejection, partial status,
false approval rejection, no-plan diagnostics, and model-manifest protection.

## Medium solver showcase attempts

`run_medium.py` runs three deterministic medium draws sequentially with .25
booking slack, .55 cargo-mass cap, C2 off, and the default scalable solver.
The first two draws use random-yard blocking .7; the third uses simulated-yard
noise .7. These are bounded showcase searches, not evaluation samples.

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
nice -n 10 uv run python experiments/mvp/showcase_tools/run_medium.py \
  --output experiments/mvp/runs/showcase-medium \
  --deadline-utc 2026-10-29T20:00:00+00:00 --overall-seconds 7200 \
  --skip-failed-generation --solver-seconds 1800 --finisher-seconds 600
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/mvp/showcase_tools/render_medium_summary.py
```

The UTC deadline above is the original campaign deadline; choose a new explicit
deadline for a new campaign. Existing successful stages are reused, and
`--skip-failed-generation` retains failed draws without retrying them. Ballast V4
is scoped to finalization. Only full-checker and complete token-mask passes enter
`showcase-medium/ui/`; numerical caches and diagnostic evidence stay outside it.
`diagnose_medium_prefix.py --case CASE --phase check` (or `mask`) diagnoses the
saved most-loaded prefix without approving departure. Supervise each diagnostic
process with a hard deadline of at most 180 seconds.
