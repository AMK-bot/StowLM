# Historical task 4.3 handoff — generator 1.0.0

This is the original 1.0.0 handoff. Counts, resume claims, tests, commands and
physics statements below are historical; they are not current-version evidence.
See README.md and `fix-wave/` for the fix-wave status.

Implemented generator, screening, resumable CLI and 42 scoped tests. Starter:
50 tiny, 50 small, 20 medium, 5 full; `manifest.json` and rung manifests carry
counts, checksums, rejection rates and provision rates. All 125 shards validate
and resume without regeneration; all share the same full real-hull payload.
See README.md for rates, commands, approximations and input semantics.

Changed files: `src/stowage/generate/instances{,_cli,_screen}.py`,
`src/stowage/generate/__init__.py` (original docstring retained),
`tests/generate/test_instances{,_cli,_screen}.py`, and this experiment directory.
No files outside the fence, global version constants, frozen signatures, git
index, commits, or leader documents were changed. Producer version is pinned
locally as `INSTANCES_VERSION` and in provenance (there is no owned global
constant in versions.py).

Verification (prefix each with `UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1`):

- `uv run pytest -q tests/generate/test_instances.py tests/generate/test_instances_cli.py tests/generate/test_instances_screen.py` — 42 passed.
- `uv run python -m stowage.generate.instances --benchmark --without-dg --output experiments/instances-sim` — five per rung, completed.
- `uv run python -m stowage.generate.instances --starter --without-dg --output experiments/instances-sim` — 125 shards; repeated invocation resumed all 125, zero generated/recovered (`resume-check.json`).
- `uv run python experiments/instances-sim/probe_screening.py` — five raw cargo candidates per rung, 1.92 s; `screening-probe.json`.
- `uv run pytest -q` — first full run failed outside scope: three illegal-plan checker assertions (seeds5/6/10), frozen linear.build_model source inspection StopIteration, three vessel tests expecting empty strength stations. Required rerun completed in 415.13 s (estimate 10 min), exit1 with FOUR failures: frozen linear.build_model source inspection and three vessel hull tests; checker assertions resolved. See full-suite-repeat.log. The current on-disk hull test changed during the run; no outside-scope files were edited here.

Open integration issue: fleet stacks currently have no recorded DG compliance
classes. The checker rejects DG in such spaces. The explicit starter non-DG
service is tagged/audited, while default generator demand retains the full mix.
Full-mix medium/full can exhaust retries. Fleet owner must supply its documented
synthetic permissions; the model docstring's None='no restriction recorded'
wording and checker fail-closed interpretation warrant coordinated clarification.
No permissions or cargo certificates were invented.

Proposed leader-only decisions text:

- APPROXIMATION: Stage-A simulation instances use two overlapping export gate
  windows from an empty yard, half-volume dry-empty background and port-profile
  placement noise; no terminal steady-state, import or throughput calibration.
- APPROXIMATION: tiny shares random-yard six empty dry20 decision cells; slices
  preserve whole-hull physics and documented fixed background. Fixed consumables
  are half-full and ballast remains empty for the finisher.
- APPROXIMATION: starter demand explicitly excludes DG until fleet compliance
  classes are recorded; full-mix probes and provision rejection rates remain
  separate. No checker/physics constraint is relaxed.
- APPROXIMATION: screening uses optimistic cell/stack unions, bounded OOG pair
  tests and conservative tabulated displacement bounds. It never certifies a
  loading plan; joint packing and full physics remain solver/checker work.

Proposed plan status: implementation + starter complete; full-suite acceptance
OPEN after required repeat (four outside-scope failures), DG-inclusive large-rung population
pending fleet permissions. Evaluation row: 125 Stage-A synthetic non-DG shards;
medium screens 10/30 (capacity 9, on-deck 1), other rungs 0; true feasibility unmeasured.

Compute: one CPU thread per job, no GPU/cloud. Cold hull 56.74 s. Five-per-rung
pilot accumulated 73.58 s generation including initial cold hull; complete
starter 91.51 s persisted generation, within <2-minute estimate per campaign.
First full suite estimate 8 min, approximately 10 min actual (not instrumented). Repeat estimate 10 min, actual415.13 s, exit1 with four outside-scope failures; see compute.md. `nice`/`ps` prohibited by
sandbox; shared ledger read and load/memory admission checked. Scope fence
prohibits edits to experiments/fleet/{ledger,history}.md, so reservations/history
are in compute.md for leader reconciliation. Peak RSS not instrumented (<1 GB
estimated generator, <2 GB suite).
