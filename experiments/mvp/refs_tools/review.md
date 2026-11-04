# Independent reference-run audit (29 Oct 2026 restart)

Scope: read-only source review and lightweight driver/report validation. No optimizer, cloud, GPU or other heavy job launched by this reviewer.

## Source contracts checked

- `run_planner(..., allow_repair=False)` is required for each baseline's own-plan evidence. It calls finalization, the full checker, binds the report to the final input/plan, and records phase timing. The default permits repair and would contaminate own-plan rows.
- `FeasibilityConfig(extra={"global_screen": 0})` is the D79 setting. Explicitly pass it through every configurable planner in this campaign, and assert `rule_freeze_v1_enabled()` is false.
- Exact's 60-second limit caps its search through its owned watchdog worker. Pipeline finalization and the final outer checker add separately measured time. Preserve the solver's optimality/domain metrics: `OPTIMAL` is weighted-lift optimality in the public-engine model; physical-domain optimality is not certified.
- Exact timeout incumbents may have passed the full checker, yet remain `status="timeout"` and `shippable=false`. `_passed(..., population="own")` can include them; `_objective_eligible` excludes them from objective means/pairs. Never omit or override the shipping verdict to bypass this.
- `report.count.relocations` is yard rehandles. Current `restow_lifts` and `hatch_cover_lifts` differ from projected future moves. Use the checker's weighted objective, which includes configured weights and future projections.
- Bounds have no plan, finisher or checker verdict. Label these N/A; zero is a measured non-informative bound, not a gap certificate.
- Input identity must be pinned to frozen bytes, not only the instance's internal ID. Source fingerprints must distinguish loaded code from concurrently changing disk files; fresh workers per result avoid stale module attribution.

## Review status

Driver and reporter reviewed after landing. No blocker for the tiny campaign remains in the audited scripts.

Findings fixed by their owners during review:

- `result.json` now has the schema-version envelope required by `stowage.io.load_result`, while `record.result` remains the plain harness payload. Pilot processes started before the fix may need their saved result envelope added without changing plan/checker content.
- Explicit instance lists may contain mixed rungs (exact remains tiny only). Additive selection/journal handling preserves tiny records when later approved small/medium inputs are added.
- Records now distinguish startup/loaded-source fingerprints from before/after disk hashes, list changed paths, retain the pipeline shipping verdict, and conservatively exclude relevant source drift from reference objective eligibility. Generator-only edits cannot affect frozen input bytes.
- Nonfinite diagnostic floats are recursively encoded as the model contract's `Infinity`/`-Infinity`/`NaN` strings. The same sanitizer handles per-instance artifacts and the journal, preventing inconsistent duplicate representations.

The reporter uses the harness's own-plan aggregation and paired comparison, keeps first failing hard rules, exposes raw failed/timeout move counts separately, and distinguishes objective eligibility from checker passes. A source-drift flag can also mean the disk changed since startup, rather than during that individual instance; the report should use that broader wording. Exact's 60-second cap is a search cap, with separately measured outer pipeline time.

Assumption: the campaign's subsequent independent validator checks all saved report bindings and artifact/journal equality. This reviewer did not rerun any full checker or optimizer and does not certify campaign outcomes before those artifacts exist.

## Lightweight verification performed

Read `audit.md`, `mvp-common.md`, the wave-2 run-folder contract, planner pipeline, exact planner/engine, scalable/two-step signatures, lower bounds and harness metrics. No heavy compute; no ledger reservation required.

`UV_CACHE_DIR=$PWD/.uv-cache uv run python - <<'PY' ... PY` (lightweight audit harness): PASS in 0.28 seconds. Parsed `run_refs.py`, `summarize_refs.py` and `validate_refs.py` with `ast.parse`; imported only the driver's standard-library startup; called its selection function to validate all 15 distinct frozen tiny SHA256 pins; checked recursive strict-JSON nonfinite serialization and round-trip equality. No run artifacts were written by this check.

## Pinned-runtime follow-up

The campaign subsequently selected `--source-root experiments/mvp/refs_tools/frozen-runtime` because concurrent working-tree edits invalidated pilot source attribution. `make_snapshot.py` compares pre-copy, post-copy and copied bytes and refuses to replace an existing runtime. `run_refs.py` imports from that runtime, fingerprints all copied source/data files plus the real shared `params/`, and keeps original corpus/output paths. No running scripts were edited by this review.

Lightweight standard-library audit (under one second): PASS. All **259** snapshot source/data files exactly match `snapshot.json`; `experiments/cache` resolves to the original shared cache; upward `pyproject.toml` discovery resolves the parameter root to the original repository; snapshot-maker and driver syntax parse.

Compatibility limitations sent to the campaign owner:

- Relative calibration evidence in `linear/margin_policy_data.json` needs read routes for snapshot `experiments/linear-model` and `experiments/stability`, in addition to the existing cache link. Across the complete bank there are 278 unique relative linear-model evidence paths, 11 stability paths, and 81 cache paths. Every corresponding original path existed during this audit. Shared cache entries perform their own content checks; they are not part of the source-only snapshot.
- `rules/imdg/canary_contexts.py` has a snapshot-relative `experiments/campaign-g1` path for authored context generation. Frozen tiny planning does not invoke that constructor, but a read route preserves that optional path.
- A remaining conditional limitation is an absolute original `src/stowage/linear/boundary_calibration.py` evidence path in the margin bank. `_boundary_list` also requires that evidence path to resolve to the currently imported primitive's `__file__`; a snapshot import changes that path even when bytes match. A branch validating such a certificate can reject it with `boundary LIST primitive source pin mismatch`; adding experiment-directory links does not solve that identity check. This is a possible cache/calibration branch incompatibility, not an observed failure in the owner's already passing tiny outputs. Do not change running source to work around it; retain any actual failure honestly.

This audit verifies snapshot source-byte stability and path reasoning, not numerical cache fidelity by recomputation. No checker, optimizer, cache warmer or heavy test was launched by the reviewer.
