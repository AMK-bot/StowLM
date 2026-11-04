# Showcase run compatibility audit

Read-only source audit, 29 Oct 2026. No solver/checker jobs or source changes performed.

## Run folders

- Use `instances/<actual instance.id>/result.json`; preserve the complete `PlannerResult` including moves, status, provenance, repair metadata and measured timing. `stowage.io.save_result` adds the schema-version file envelope; raw `model_dump(mode="json")` also imports through `app.inputs` when a plan exists.
- These are solver references, not Muse Glimmer or StowLM evidence. Ordinary manifests should contain method/planner/source/status/command/version declarations, **without** `planner_kind`, `base_model`, `adapter_sha256` or `mask_version`. Any one of those keys triggers the app's strict model-manifest path (`app/model_runs.py:model_run_metadata`), which only accepts the two genuine model kinds.
- Store an unmodified copy of the original showcase `instance.json` beside each result. Strongest automatic binding: a run entry wrapper containing `result`, `instance`, `input_sha256` and `report`, or a raw PlannerResult plus explicit `--instance` pointing at its original input. If keeping raw results and local instance files, an import envelope may also add `input_sha256` (the loader removes this envelope key before canonical validation), and/or `report` with the checker's direct instance binding.
- The current automatic corpus search covers simulator/v3/v2 directories, **not** `experiments/mvp/corpus/showcase`. An unbound standalone result is insufficient for automatic discovery. Copying the original instance into the run folder plus supplying its original `input_sha256` is a self-contained solution.
- Every folder contains several results: CLI examples must use `--entry instances/<actual instance.id>/result.json`. Filename aliases such as `feeder_500-small` need not equal `instance.id`.
- Trace files are model-decoding evidence and are not required for ordinary solver run imports. Do not invent logits, legal-action counts or model provenance.

## Checker and partial results

- `run_planner(planner, instance, ..., allow_repair=...)` runs planning, ballast finalization, full checker and optional repair/recheck. Persist `pipeline_timing`, `fallback_needed`, `repair_status`, `repaired` and `repair_note`. The supplied time limit is per phase; it is not a hard end-to-end limit.
- A stored `metrics.shippable` is informational only. Deserialized results lose private shipping evidence and must be freshly checked; the app already does this. Shipping requires status `ok`, complete loads, all seven checker parts, clean recursive provenance and no unsupported future calls.
- `PlannerResult.status` allows only `ok`, `dead_end`, `infeasible`, `timeout`, `error`. For a hero replay label the manifest/outcome `status: partial`, while keeping the actual planner status in `result.json`; do not introduce the invalid result status `partial`.
- `ScalableSolver` retains a best physical partial plan on timeout/dead end. The app replays its legal prefix and checker rejects missing cargo. Preserve actual status and list executed/planned moves and loaded/required cargo; no approval or saving claim.
- `ConstructivePlanner` currently returns no failed candidate plan, even when its stats retain `last_assignment`. An assignment is not a crane sequence and must not be presented as completed replay.
- Importing an ordinary no-plan solver result has an app limitation: `load_inputs` sends no-plan run entries through `_failed_model_inputs`, which expects a model manifest. Keep these failure artifacts, report them, and do not claim replay works. A partial scalable plan avoids this issue.
- Use report counts as the source for rehandles/restows/hatch lifts and distinguish current from projected later-call counts. Record the first hard violation's rule id and message, or an explicit unavailable checker reason.

## Cheapest viable methods

1. Constructor: `ConstructivePlanner(num_workers=1, direction="necessary")` through `run_planner`. This creates the first complete static assignment, sequences it, checks it and attempts ballast. Its current surrogate objective does not prove minimum moves. The default necessary-direction physics is permissive during search only; full checker acceptance is mandatory.
2. Scalable alternative: `ScalableSolver(ScalableConfig(...))`. For quickest first incumbent use narrow beam/branch limits and disable expensive improvement windows (`exact_window_time_limit_s=0`, `exact_whole_plan_time_limit_s=0`, `max_neighborhoods=0`). If a first incumbent fails, retain the failure, then try a different `initial_strategy`/seed or a larger budget. These truncate search, not checker rules. Do not assume narrower search will solve a difficult instance.
3. Repair fallback: use existing `run_planner` repair and retain its fallback evidence. For method-pure two-step comparison use `allow_repair=False`; otherwise a failed baseline can silently become solver output. A repaired plan remains diagnostic for the app's percentage-saving display.
4. Lower bound: `stowage.bounds.lower_bound(instance)` is deterministic counting, has no search, and accepts a zero time budget without changing its result. It is guaranteed work every plan needs under a relaxed problem; a gap does not establish optimality except checked equality with a positive same-domain bound. Sources are the existing `bounds/components.py` references, including Tanaka–Voß (2019), DOI `10.1016/j.ejor.2019.06.014`.

Assumptions: the rule-freeze switch remains off; no model inference; no source fixes; all solver runs remain serial on one core until fleet admission permits more.

APPROXIMATION: any proposed narrow search configuration limits search effort and makes no optimality claim. Existing constructor objective/physics approximations must be copied from the actual result stats into the run report, not replaced with a generic claim of exact optimization.
