# Prepared heat ID correction — not applied

The E8 completeness check compares heated **tank IDs** with heat **source IDs**. Those are separate namespaces: the model documents `HeatSource.tank_id` as the reference to its heated tank, and the vessel validator verifies that reference. The frozen feeder800 input contains four heated tanks such as `FO-A-P`, represented by sources such as `heat-FO-A-P` with `tank_id=FO-A-P`. Its existing geometry is incorrectly reported absent.

Read-only frozen input SHA256: `d2453d9d2055f306fd6cbcea461fdb3543dd754e36af54eb5b9f218bccaa7694`. Static ID inventory reports four unmatched IDs with the current comparison and zero with the proposed explicit-link comparison. This is an ID inventory, not physical or plan acceptance evidence; see `frozen-id-evidence.json`.

The patch changes only the E8 heated-tank coverage set: use `tank_id` for a `heated_tank` source; use the exact source ID only when no link is supplied, preserving existing legacy fixtures. An explicit unknown/empty/wrong link never falls back. An engine-room/funnel/other source cannot impersonate a heated tank merely by sharing its ID. Every heated tank must still be represented. The existing missing-data failure, all source distance/overlap checks, geometry, clearance values and other rules are untouched. Vessel/source IDs are not renamed because other model metadata refers to those identities.

No approximation or new geometry inference is introduced. A `heat-` prefix alone is never treated as a tank link. Unknown heated tanks are not dropped. The source-model contract and current fixture behavior, rather than a heuristic prefix convention, determine the mapping.

Files prepared:

- `../heat-ids.patch`: unified diff for `src/stowage/checker/rules_special.py` and a new `tests/checker/test_rules_heat_ids.py`.
- `src/stowage/checker/rules_special.py`: patched scratch source with the unchanged `special_violations` API.
- `baseline/src/stowage/checker/rules_special.py`: exact pinned live source before preparation.
- `test_rules_heat_ids.py`: standalone synthetic static E8 regressions, also included in the patch at the intended live test path.
- `manifest.json`, `verification.json`, raw logs and JUnit: source pins, measured test outcomes and unchanged-live-file audit.

The twelve regressions cover distinct linked source IDs, legacy same-ID compatibility, explicit unknown/empty/unheated-tank links, non-tank ID collisions, genuinely absent geometry, an additional unrepresented heated tank, refusal to guess a tank from a prefix, and preserved nearby engine-heat rejection. Baseline: **4 passed / 8 failed**, 0.198 s inside pytest. Candidate: **12 passed**, 0.157 s inside pytest. These are static synthetic rules, with no hull construction, stability calculation, full checker, mask replay or solver run. The candidate result is not a showcase solvability claim.

Commands run sequentially on one native thread, with bytecode and external pytest cache writes disabled:

```sh
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=$PWD/experiments/mvp/fixwindow/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run --no-sync --frozen python experiments/mvp/fixwindow/heat/prepare_patch.py
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=$PWD/experiments/mvp/fixwindow/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run --no-sync --frozen python experiments/mvp/fixwindow/heat/run_regression.py --source experiments/mvp/fixwindow/heat/baseline/src/stowage/checker/rules_special.py --name baseline
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=$PWD/experiments/mvp/fixwindow/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run --no-sync --frozen python experiments/mvp/fixwindow/heat/run_regression.py --source experiments/mvp/fixwindow/heat/src/stowage/checker/rules_special.py --name candidate
git apply --check experiments/mvp/fixwindow/heat-ids.patch
```

The regression runner explicitly disables pytest's cacheprovider. Baseline exits1 on the reproduced defects; candidate exits0. Read-only `git apply --check` exits0; the patch was **never applied**. All five pinned relevant live source/test files remain byte-identical, and the intended new live test file remains absent. Exact source and patch SHA256 values are in `manifest.json`.

Compute: one core, native threads1, no GPU/cloud; full shell invocation walls were 0.542 s baseline / 0.512 s candidate. Peak RSS for these unit tests was unmeasured. All writes stayed inside `experiments/mvp/fixwindow/`; shared ledger/history were untouched.

Parent measurement is complete: see `../REPORT.md` and `../heat_snapshot_results/heat-report.md`. All80 saved-plan comparisons are unchanged. `showcase_supplement/local-summary.json` demonstrates34false E8 missing-data rejections removed, five genuine heat-clearance rejections preserved, and four newly locally allowed placements. A fixed18th-load full-check diagnostic loses the false heat error but still fails two existing forward-draught limits. No solver or ballast finisher was run; no complete showcase certification is claimed. The patches remain unapplied.
