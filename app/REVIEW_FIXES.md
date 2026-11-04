# 6.5 — wave-2 review fixes

Scope: `app/**` only. No browser, solver campaign, model call, cloud/GPU job or
`src/` edit. Browser completion remains leader-owned after policy promotion.
Every fixed finding has a regression observed failing before its fix. Tests use
synthetic physical evidence only where isolating approval/attribution behavior;
they do not claim real model inference, browser paint or ship qualification.

| Report / severity | Finding | Status | Regression coverage |
|---|---|---|---|
| code P1 | Cached artifact/source changes retain approval | fixed | `test_cached_artifact_revalidates_source_on_every_read` (status/world × replay/comparison), `test_cached_registered_replay_revalidates_instance_files`, `test_cli_instance_fingerprint_is_rechecked_after_cached_replay`, `test_initial_demo_endpoint_revalidates_registered_artifact` |
| code P1 | Mixed-case HTML script terminator in capture | fixed | `test_capture_serializes_mixed_case_html_script_terminators_safely` through actual capture serializer; all `<` escaped |
| code P2 | Host/Origin DNS-rebinding boundary | fixed | `test_review_security.py` hostile Host/port/Origin cases; all parsed requests validate loopback and actual configured port; deliberate in-process authority only |
| code P2 | Malformed report metadata aborts discovery | fixed | `test_review_security.py` malformed report/provenance/parents cases; explicit `ValueError` and discovery skip |
| code P2 | Browser verifier misses errors | fixed | `test_browser_verification_rejects_every_error_channel` console/page/request/HTTP/route/visible-error/source-drift/unshippable negative controls; no browser launch |
| code P3 | Weather clearance override shown against zero | fixed | `test_weather_override_displays_raw_clearance_and_applied_minimum`; actual checker helper, raw clearance and .02 override; clause/page cited in adapter |
| logic High | Hidden solver repair biases comparison | fixed | `test_repaired_checked_plan_is_attributed_but_never_an_approved_comparison`, `test_baseline_pipeline_explicitly_disables_repair`, replay/card badge renderer regressions |
| logic High | Meta model invisible | fixed | always-visible Glimmer/LoRA/MLX/mask/checker panel, explicit no-run state, model identity and pre-mask trace renderer regressions |
| logic High | C1 result-to-replay consumer path absent | fixed in app | `test_model_runs.py`: complete run folder and fixture imports, raw result + bound report, fresh checker replay; producer must emit the contracted artifacts |
| logic Medium | Self-declared StowLM label | fixed | missing/malformed manifest and missing binding stay unverified; base/adapter labels distinct; fixture opt-in is test monkeypatch only; comparison preserves verified identity |
| logic Medium | Unrepresentative hand-picked instance | fixed selection mechanism | pre-declared CLI split/rung/rule and frozen inventory, first qualifying id; selection and current-generator missing-split regressions; leader chooses regenerated root |
| logic Medium | Crane geometry and crossing story missing | fixed display | H line, side-oriented rows/quay, truck lane, ship/yard crossing and reported shift-reason regressions; geometry fallback explicitly labelled |
| logic Medium | Scope outdated / in-port safety invisible | fixed | departure/mid-voyage/arrival labels; `test_inport_panel_pending_then_uses_only_actual_bound_report`; pending D64 and per-lift/k renderer regressions |
| logic Medium | Real port names and fixed Thai header | fixed | actual `load_port` archetype tests plus synthetic POD/tooltips/header |
| logic Medium | Spark role overstated | fixed, superseded below | truthful stored-explanation/fallback text and deterministic-template labels; no page API call |
| logic Low | H/side source unclear | fixed | Laem Chabang-like H tagged expert Q3 only; other H and quay assumptions tagged synthetic, including custom-archetype/THLCH negative control |
| logic Low | Exact FEASIBLE appears optimal | fixed | exact incumbent backend/renderer regressions, **Exact (not proven optimal)** |
| logic Low | Vessel/baseline labels inconsistent | fixed | current params name for each known canonical fleet family, Synthetic prefix for all; E10/Navis/human-performance footnote renderer regression |
| logic Low | Brief pre-states results | deferred, outside fence | Leader edits `docs/brief.md:55–60,89–94`: replace close-to-bound/guaranteed-pass/result claims with hypotheses linked to measured evaluation rows. No app test can change that document. |
| logic Low | Future lift delta cannot be reconciled | fixed | projected total row plus explicit comparison difference and renderer regression |
| code done gap | Real approved browser storyboard | leader-owned | No new screenshot or successful planning claim. Use corrected verifier after promotion and inspect all three primary choices. |

## Contract notes and limitations

- A repaired plan can remain canonically shippable after the full checker, while
  its planner comparison is always diagnostic. The UI names `solver.scalable`
  (the actual current repair implementation), repair status and note. Two-step
  gets `allow_repair=False` on every live call, in either comparison position.
- Import checks complete manifest/trace shapes, model/runtime/version declarations,
  adapter SHA/lineage declarations and displayed-world binding. Manifest and trace
  fingerprints, result file and resolved instance files are checked on every replay,
  including cached initial `/api/demo` and comparison requests.
- Raw `PlannerResult` has no stored report/hash field. The producer must include
  top-level `report`/`check_report` with direct checker instance binding, or
  `input_sha256` with its locatable original file, or an envelope containing them.
  The app accepts both layouts. Stored verdicts never grant shipping approval.
- `epoch_id`, root digest and adapter lineage are declared provenance, not external
  producer authentication. The run contract gives no instance-root inventory path
  or root-digest algorithm to independently reconstruct that digest. Leader should
  define those if independent manifest-root attestation is required.
- The synthetic run fixture is marked `fixture: true`; it stays unverified unless
  a test monkeypatches the private fixture-verification hook. No CLI/env/imported
  data can turn that hook on. There is no invented Glimmer placeholder plan.
- Campaign storyboard roots call `eval.harness_inventory.select_frozen_instances`
  and `load_bound_instance` with the pinned split inventory, then select by input
  contents and id only. Simple non-campaign roots use explicitly labelled family
  split declarations. No solving, rerolling or outcome filtering is performed.
- Current move models expose no crossing/forced-shift report. Ship and yard
  highlights use explicitly labelled instance geometry, never a feasibility gate.
  A crane-height shift tag appears only for a reported reason. The future engine
  data seam accepts reported crossing stack ids; source owners must expose the
  canonical report when their activation switch lands. No physics is approximated
  or weakened by this display.
- D64 is pending until `port.stability.cargo_operations` exists. Scalar per-lift
  evidence is retained and displayed; unknown lift verdicts say diagnostic rather
  than pass. The actual producer's final per-lift schema must remain coordinated.

## Verification

Initial failures: security 21 plus two report-object cases; model import 12 initial
cases plus raw-layout support; frontend 16 initial cases plus geometry/profile/
attribution refinements; root seven logic cases, four missing browser-error gates,
and first-failing integration checks for identity forwarding, CLI source drift,
lowercase Host, initial `/api/demo` drift and frozen split selection.

Commands (single-core BLAS/OMP, no cacheprovider):

```sh
UV_CACHE_DIR=/private/tmp/stowlm-w2-uv PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 nice -n 10 uv run pytest -q -o addopts='' -p no:cacheprovider app/tests/
node --check app/static/app.js
node --check app/tests/renderer_harness.js
git diff --check -- app
```

Final result: **296 passed in 11.35 s**, zero failures/skips. Both Node syntax
checks and scoped whitespace check passed. The sandbox denies setting `nice`
priority; execution continues with thread caps. No heavy job was run.

## Revised adapter / trace contract follow-up

`adapter_clean=true` is required for the clean StowLM label; production-gold data
and empty lineage stubs must agree. Diagnostic/pipeline-test taint wins over a
clean claim, including a readable weight-bound lineage sidecar. Such taint is
retained in PlannerResult provenance and cannot pass the canonical shipping gate.
Fixture verification remains test-only. Added first-failing label, actual-lineage
and synthetic passing-check negative controls.

The trace is captioned **selected sample, replayed**. Physically possible action
denominators are shown only when recorded with that scope. Top-five entries are
vocabulary tokens, including empty EOS spellings; structured actions and terminal
rows remain readable. Only explicitly aggregated all-samples decision/token rates
are displayed; repair's original draft metrics are retained, rather than deriving
batch statistics from a selected sample. Missing/invalid aggregates are unavailable.

Real fixture: `experiments/sft-pilot/pipeline-test/c1-loop`, first tiny result.
Its actual status is error and plan is null. SHA-bound config inputs resolve the
world; no plan, count, replay or checker approval is fabricated. Production JS
renders its diagnostic model label, selected trace and actual no-plan error.
That historical run lacks the newer aggregate statistic and physical denominator
scope; both absences are labelled. No inference or browser was launched.

First-failing tests: 12 adapter-label cases, eight initial trace/aggregate cases,
three initial failed-run cases, and later lineage/taint/current-writer checks.
`app/tests/`: **336 passed in 17.74 s**; Node syntax and scoped diff checks pass.
All changes remain in `app/**`, no `src/` edits or heavy jobs.

## 6.5 — offline Spark explanations and honest validation verdicts

| Report / severity | Finding | Status | Regression coverage |
|---|---|---|---|
| `logic-explain.md` High 1 | Stub, explicitly unvalidated or not-run reports can appear to pass | fixed in app | `test_replay_never_labels_incomplete_or_tainted_report_passed`, `test_explicit_unvalidated_marker_is_a_fail_closed_display_gate`, `test_tainted_or_incomplete_checker_never_renders_overall_pass`, `test_job_summary_cannot_call_an_incomplete_pipeline_report_a_pass`; first-failing controls for report/input/result stubs, missing parts/counts, not-run/error checks and explicit flags |
| `logic-explain.md` High 2 | False section-ordering claim and no stored Spark path | fixed mechanism; prefetch deliberately unrun | `test_ui_drops_the_obsolete_section_ordering_claim`, `test_stored_spark_selected_only_after_current_offline_revalidation`, `test_stored_explanation_label_date_and_text_are_visible_in_replay`, `test_stored_explanations.py`; missing feature, stale/tampered/invalid artifacts and unbound prefetch checks observed failing before fixes |

Changed: `validation.py`, backend/checker/comparison/job gates, the offline
`stored_explanations.py` loader and `prefetch_explanations.py` command,
`explanations.py` selection, replay/capture transport, static labels/rendering,
tests and app documentation. All changes are in `app/**`.

Contract changes: presentation adds `validation_status` / `validation_reasons`;
incomplete or tainted evidence is **NOT VALIDATED** regardless of raw flags.
Raw reports remain intact. Fully completed clean rejection remains FAIL, and
`PlannerResult.shippable` is still required for approval. Job summaries use the
fresh replay's gates. Safe explainer verdict/text is displayed verbatim; an
older contradictory PASS is suppressed, never repaired into an approval.

The explicit prefetch CLI fresh-checks and verifies report binding before
constructing PlanExplainer, retaining all normal Spark credit/spend gates. Its
`<plan filename>.spark-explanation.json` sidecar records model, UTC date,
validator verdict, raw response and complete normalized instance/plan/report
digests. Every replay and comparison re-reads the artifact and runs the current
validator using an in-memory response client. Missing/rejected/stale artifacts
use the deterministic template. This sidecar cannot grant shipping approval.
The renderer labels accepted text **Explained by Muse Spark; the checker's verdict
is authoritative**, with date/model/validator evidence. Neither serving nor
capturing a page makes a Spark call.

The upstream explainer changed to version 3.0 during this job. The real prose
recordings in `experiments/spark-explain/recorded.json` now correctly reject under
its statement-selection protocol. Tests retain those actual rejection controls;
accepted-path tests use an explicitly synthetic statement-ID response over the
recorded C instance/plan/report, never a claimed new model response. No prefetch,
Spark/API request, browser launch or heavy compute job was run. No physics or
regulatory rule changed; no approximation or decision change is needed.

Verification: **376 passed in 27.39 s**, using the thread-capped `app/tests/`
command above. Both Node syntax checks and `git diff --check -- app` pass.
The 40 new tests include replay/capture/comparison integration and per-read
fallback after mutating a sidecar on an already cached replay. Existing tests
that asserted PASS for incomplete synthetic evidence now assert NOT VALIDATED;
isolated positive shipping tests explicitly construct complete synthetic evidence.

Leader-owned browser check: open a saved plan in the local UI, expand its
explanation and verify **Deterministic template explanation** when no sidecar is
present. After a separately authorized prefetch of that exact plan/instance,
reload and verify the requested Spark label, UTC date, accepted validator and
unchanged checker verdict on both replay and comparison. Mark the sidecar's
validator verdict false and reload the same cached replay: it must fall back to
the template. Inspect a stub/not-run diagnostic plan: overall badges must say
NOT VALIDATED and never approve it. Serving these checks must make no remote
requests. Browser verification remains with the leader; real prefetch remains
unrun as explicitly ordered.

Needs the leader: run authorized prefetch against the final saved demo plans
before recording Spark footage; no accepted version-3 real response was generated
by this job. Needs the user: none for this change; any later paid call retains
the existing credit authorization requirements.

## 6.5 — amended decision-token and dead-state trace contract

| Contract / finding | Status | Regression coverage |
|---|---|---|
| Amended 29 Oct: per-action box/slot decisions and separate opcode overrides | fixed | `test_decision_token_preferences_replace_the_opcode_preferences`, `test_decisions_without_decode_loop_exact_counts_are_retained`, malformed-new-evidence controls, current/legacy override-scope controls and two candidate-shadowing regressions |
| Added 28 Oct / logic-mask Medium: terminal dead states and run fallback/repair rate | fixed | root dead-end controls with/without rule IDs, `test_dead_state_rules_are_not_claimed_as_single_causes_and_counts_are_visible`, `test_unattributed_dead_end_without_action_does_not_invent_a_choice`, `test_run_rates_dead_end_summary_outcome_and_files_are_retained` |
| Mask budget exhaustion is its own outcome; older keys are optional | fixed | `test_mask_time_limit_is_its_own_outcome_even_after_repair`, `test_repaired_deadline_stays_mask_time_limit_not_model_failure`, old-run and old-trace not-recorded controls |

Changed: `model_runs.py`, static trace/status rendering, two new trace test
modules, legacy aggregate metric fixtures and app documentation. All changes
remain in `app/**`. Seven renderer regressions and 16 backend regression cases
were observed failing before their fixes; additional positive/integration
controls cover the producer's actual recorded run.

Contract changes: `model_run.mask_override` now identifies box/bay/row/tier scope
and separates format/token statistics. Ambiguous older decision rates stay
unavailable. Per-action `decisions` are retained and validated; missing exact
counts, top-five details and new keys never become estimates. Terminal dead-end
rows do not need an invented action, logits or timings. `model_run.run_rates`,
`dead_ends` and `outcome` carry normalized diagnostics. Optional `summary.json`
and per-instance `outcome.json` are fingerprinted with the run. Recorded outcome
labels never carry a stored checker approval into the display. Time-limit
classification also reads the original draft metrics preserved by repair.

The fallback/repair-rate definition is the producer's existing repaired-instance
fraction, including failed repairs, not a newly inferred attempt rate. Active
dead-state rules can overlap; the app does not claim a unique cause. Screen
counts are recorded probes and not rejected-action totals. No physics,
regulation, decision or source file changed; no approximation was introduced.

Recorded integration: imported
`experiments/eval-c1/runs/decisions-smoke/instances/v3.random_yard.tiny.110280001/result.json`
through `load_inputs` / capture transport and rendered production JavaScript.
The box/bay/row/tier preferences, masked strike-throughs, opcode statistic and
100% run repair fraction render, while the failed diagnostic remains NOT
VALIDATED. This read-only replay did not run Glimmer, Spark, a solver or a browser.

Leader-owned browser check: open the recorded run with
`uv run python -m app --run-folder experiments/eval-c1/runs/decisions-smoke --entry instances/v3.random_yard.tiny.110280001/result.json --no-discovery --port 8765`.
Expand the selected-sample trace and its first action: check the four decision
fields, struck-through masked choices, separate opcode override and adjacent run
fallback/repair rate. For a dead-end run, check the terminal depth, active-rule
label and unattributed counts; a mask time-limit run must retain that outcome
even after repair. Re-open the historical pipeline-test run to check missing
new metrics say not recorded. These displays never approve a diagnostic result.

Needs the leader/user: none to implement this contract. Browser verification
remains leader-owned. No API/model calls, paid jobs or heavy compute were run.

Verification: the thread-capped `app/tests/` command above passed **403 tests in
79.71 s** (zero failures/skips). Both Node syntax checks and the scoped diff
whitespace check passed. The recorded producer smoke is present and its backend
and renderer integration controls both passed; no model was loaded.

## 6.5 — static cache policy and recorded trace transport

Static HTML/JS/CSS responses now explicitly send **no-cache, no-store**; API
responses keep their existing no-store policy. Five HTTP regression cases
(`/`, `/index.html`, `/app.js`, `/style.css`, asset query URL) failed before the
change because no-cache was absent. The server always reads the current file.

`test_real_trace_flow.py` adds three recorded import → DemoService → HTTP replay
JSON → production-renderer checks, with no inference or altered trace evidence:

- `decisions-smoke`: box/bay/row/tier positions, actual masked top-five tokens,
  84 decision positions versus 21 opcode positions and 100% run fallback/repair.
- `mask-diag-smoke`: genuine terminal dead end at depth 2, active screen rule
  retained separately, recorded unattributed count and absent attribution labels.
- `trace-smoke-b4`: older trace fields and explicit not-recorded labels.

No present recorded terminal dead-end row has attributed rule IDs. Actual screen
rules are not promoted into those IDs. The real-data attribution check therefore
asserts not recorded / unattributed; existing synthetic attributed-row controls
verify the positive renderer path. An attributed real trace requires a new run
from fix-t8-sft; no model run is authorized or started by this task.

Changed files: server, cache tests, new real-flow tests and app documentation;
also the explicitly requested `.claude/launch.json` entry **stowlm-ui-trace**,
the named exception to the app-only fence. It opens the newer decision-token
run at `http://127.0.0.1:8771`, numeric-library threads capped at one, discovery
off. Existing launch entries are unchanged. Leader browser check: launch that
entry, reload normally, check no-cache/no-store on HTML/JS/CSS and expand the
first trace action; all four token fields and separate override/fallback metrics
must appear. There is no invented passing plan or model inference.

Verification: **411 app tests passed in 43.06 s**, using the same one-thread,
single-process command above. The launch JSON/name/port/selected run parsed and
validated, and `git diff --check -- app .claude/launch.json` passed. No browser,
model/API call or heavy compute job was started. Real attributed terminal rule
IDs remain a producer-artifact gap, not an app rendering failure.

## 6.5 — no-plan message punctuation

The planner's ballast message already ends in a full stop; the no-plan renderer
appended another before its decode-trace sentence. The renderer now preserves
existing sentence punctuation, trims trailing whitespace and adds a period only
when needed. `test_no_plan_message_does_not_duplicate_sentence_punctuation`
reproduced the exact doubled-stop message before the fix and also covers missing
punctuation, trailing whitespace, exclamation and question marks. App-only change;
no source messages, verdicts or planner behavior changed.

Verification: **416 app tests passed in 29.27 s** with one-thread numerical
libraries; production JavaScript syntax and scoped whitespace checks passed.
