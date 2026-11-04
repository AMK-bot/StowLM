# Stage bundle contract (demo UI v2, task 0.3)

One JSON document drives the new "stage" page (`/stage`). The backend builds it from a replay the app already
validated (content binding + checker recomputation stay on the load path). The browser never recomputes physics or
rules; it only animates what the bundle says.

Endpoints (added to `app/server.py`):

| Route | Returns |
|---|---|
| `GET /stage` | `app/static/stage.html` (also `/stage.js`, `/stage.css`) |
| `GET /api/stage?plan_id=<registry id>` | stage bundle (below). `plan_id` omitted → active plan. Must return in < 3 s; physics comes from the cache or is marked pending. |
| `GET /api/stage/physics?plan_id=<id>` | `{"status": "ready"|"running"|"unavailable", "done": n, "total": m, "physics": <physics block or null>}`; starts a background keyframe computation on first call when no cache exists (live mode). |
| `GET /api/stage/explain?plan_id=<id>&box=<container id>` | cached explanation text for a box, or the plan explanation. Never calls Spark unless `live=1` AND the existing Meta credit gate allows it. |

All numbers are measured by the engine/checker; anything not available is `null` with a reason string, never a guess.

## Bundle (`version: "stage-v1"`)

```jsonc
{
  "version": "stage-v1",
  "meta": {
    "plan_id": "...", "instance_id": "...", "vessel_label": "Synthetic feeder 800 TEU", "rung": "tiny|small|medium",
    "port": {"locode": "THLCH", "label": "Laem Chabang (simulated)"},
    "planner": "stowlm|solver.scalable|solver.exact|baseline.twostep|ui-placeholder|supplied-plan",
    "planner_label": "StowLM · Muse Glimmer 30B (LoRA)",   // human label
    "source": "run-folder|live|placeholder|supplied",
    "checker_passed": true, "departure_approved": true, "repaired": false,
    "notice": "Full checker passed; ...",                  // backend's existing notice
    "scope_line": "Simulated port, synthetic ships, demo rule set (rule-freeze off)."
  },
  "geometry": {
    "slots": [ { "id": "0010282", "bay": 1, "row": 2, "tier": 82, "on_deck": true, "stack_id": "00102D",
                 "x_m": 91.97, "y_m": 1.25, "z_m": 10.4,       // lcg, tcg, base height of the cell
                 "hatch_cover_id": "HC-002-P", "reefer_plug": true, "accepts": [20, 40] } ],
    "hatch_covers": [ { "id": "HC-002-P", "bays": [1, 3], "rows": [2, 4] } ],
    "hull": { "loa_m": 120.0, "beam_m": 20.0, "depth_m": 10.0, "stations_x_m": [ ... ] },   // nulls allowed
    "yard": { "stacks": [ { "key": "B012-003-01", "block": "B012", "bay": 3, "row": 1, "length_ft": 20 } ],
              "max_tiers": 5 }
  },
  "pods": [ { "locode": "SGSIN", "label": "Singapore", "call_index": 1, "color": "#2b7bb9" } ],   // backend picks colours
  "boxes": { "QZXU0000009": { "pod": "SGSIN", "length_ft": 20, "height_m": 2.591, "mass_t": 21.3,
             "weight_class": "H", "empty": false, "reefer": false,
             "dg": { "class": "3", "un": "1203" } | null, "iso_type": "22G1" } },
  "initial": { "ship": [ { "box": "...", "slot": "..." } ], "yard": [ { "key": "...", "boxes": ["bottom", "...top"] } ],
               "covers_off": [], "quay_buffer": [] },
  "steps": [   // step i = the (i+1)-th crane lift in plan order; frame 0 = initial
    { "i": 0, "kind": "load|relocate|restow_off|restow_on|discharge|hatch_cover_off|hatch_cover_on",
      "box": "QZXU..." | null, "cover": "HC-..." | null, "src": "...", "dst": "...",
      "nonproductive": false,                         // rehandle/restow/hatch lift
      "counts": { "loads": 1, "yard_rehandles": 0, "restows": 0, "hatch_lifts": 0, "total_lifts": 1,
                  "projected_later_restows": 0, "projected_later_hatch_lifts": 0, "weighted_nonproductive": 0 } }
  ],
  "physics": {            // may be null while pending (then poll /api/stage/physics)
    "frames": [ { "step": 0,     // frame index (0 = before first lift; k = after k lifts)
        "gm_m": 1.2, "gm_min_m": 0.15, "heel_deg": 0.3, "trim_m": -0.4,
        "draught_aft_m": 6.1, "draught_fwd_m": 5.7, "draught_mean_m": 5.9, "max_draught_m": 7.9,  // load line / port limit
        "displacement_t": 9000, "sf_n": [...], "bm_nm": [...],      // per station, same order as stations_x_m
        "gz": [ { "deg": 0, "m": 0.0 } ],
        "stability_passed": true, "strength_passed": true, "error": null } ],
    "stations_x_m": [...], "sf_min_n": [...], "sf_max_n": [...], "bm_min_nm": [...], "bm_max_nm": [...],
    "keyframes": "all|every-k|final-only", "note": "APPROXIMATION text if any (e.g. final ballast applied to prefixes)"
  },
  "comparison": {          // totals for the race; any entry may be null with a reason
    "stowlm":      { "label": "...", "counts": { ...same keys as step counts... }, "checker_passed": true },
    "baseline":    { "label": "Two-step baseline (our model of industry practice)", "counts": {...}, "checker_passed": true } | null,
    "lower_bound": { "label": "Lower bound (proven floor, not necessarily reachable)", "nonproductive": 3, "total_lifts": 40 } | null,
    "reasons": { "baseline": "...", "lower_bound": "..." }
  },
  "rules": { "source": "experiments/mvp/rules.json|catalogue",
    "families": [ { "family": "stability", "label": "Stability (IS Code)", "status": "pass|fail|not_run|n/a",
                    "rules": [ { "id": "...", "description": "...", "hardness": "hard", "status": "pass|fail|not_run|n/a",
                                 "violations": 0 } ] } ] },
  "xray": { "available": true, "reason": null,
    "steps": [ { "step": 0, "n_legal": 12, "n_actions": 27, "chosen": { "box": "...", "slot": "...", "text": "P001 0030202" },
                 "chosen_logprob": -2.5, "top_k": [ { "text": "...", "logprob": -1.4, "legal": false } ],
                 "legal_slots": [ "..." ] | null, "plan_step": 0 } ] },   // plan_step = index into steps[]
  "explanation": { "source": "spark-cached|template|none", "text": "...", "per_box": { "QZXU...": "..." },
                   "live_available": false, "live_reason": "..." },
  "kpi": { "scenario": "mid", "moves_saved": 7, "quay_moves_saved": 2, "yard_moves_saved": 5,
           "crane_hours": 0.08, "usd_per_call": 525, "thb_per_call": 18900,
           "basis": "this slice only (its own load list, not a whole call); valued with the docs/impact.md mid scenario; constants in app/kpi_config.json", "vs": "baseline" } | null
}
```

`top_k` in `trace.jsonl` is the first-token distribution before masking (see `w2-stowlm-run-contract.md`);
the UI labels it that way. `legal_slots` is computed by the backend from the action space at that state when cheap,
else null.

## Additive fields (backend, 29 Oct 13:00 EDT; nothing above changed meaning)

Consumers must ignore unknown keys; every field below is optional to render.

- Routes: static stage assets are served by pattern: any `app/static/<name>` where `<name>` fully matches
  `stage(-[a-z0-9]+)?\.(js|css)` and the file exists (e.g. `/stage-scene.js`, `/stage-gauges.js`, `/stage-yard.css`).
  `/api/stage/physics` also returns `"source": "cache"|"live"|null` and `"reason"`.
- Top level: `physics_status` (`ready|running|unavailable|pending`), `physics_progress {done, total, source, reason}`.
- `meta`: `plan_ref` (MovePlan id), `placeholder` (bool; true ⇒ `planner = "ui-placeholder"`, `departure_approved =
  false`, notice starts with `PLACEHOLDER PLAN`, `kpi = null`), `validation_status`, `physical_replay_legal`.
  `planner = "stowlm"` only for a verified model run; reference runs keep `solver.exact` / `baseline.twostep`.
- `geometry.slots[]`: `accepts` lists 20/40/45. `geometry.hatch_covers[]`: `x_m, y_m, z_m, deck_stack_ids,
  hold_stack_ids`. `geometry.hull`: also `lpp_m, ap_x_m, fp_x_m, design_draught_m, max_draught_m, bridge`.
  `geometry.yard.blocks[]` (port yard layout when the instance has it).
- `boxes{}`: `pol`, `oog` (bool), `in_load_list` (bool).
- `initial.ship[]`: `cells` (the 20' cell ids a 40' box covers).
- `steps[]`: `slot_cells` (cells of the ship slot touched, or null); `counts` also has `discharges`. `kind` may also
  be `restow_shift`. For hatch lifts `src`/`dst` are the cover id and `"quay"`.
- `physics.frames[]`: `criteria` (which criteria set produced the numbers: `departure: sea-going IS Code`,
  `sea-going IS Code (as if departing now)`, or `in-port equilibrium (hatch cover off; no GZ curve)`), `tank_state`,
  `covers_off`, `max_sf_ratio`, `max_bm_ratio`, `gz_error` (why `gz` is empty, e.g. withheld while a hatch is off),
  `stability_violations`, `strength_violations`. `physics.n_lifts`. Frames are keyframes: `step` = lifts done.
- `comparison.baseline`: `plan_id` (registry id or null), `source` (how it was obtained — registered run, saved
  result, or measured live this session). `comparison.lower_bound`: `projected_later`, per-type counts
  (`yard_rehandles, restows, hatch_lifts, projected_later_*`), `weighted_nonproductive`, `note`, `method`.
  `comparison.reasons.kpi`.
- `rules`: `bound` (checker report bound to this plan); each family has `passed`, `total`, `source_family`; each
  rule has `source`. Family slug `other` lists checker findings outside the demo list.
- `xray`: `label`, `legal_slots_note`; rows have `event`, `chosen.op`, `chosen_logprob_unmasked`, `top_k_kind`.
- `explanation.model`.
- `kpi`: `lifts_saved_now`, `later_lifts_saved`, `yard_equipment_hours`, `berth_hours`, `thb_per_usd`. Values may be
  **negative** (the plan did more work than the baseline); show them, don't hide them.
- Lite replay (plans over 600 lifts, `app/stage_fast.py`): `meta.lite_replay = true`, `meta.planner_status` (e.g.
  `timeout` for a partial solver plan), `meta.executed_lifts`, `meta.planned_lifts`, `meta.shut_out` (load-list boxes
  not aboard). In `steps[].counts`, `projected_later_restows`, `projected_later_hatch_lifts` and
  `weighted_nonproductive` are **null** except at ~40 evenly spaced projection keyframes and the last step (hold the
  last known value in the race; don't label it as measured at that step). Current-call counts are exact every step.

## Additive fields (data pass, 29 Oct ~13:35 EDT; nothing above changed meaning)

- `meta.planner` may be `glimmer.base-mask` (verified base Muse Glimmer + mask run; never `stowlm`) or
  `muse-spark-zero-shot` (recorded Spark plan imported as a plan).
- `comparison.spark`: `{label: "Muse Spark zero-shot (Meta API)", plan_id, counts (same keys as step counts),
  checker_passed, source, file_counts_trusted: false}` or null with `comparison.reasons.spark`. Counts come from
  replaying the recorded plan through the move engine + full checker in this server, never from the run's files.
- `comparison.baseline.source` says `measured live …` only when the server ran with `--live-baseline`.
- `xray.steps[]`: `overridden` (bool|null: the model's own top-1 token was illegal at ≥1 decision position, from
  the trace's `decisions[].overridden`), `decisions` (`[{field, overridden, n_allowed, n_legal, top1, top1_legal}]`),
  `n_actions_scope`, `format_override`. `legal_slots` is now filled for tiny/small traces (recomputed on the
  feasibility engine, D79 C2 off; the recomputed legal-set size must equal the trace's `n_legal` at every row, else
  null and `legal_slots_note` says why).
- `xray.alternate`: `{plan_id, label, note}` — when the displayed plan has no trace but a base-mask run for the same
  instance is registered (open it with `plan_id`).
- `xray.highlights[]`: recorded Spark-without-mask refusals: `{instance_id, same_instance, label, spark_output
  (verbatim lines), offending_line, slot, engine_refusal (verbatim scoring.failure), point, with_appendix
  {checker_passed, weighted_nonproductive} | null, source, refusal_source}`.
- `explanation`: may come from `experiments/mvp/explain-cache/<instance>__<method>.json`, then `source` =
  `spark-cached` (or `template` when the cache entry used no Spark), plus `label` ("Muse Spark (cached)"),
  `statements`, `cache_file`, `created_utc`, `binding`. Shown only when instance content, plan SHA-256 (provenance
  excluded), checker verdict and weighted lifts all match; otherwise `explanation.cache_reason` says why.

## Additive fields (video v2, 29 Oct; nothing above changed meaning)

- `securing` (new top-level block, from the bound checker report's lashing parts; nothing recomputed):
  `{available, reason, source, method, limit: 1.0, max_ratio, passed, violations,
  conditions: [{id, phase}],                                   // departure, mid_voyage, arrival (sampled conditions)
  checks: [{key, label, ratio, limit, passed, governing_phase, by_condition: [{phase, ratio}]}],   // worst first
  stacks: null, stacks_reason}`. `key` is the checker metric (`max_fitting_ratio` = twistlocks/corner fittings,
  `max_corner_tension_ratio`, `max_lashing_ratio`, `max_racking_ratio`, `max_longitudinal_racking_ratio`,
  `max_corner_post_ratio`, `max_cell_guide_ratio`); `ratio` = the checker's worst load ÷ allowable over all stacks
  for that check, worst over the sampled conditions (fittings and rods are already against 85 % of SWL). The checker
  does not report per-stack ratios, so `stacks` is null; `available = false` with a `reason` when the report is
  unbound or the lashing part did not run.
- `meta.vessel_label` is now also filled on the lite-replay path (`app/stage_fast.py`, via
  `app.backend.vessel_display_label`, the same label the full replay uses).

## Additive fields (real StowLM run folders, 29 Oct ~23:50 EDT; nothing above changed meaning)

Built by `app/stage_stowlm.py` from the registered plan's server-side metadata.

- `meta.stowlm_run` (bool): verified StowLM model run (`planner_kind: stowlm_adapter`).
- `meta.final_showcase` (bool), `meta.final_showcase_reason` (text), `meta.final_rule` (text): D99 rule — final only for
  a StowLM run in `experiments/mvp/runs/stowlm-showcase*` on one of the three small Laem Chabang showcase calls.
- `meta.preview_label` (text | null): in-frame PREVIEW label for every StowLM plan that is not final; null otherwise
  (and for non-StowLM plans, whose stand-in labels come from the shot list).
- `meta.model_outcome` (object | null): set when a StowLM run delivered no shippable plan (`status` ≠ `ok` or no plan):
  `{kind, title, outcome, reason, failure_detail, decoded_actions, dead_end_depth, load_list, trace_rows,
  partial_moves_dropped, backtracks, wall_time_s, note}` — the run's own records, not checker results. When set:
  `steps` = [], `comparison.stowlm.counts` = null, `comparison.reasons.stowlm` = title, `kpi` = null,
  `xray.available` = false; `initial` still draws the starting world.
- `meta.backtracks` / `xray.backtracks` (object | null): `{count, rewound_actions, events[], decode_mode, source,
  note}`; `events` lists trace rows with `event` `backtrack`/`rewind` (`step`, `to_step`, `n_rewound`), else counts come
  from `result.json` `metrics.gen_backtracks` / `gen_rewound_actions`.
- `xray.steps[].at_plan_step` (int, backtrack/rewind rows only): lift index where the ↶ marker goes.
- `xray.steps[].chosen_logprob_recorded` (text | null): `"-inf"` when the trace recorded a non-finite masked log p
  (then `chosen_logprob` is null; the bundle stays strict JSON).
- `meta.recorded_twostep` (object | null): recorded two-step *score* next to the run
  (`<run>/../../<parent>-twostep/<id>.json`): `{weighted_nonproductive, passed, plan_status, source, note}`. Never a
  baseline result (no plan → not replayed or re-checked); quoted in `comparison.reasons.baseline`.

## Additive: story-panel fields (30 Oct, logic review)

- `kpi`: the values are this slice's, never a whole call's; `usd_per_call` / `thb_per_call` keep their key names for
  compatibility only and are never shown as "per call". New: `scale` ("this slice"), `thb_rate_note` (the rate
  assumption), `prize` (`app/prize_config.json`: `text`, `low_usd`, `mid_usd`, `high_usd`, `avoided_pct`, `export_boxes`,
  `kind`, `source`; null if the file is unreadable). `prize` is a labelled sizing of the problem, not a result.
- `meta`: `optimality_status`, `proved_optimal` (true only for `solver.exact` with status `OPTIMAL`; gates the word
  "optimal" in the race headline), `slice_boxes` (boxes on this instance's load list).
- `securing`: `state` = `ok|over_limit|violation|not_checked`, `reason` (null when `ok`), `violation_rules`. `passed` is
  true only for `ok`, so a condition's lashing part that did not run now blocks green. Render with
  `window.StageRules.securingState(sc)`.
- `steps[].counts` stay the running prefix, for playback only: the race table and bars read final counts
  (`comparison.stowlm.counts`) at every lift.

## Additive: brief flow (plan v5 task 2.2, 31 Oct; `app/brief.py`, `stage-brief.js`, `stage-brief.css`)

Panel on `/stage` (button BRIEF, key V, `?brief=1` opens it; hidden in `?present=1` unless `?brief=1`). Nothing above changed.
Same-origin, JSON and size gates as `/api/solve`. Launch flags: `--brief-live` (live reading and speech, still needs
`SPARK_CREDIT_READY=1`), `--brief-live-replan`, `--reader glimmer --reader-adapter <dir>`. Default: recorded readings, no calls.

| Route | Does |
|---|---|
| `GET /api/brief/state` | `{modes, default_call, calls[], prepared[], hero, voice_clips[], refusals, messages}`. First call starts the gates |
| `GET /api/brief/gates` | gate per case: `status: pending\|checking\|shown\|not_shown`, `why[]`, `before/after {plan_id, weighted, total_lifts, later, passed, shippable}`, `delta_weighted`, `boxes_moved`, `honoured`, `binding`, `plan_label` |
| `POST /api/brief/draft {text, call?}` | editable draft; never calls a reader |
| `POST /api/brief/clip {clip}` | draft from a recorded clip's stored transcript |
| `POST /api/brief/audio?call=` (`audio/wav`, ≤ 2 MB, ≤ 30 s) | draft from Muse Voice Transcribe; recorded mode returns `typed_required` |
| `POST /api/brief/confirm {draft, text}` | `status: constraint_ready\|refused\|impossible\|no_recording\|typed_required`; `rule`, `answer`, `reader {label, short, kind, on_device}`, `evidence {parser, model, adapter_sha256, request_id}`, `seconds`, `card` |
| `POST /api/brief/plan {reading}` | `kind: stored` + `gate`, or `kind: none` + `message`; always `claim_scope`, `sentence_compliance_verified: false` |
| `POST /api/brief/replan {reading}`, `GET /api/brief/jobs/<id>`, `POST /api/brief/cancel {job}` | real replan, 403 without the flag; `elapsed_s` is real and there's no percentage |

**Director API** (page, in every mode):

```js
await window.StageBrief.ready();     // resolves when gates and refusal cards have settled
window.StageBrief.prepared();        // [{kind: 'plan'|'refused'|'impossible'|'clip'|'pending', sentence, call, case, status, ...}]
window.StageBrief.show({sentence, stage, recorded: true,    // stage: listening|transcript|reading|searching|plan|refused|impossible
                        progress: 0.4, peaks: [...], full: true});   // all three optional
window.StageBrief.show(null);        // hide
```

`show` is a pure, idempotent render of data the page already holds: no network, no parser call, no plan switch. `progress` reveals the
waveform and the words in `listening`. `peaks` overrides the recorded clip's measured peaks. `full` centres a larger card. An unknown
sentence, or the hero before task 3.2, renders as pending. Labels come from the same evidence as the live panel, so the card says
"Muse Spark" today and changes itself when the recording's evidence changes. To play the ship the director loads
`gate.after.plan_id` from `prepared` data (`/api/brief/gates`) as for any plan.

## Additive: `instruction` block (D144, recorded instruction replan)

Bundle key `instruction`: null for every plan except one from a run folder whose `config.json` has
`format: "instruction-replan-v1"` (`experiments/mvp/runs/instruction-replan-dg/`, built by
`experiments/mvp/ui/make_dg_replan.py`). Also served by `GET /api/instruct/recorded[?plan_id=<id>]` as
`{"plan_id", "instruction"}`; with no id it finds the registered replan. Page: `/stage?instruct_recorded=1`.

```jsonc
{ "sentence": "...", "constraint": { ... }, "parse": "how the constraint was obtained", "applied": "cells closed",
  "before_plan": "<run-relative path>", "before_reason": null,      // reason when the before plan can't be bound
  "before_violations": ["load BOX -> SLOT: ..."],                   // recomputed on load, never stored
  "after_violations": [], "state_violations": [],                   // same
  "honoured": true, "checker_passed": true,                         // the app's own fresh checker
  "presented": true, "refusal": null,                               // false + reason: the plan must not be shown
  "flagged": [ { "box": "...", "imdg_class": "8", "before_slot": "...", "after_slot": "...",
                 "before_breaks": true, "after_honoured": true } ],
  "before_counts": { "weighted": 37, "total_lifts": 41 }, "after_counts": { "weighted": 37, "total_lifts": 41 },
  "label": "Solver replan under the instruction", "in_frame_label": "...not StowLM", "not_stowlm": true,
  "method": "how honoured was computed" }
```

`meta` gains `preview_label` (the in-frame label) and `preview_label_all_modes: true` for this plan, so the D99 tag also
shows with `video=1`. When `presented` is false, `meta.model_outcome` becomes the "not shown as a plan" card, `steps`
are empty and there is no KPI, exactly as for a StowLM dead end.

## Additive: demo v6 (plan v6 task 2.2, 1 Nov; `app/v6.py`, `stage-v6*.js`, `stage-director.js`)

Nothing above changed meaning. `/api/stage` is unchanged; under `--v6` a settled bundle is memoised while its sources and the epoch hold.

**`GET /api/v6/replan`** adds to the skeleton block:

| Field | Meaning |
|---|---|
| `gate.status` | `checking`; `shown` (both plans pass the full checker and the rule holds); `before_only` (hold the stored plan under the standing brief, `why` says the reason); `not_shown` (the stored plan failed: a gap) |
| `gate.before/after.lifts` | `weighted_nonproductive` of the plan's last frame, so it includes projected later-port lifts (D15) |
| `gate.boxes_moved`, `gate.extra_lifts` | Measured from the two replays. The page recounts with `STORY.morphOrder` and holds if the counts differ. `extra_lifts` is a gap between two plans, never the rule's cost (logic3, B1): `STORY.liftGapLine` |
| `gate.forced_boxes` | Boxes that sit, in the plan before, where the confirmed rule forbids them (`app.v6.forced_out` over the rule's own `forbids`). Strong glow; every other moved box is "the search also rearranged these". Null: the rule couldn't be asked, nothing is credited to it |
| `gate.envelope` | `{inside, boxes, teu, fill_pct, region}` from the runner's own `envelope.check` |
| `gauges` | `app.v6.gauge_walk` over every measured frame of both plans ("stay" looks at the plan played, "ends" at both; a last frame outside limits drops `line`): `{frames, outside: [{plan, step, why, criteria}], stay_green, ends_green, line}`, or null until the played plan's physics are in. `line` is the only source of the words on screen |
| `rule_counts` | `{active, off}` from the package's `rule-coverage.json`, else `verification-summary.json`; null when neither reads. Also in `/api/v6/state`. The only source of the rule count on screen. The rule panel's rows are `experiments/mvp/rules.json`, 51 of the 54 active rules: it says "listed" |
| `first_plan` | `{median_s, runs}`: the live profile's `measured_first_legal_s` from `runner/fixtures/profile-selection.json`; null until the runner publishes it, and then nothing shows |
| `rule` | `{sentence, text}` of the confirmed reading |
| `before_note`, `live_available` | The plan before's provenance line, ending in what its check really was; whether `--brief-live-replan` is on |
| `gate.recheck`, `gate.before.recheck` | `"rechecked now"` only if `full.check` ran in this process for that plan (`--no-check-cache`), `"rechecked (exact memo)"` from the disk memo, null when unknown (`checker.check_source`) |
| `POST /api/v6/confirm {sentence_sha256, rule_sha256, instance_sha256}` | The hashes in `/api/v6/reading`'s `binding`. Returns `{token, source}`; 403 if they aren't the reading on the server |
| `POST /api/v6/replan {token}` | 403 with no token, a spent token, or a token for another sentence, rule or ship. The source on the receipt comes from the token |

**`POST /api/v6/replan`** starts the live job: 403 without the flag, 409 if one runs. **`GET /api/v6/replan/<job>`** returns `{id, status: running|done|failed, elapsed_s, result: {status, passed, plan_id?, lifts?}, error, incumbents}`. No percentage, on purpose.

**Chapter API additions** (`stage-v6.js` header): a view may have `preload()` (awaited by `StageV6.prepare`), `ready`-style async `enter()` (awaited before the first seek), and `gap` (true: the shell skips it, the director plays the fallback). `StageV6.prepare(id)`, `.seek(id, tau)`, `.hide()`, `.freeze(tau)` (dev), `.state`. `ctx.label` belongs to the chapter on screen; `ctx.rewind()` restarts its clock.

**Stage hooks:** `window.__stage.peek(k)`: every panel reads lift k, with no replay or scene work. `__stage.stateAt(k)`. `scene.setMorph({state, glow: Set(box), quiet?: Set(box), marks?: [{box, slot}], flyers: [{box, from, to, p}]})` and `scene.setFlyers(list)`: the scene draws that state and those flights whatever the replay does; `setMorph(null)` gives the ship back.

**Shot keys (variant v6):** `v6: {chapter, from, to?, needs?, hole?}`, `fallback`, `show_tag`, `plan: {label}`. Card types added: `hits`, `rows`, `card`, `tally`, `footage`. `STORY.applyFallbacks`, `chapterAt`, `ifBlocks`, `hookHits`, `morphOrder`, `morphAt`, `morphShip`, `plainGauges` are pure and tested under node.

