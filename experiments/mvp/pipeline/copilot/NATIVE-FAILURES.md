# Native outcome diagnosis

This is an offline audit of saved records, not a rerun or a revised score. The
[four-case recording](fixtures/real-recovery/results.json) retains **2/4 observed
recoveries and 0/4 strict goals**. The exact-sentence reader was a fixture; no
adopted-reader accuracy is measured. No expensive native work or paid call was
made for this diagnosis.

| Recorded case | What actually happened | Why the strict goal failed | Ownership and limit |
| --- | --- | --- | --- |
| `native_empty_catalogue_recovery` | A real catalogue miss was followed by a fresh full checker pass and confirmed-instruction audit. The displayed plan had 27 weighted nonproductive lifts, including projected later-port work. Final prose was unchanged by the gate. | A model response prefixed a JSON action with prose and required protocol repair, so the strict raw-behavior condition failed. | Model protocol failure; useful recovery was observed. This was evidence about the displayed plan, not a newly generated replacement. |
| `native_bad_plan_recovery` | The deliberately altered input omitted a load. The native checker correctly rejected it with `objective.all_loaded`; lookup found no replacement, and the sandbox did not cure the missing box. | No checked replacement was available. Raw prose also triggered the recorded numeric/acceptance gate. | The input was intentionally damaged by the harness, not produced by engine search. The host scope excluded `replan`, `wait_replan` and `try_ladder`, and the actual catalogue was empty. The replacement goal was therefore unsupported by the available services; this does not show that the engine failed to find a solution. Gate-language findings belong to the separate safety audit. |
| `native_move_explainer_recovery` | The harness deliberately injected one explainer `OSError`. The model recovered with an actual full sandbox verdict for a changed target: legal, with zero added current-port lifts. A fresh check of the current plan also passed. | The exact target `0020284` appeared in authorized arguments but was absent from tool-result fields. The literal current-turn number gate rejected its otherwise sourced restatement. Protocol repairs also occurred. | This adapter omission is fixed: `try_move` results echo the exact attempted typed `box` and `slot`. The echo supplies reference identity, not a legality claim. The verdict still determines legality, no candidate is installed, and no accepted plan hash is returned. Historical scores remain unchanged. |
| `native_impossible_negotiation` | The reader-owned proof established that the zero-deck-tier request needs 22 TEU of cells with only 8 available. Four arithmetic-supported relaxations remained explicitly unchecked; no measured alternative costs were supplied. | The predefined goal demanded two checked alternatives with lift comparison, but none existed. The raw response also triggered the recorded gate. | Catalogue preparation failed its native gate, leaving the catalogue empty; host scope excluded planning jobs. The harness could not provide the candidates needed by this goal. This is not an engine search failure or permission to invent alternatives, costs or feasibility. |

The recorded host scope is explicit in `real_recovery.ALLOWED` and in the frozen
case permissions. These four cases did not exercise a native binding search.

## Why catalogue preparation failed

The [prepared baseline](fixtures/agency-native/summary.json) passed its full
native report, but its separate [lattice crane-frame gate](fixtures/agency-native/baseline-lattice-gate.json)
failed `iscode.loading.draft` at steps 15–25. All frames were computed. Those
frames report `error: null`, `stability_passed: false`, and actual draught-limit
violations, rather than a binding exception. For example, step 15 reports
forward draught 7.06996087413718 m against a 7.05 m limit. The final departure
frame uses declared departure ballast and passes; intermediate frames use the
arrival tanks. A final-report pass therefore does not establish a passing
all-crane-frame catalogue entry. The stricter rejection remains in force. No
checked native alternative or hero catalogue is claimed from this preparation.

## Explanation payload defect and offline fix

The additional [functional explanation probe](fixtures/agency-v6-native-explain.json)
did produce real explanation statements after native checking. Two evidence
links each embedded the whole report `/parts` tree. The saved explanation alone
was 724,336 UTF-8 bytes, and a later recorded model call had 280,835 input tokens.
There were protocol errors followed by generic model unavailability. The record
does not preserve a specific provider exception, so context size is an observed
payload problem, not a proved cause of that unavailable response. This probe is
not an additional successful recovery or a fresh benchmark.

`ToolSet._explain_box` now retains the full API result privately in
`_explanation`, alongside the full native `_report`. Outward statements, language,
render checks, gauges, provenance and exact scalar references remain intact.
Whole-report aggregate links retain their original source, hash, JSON pointer
and href; they carry an original-value hash and an explicitly labelled pointer
to a compact projection, rather than pretending the projected value is the
original JSON-pointer value. The compact checker summary and audited confirmed
rules are returned once. The explanation remains diagnostic and cannot accept
or install a plan.

Replaying only that projection over the saved explanation reduced it to 20,708
UTF-8 bytes, retaining all 11 statements and compacting both aggregate links.
This measures explanation projection only; a current tool result also includes
the compact checker summary. No post-fix live-model improvement is asserted.

## Binding confirmation is already repaired

The first [background binding probe](fixtures/binding-background-probe/result.json)
failed before planning because the adapter passed a plain `ReadBrief` to the
landed API that requires an authenticated `ConfirmedBrief`. This was a copilot
integration defect, not a budget misuse or report-serialization defect.

Runtime 7 seals the separate capability only in the host method
`ToolSet.confirm_reading(reading_id)`, after reading, standing, drift and
resolution checks. Its source is exactly
`copilot_planner_confirmed:<reading_id>`, naming that application host event;
it does not independently establish human identity. The scripted harness calls
this host action explicitly. The capability is bound to the original reader
payload/authentication and verified instance context, copied into detached work,
included in job freshness identity, and cleared after standing edits. Default
binding verifies it; injected replanners keep receiving the original receipt.
Neither the model registry nor the worker creates a confirmation.

The [second probe](fixtures/binding-background-probe-v2/result.json) completed the
real binding path with `no_checked_plan` under explicitly disabled stages and
accepted nothing. It proves the repaired boundary and responsive background
conversation, not successful production search.

## Later hero: separate numerical and integration outcomes

The later hero uses different, larger prepared data than the earlier baseline
above. Its [original engine candidate](fixtures/hero-final/native/original-candidate-gate.json)
failed the real all-frame release gate at step 76: heel was
**2.003047833000845 degrees**, stability failed with
`port.stability.cargo_operations`, strength passed, and `error` was null. The
gate stopped after the first failing sampled frame; it does not certify the
unvisited frames. This is a measured candidate failure, not a binding or report
serialization exception. Its 66-lift departure result is insufficient for
all-frame acceptance.

Two distinct stored alternatives did pass actual native full checker, exact
rule audit and **all 146 crane frames** during preparation: DG clearance has
70 weighted nonproductive lifts, and bay closure has 82. The separately checked
66-lift baseline passed all 144 frames. These counts include projected
later-port lifts. [HERO-LATENCY.md](HERO-LATENCY.md) gives their original native
check dates and measured CPU times. Later receipt handoff authenticates that
recorded work; it does **not** run those numerical checks again.

The first two paid hero takes then encountered an independent **copilot
integration defect**. `guarded_envelope` lazily imports
`experiments.mvp.pipeline.runner.envelope`; importing the package runs
`runner.__init__`, which imports `runner.core`. The latter unconditionally sets
`STOWAGE_VESSEL_CACHE_DIR` to `runner/cache/vessels` at import. This replaces the
campaign's `copilot/cache/agency-native/vessels` after successful native receipt
preflight. The checker correctly detects environmental drift, so every current
native token becomes unavailable and stored lookups miss. Spark decisions,
reader interpretation and plan normalization are not needed to reproduce it.

The [offline diagnosis](fixtures/hero-verified/diagnostic-only.json) preserves
exact recorded Spark reader outputs, source hashes and before/after tokens.
All three receipts validate before the first tool call; the first call changes
the cache environment and causes drift. Restoring that exact original
environment restores receipt validation with the same `8c016077…` physics epoch.
No source, checker flag, receipt or numerical criterion is changed by that
control. A separate preserved-import replay offers both checked alternatives,
then reads and host-confirms the DG sentence and retrieves its checked plan.
This is an offline adapter control, not a new live-model or numerical result.

The fix is confined to the copilot: preserve the existing cache environment
around the lazy import, restoring its exact value or removing the import-added
key when originally absent, including on `ImportError`. The real envelope API
and all epoch/source guards remain in force. Runner, reader, engine, checker and
binding source files were unchanged. Four simulated import regressions, an
actual fresh-interpreter first-call/epoch/two-choice regression, and a refusal
wording regression failed before the fix. Verification afterwards covered
**95 tool tests and 27 handoff tests** offline.

The saved catalogue proof's **60 open cells for 143 required** is a necessary
screen upper bound, not the exact capacity after both zero-tier instructions
have been applied. A compilation shortfall can end the screen before the full
instruction intersection is built. Its original scope metadata is retained;
the tool now says the necessary screen gives *at most* that capacity and even
that upper bound falls short. It does not invent another measured count.

The pre-fix verified run also retained these terminal job receipts:

| Receipt under `fixtures/hero-verified/` | Measured job elapsed | Recorded outcome |
| --- | ---: | --- |
| [shot10-background-terminal.json](fixtures/hero-verified/shot10-background-terminal.json) | 81.220 s | `ValueError`, no native result available, no plan accepted |
| [hero_confirmed_dg_stored_choice-background-terminal.json](fixtures/hero-verified/hero_confirmed_dg_stored_choice-background-terminal.json) | 15.653 s | `ValueError`, no native result available, no plan accepted |
| [background-terminal.json](fixtures/hero-verified/background-terminal.json) | 69.948 s | `ValueError`, no native result available, no plan accepted |

Those receipts establish terminal failure and non-acceptance. They omit the
private exception traceback, so they do not establish the exact origin of each
`ValueError` or support blaming binding serialization. The independently
reproduced cache drift explains unavailable handoff/lookups. It must not be
silently substituted for a captured worker exception diagnosis.

Historical paid artifacts remain unchanged. The original take started at
2026-11-02T05:49:04.713640Z and its saved [call total](fixtures/hero-final/calls.json)
is **$0.11908605**; the verified continuation started at
2026-11-02T06:03:22.540113Z and its [saved total](fixtures/hero-verified/calls.json)
is **$0.19798450**. These are those files' model-call totals, not cumulative task
spend or native CPU charges. They exclude the later post-fix run. Only the
completed post-fix native job below is audited here; no fresh-goal score or
separate demo outcome is inferred.

## Completed post-fix native job: candidate correctly rejected

The [post-fix terminal receipt](fixtures/hero-postfix/background-terminal.json)
records job `37bbf4e0198842e2aff44cf8cbb6f714` completed after **213.496 seconds**,
with `status: relaxed_plan`, `passed: false` and no accepted `plan_sha256`.
Unlike the earlier masked `ValueError` terminals, this worker returned an actual
candidate through the default real binding path and reached native checking.
This establishes completed native work, not a successful plan or a proof that
the requested instruction is impossible.

The confirmed rule was the reader envelope for at most **2 occupied hold tiers
in bay 9**. Its independent instruction recheck passed. The binding capability
remains a separate host event, `ToolSet.confirm_reading`, with source
`copilot_planner_confirmed:<reading_id>`; the compact terminal does not retain
the exact reading ID or confirmation-source field, so no literal capability ID
is reconstructed from this receipt.

The [associated native gate](fixtures/hero-postfix/native/additional-e6c14c4071ffc6db392453e8b0ac3cd67d38e375a6191bfcd817d3db31ab6d7c.json)
completed at **2026-11-02T06:26:33.406052Z**, with **127.779961 seconds** gate wall
time. The full checker failed with 404 reported violations: 394 securing, nine
visibility and one containership stability second-check violation. No checker
part reported an execution error. The all-frame test independently stopped at
step 24: measured heel **2.037241970645079 degrees**, stability false with
`port.stability.cargo_operations`, strength true and `error: null`. It visited
27 frames and explicitly reports `all_frames: false`. The instruction passing
therefore does not cure the physical failures.

The failed candidate's report counts 94 weighted nonproductive lifts, including
projected later-port work. That is a diagnostic count, not a feasible alternative
cost. The native file retains its failed-candidate hash as evidence; the terminal
does not promote it or reuse a prior accepted hash. This outcome belongs to the
reduced-budget engine candidate and its correct rejection by the safety gates,
not an import failure or binding serialization error. Neither checker criteria
nor numerical sources were changed to obtain it. This audit reads only the
completed terminal and its associated gate, not the fresh-goal raw traces or
the separately running demo take.

## Offline verification

The explanation-size and typed-target regressions both failed before these
changes and pass afterwards. The actual binding validator regression forbids
`plan_with_rules`, proving capability handling without running native planning.
The earlier tool, job-lifecycle, async-safety and probe verification covered **128 tests**. Checker,
taint, instruction-audit and planner-confirmation gates were not bypassed. The
prompt, authorization, safety runtime, historical traces and scores were not
edited in this change.
