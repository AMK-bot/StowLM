# Gold API audit — 29 Oct 2026

Read-only audit of production APIs and producer contracts; no experiment run.

- Exact tiny: `ExactPlanner(num_workers=1, feasibility_config=FeasibilityConfig(extra={"global_screen": 0})).plan(instance, seed=seed, time_limit_s=60)` from `stowage.solver.exact`. The wrapper may return a timeout with an incumbent plan: preserve its original status/metrics, independently gate that complete candidate, and label best-found. `metrics['optimality_status']` supplies the search status. OPTIMAL is relative to the reported engine/move model, not a full physical-domain certificate. Exact declares `sets_ballast=True`; absent ballast is a planner bug.
- Scalable: `ScalableSolver(ScalableConfig(feasibility_config=..., max_neighborhoods=12))`; `constructor_v4=True` exists as an opt-in. Adoption and rung settings still await the leader's lock. `max_neighborhoods=0` disables objective improvement and should be labelled if used.
- `stowage.planner.finalize(instance,result,time_limit_s=...)` skips non-`ok` results and plans already carrying ballast. For an exact timeout incumbent, explicitly retaining it as an independently gated best-found candidate requires an `ok` wrapper before finalize; retain original solver status in metadata. Do not promote arbitrary partial scalable plans.
- Full gate: `report=stowage.checker.check(instance,plan)`, require `report.passed`. `report.count` is authoritative: relocations are yard rehandles, restow_lifts includes ship shifts/off/on, hatch_cover_lifts counts cover off/on. Record `weighted_nonproductive` and `total_lifts_now` as separate objectives/counts. No repair.
- Replay: `experiments/mvp/smoke/run_smoke.py::token_replay(instance,plan,False,row)` demonstrates the production token path. Engine has `FeasibilityConfig(extra={"global_screen":0})`; encode every action and every end-plan token through `MaskedDecoder.advance`. Require `decoder.status=='complete'` and `decoder.actions==list(actions)`. Merely calling `allowed_actions`, or checking action prefixes, is insufficient.
- Versions: `stowage.versions.component_versions()`. Catalogue: `catalogue_snapshot().content_sha256`. Assert `rule_freeze_v1_enabled() is False`; do not use the switch-setting context helper.

## Streaming contract

Scan train/val `*.audit.json` sidecars, not the top-level manifest: producer writes the instance atomically, then its audit atomically. Require `accepted is True`, `rule_freeze_v1_enabled is False`, `global_screen==0`, and instance bytes SHA-256 equals `instance_sha256`. Audit `config` provides split/rung/vessel/generator/seed. Instance must be clean (`require_clean`), seed/size must match config, load-list length must equal `requested_targets`, voyage must exist. Producer `build.validate_shard` additionally checks necessary background-gate evidence. Derive sibling instance by replacing `.audit.json` with `.json`.

Completed tiny corpus bytes deliberately retain their original generation/source epoch, so current generator-source equality is not a valid acceptance requirement. Pin input bytes and both its recorded corpus epoch and the actual gold solver/checker epoch.

## Projection limitation

At audit time `solve47/construct/STATUS.md`, `slack/STATUS.md`, and `twostep/STATUS.md` report zero completed checker-and-mask successes for small/medium. Success throughput is therefore **unverified**. Existing failed-case timing cannot establish gold yield.

For 1,110 small and 640 medium training attempts plus roughly 5% validation, 6 h × 12 cores gives about 141 s/attempt across **all** stages. Existing 120 s small and 300 s medium solve caps alone require about 7.90 h on 12 ideally utilised cores if every cap is exhausted, before ballast/check/replay; these caps cannot supply a worst-case six-hour guarantee. This arithmetic is a budget bound, not a measured prediction. Recommend shorter solver/LNS caps based on the first qualified complete-pipeline pilot, or fewer attempts; preserve both gates.
