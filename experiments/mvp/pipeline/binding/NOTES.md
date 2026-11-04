# Confirmed rules on Frontier Engine 0.13.0

Task 0.5 adapter. Only this directory is changed. The pinned review-fixes
engine manifest and all 257 pinned native/parameter files are verified before
and after every invocation and before checked-incumbent publication. Both
manifest bytes, the expected source tree and full required file/hash maps are
frozen in `provenance.py`/`provenance-pins.json`; a refreshed or truncated
manifest cannot redefine the release. Final/publication identities must match
the initial invocation identity, even if both report a pass. Engine source identity:
`e768ee9e7a2ee2c06717c0192a4b91acc6400c29fb0808deed2c14be3ce9229a`.
Manifest file SHA256:
`d4febd15222fd783584ddbffb8003d5e281baa7f7de95cb4aa5be7d536f0705b`.
Native manifest SHA256:
`55a2de7304500675dbfc1aaca14ec25e7ffe6e6ed2a04d0e88e58afc2eba6071`.

## Runner interface

```python
from experiments.mvp.pipeline.binding import plan_with_rules, execute_brief, confirm_brief
result, evidence = plan_with_rules(instance, validated_reader_answers,
    budget_s=90, finalize_s=30, config=engine_config, on_incumbent=publish)
confirmed = confirm_brief(authenticated_read_brief, source='planner_ui:explicit-review-event')
result, evidence = execute_brief(instance, confirmed,
    budget_s=90, finalize_s=30, config=engine_config, on_incumbent=publish)
```

Use `evidence['passed']` for a pass. `result is not None` also permits a
clearly identified relaxed plan. A relaxed result has `status='dead_end'`,
`evidence.status='relaxed_plan'`, `passed=False`, exact native broken-rule
names, tier/level, and boxes left ashore. No instruction can be peeled. A
candidate with a T0 breach, failed replay, missing checker count, or failed
instruction audit is never released. No acceptable witness returns
`no_checked_plan`, with a reason. Unsupported/infeasible compilation and
proven necessary-screen failures return `constraint_unavailable`. Invalid
API types and unauthenticated/mismatched receipts raise `TypeError`/`ValueError`.
An authenticated but unconfirmed `ReadBrief` deliberately raises `ValueError`
with an instruction to call `confirm_brief`; no compatibility auto-confirm exists.

Answers must be reader schema objects, revalidated against this instance;
dictionaries and natural-language strings are rejected. `execute_brief`
requires a `ConfirmedBrief`: a frozen, reader-compatible receipt with a separate
HMAC over the exact reading payload, reader authentication and nonempty host
confirmation source. It verifies that approval before the reader/input binding,
stops on unresolved voyage sentences, and reconstructs constraints exclusively
from authenticated accepted voyage readings. Standing sentences keep the
public API's preference role and do not become hard rules. Importing
`public_brief` loads the historical 0.9 driver definitions but never calls that
driver, installs its hooks, loads a model, or replans through it.

## Binding method

Vessel surgery is deliberately avoided. Existing instruction code documents
that changing vessel cells invalidates the fleet-parameter geometry digests.
The original instance and slot/container identifiers remain unchanged.

`hooks.binding_scope` temporarily wraps the native feasibility engine's
`build_tables`. It shallow-copies each table and ANDs its static box/slot
compatibility matrix with the compiled `forbids` predicate **before** the native
engine derives compatibility/completion caches. The original cached table is
never mutated. Page constructors and pricing consume that matrix; assignment
LP `pair`/variable generation excludes its false edges. Private ladder masks
wrap the captured table builder, retaining the instruction even at L5. Public
table builders and captured aliases are also bound for targeted exact CP
construction; its optimistic DG unary relaxation is intersected again with the
confirmed mask, so it cannot reopen forbidden variables. Native
constructor geometry predicates and public/private `Problem.score` also reject
forbidden placements. All wrappers restore on normal and exceptional exits.

| Rule | Compiler and meaning | Binding |
|---|---|---|
| `close_bay` | Existing tighten-only reader compiler; loading/restowing into either footprint cell of the named bay is prohibited. Discharges and hatch lifts remain allowed; retained unmoved cargo is outside this instruction's occupancy semantics. | Static edges removed for every box. |
| `max_tiers` | Existing occupancy-aware compiler caps the lower allowed tiers of each named stack/zone. Retained cargo conflicting with the cap is rejected. | Static edges removed for every box above the cap. |
| `dg_keep_away` | Existing compiler covers primary/subsidiary DG classes recursively and checks both footprint cells. Distances in 20-foot bays; accommodation distance retains the reader's documented geometric approximation. Retained conflicts are rejected. | Static edges removed for the applicable boxes. |
| `one_port_per_hatch` | Small physical-cover/POD capacity allocation, across hold and deck and retained cargo, with no port-sharing fallback; reader's existing strict compiler creates the mask. | Allocation becomes a per-box static mask; actual final physical-cover occupancy is audited again. |

Every returned candidate is independently checked with each compiled
constraint's `violations` and the full native checker on the **original**
instance. Native `peel.inspect` supplies exact broken rules, ashore boxes and
the unpeelable T0 verdict. Existing `capacity_shortfall`, retained-cargo checks
and `per_box_slot_screen` are reused. Universal closures are intersected before
the aggregate TEU screen, catching instructions that are individually possible
but jointly close all capacity. These screens are necessary conditions, never
certificates that a stow exists. An allocator timeout is unavailable, not proof
that the natural-language instruction is globally impossible.

The original reader hatch allocator solves a full grouped slot-packing model
with a fixed 20-second limit. It returned UNKNOWN on both first C6 medium
probes. `allocation.py` instead assigns each load box to a deduplicated physical
footprint signature (cover IDs and cell counts), with aggregate per-cover cell
capacities and cover/POD selectors. A 40/45-foot footprint touching two covers
links both selectors. Retained cells consume capacity and fix their cover PODs;
uncovered deck cells form a separate pool. Other confirmed masks restrict the
new-load signatures, irrespective of sentence order. A retained box in a closed
bay is not falsely treated as a loading violation.

This is a necessary capacity relaxation, **not an actual slot packing** or a
checker certificate. Feasibility chooses a conservative mapping; the engine
still has to find a real plan. Infeasibility screens out a complete packing
under the instructions; a three-second solver timeout says unavailable with no
infeasibility proof. The native strict reader compiler and caller composite
perform the final rule audit. A 121-load/six-cover development smoke compiled
in 0.571 seconds, including a 0.185-second CP-SAT solve (one sample, not a full
planner measurement).

## Workers and checked incumbents

One invocation owns process-wide hooks at a time; concurrent/nested invocations
in one process fail explicitly. Use separate processes for concurrent requests.
The scoped multiprocessing `BaseProcess.start` wrapper supplies an importable,
pickleable trampoline carrying the exact composite constraint. Each spawned
worker enters the same binding scope **before its original target**, including
constructor/LP/ballast worker targets. Fork workers retain inherited bindings.
Short append-only worker events record PID, hook activation and forbidden-pair
count; they are returned in `binding_audit.worker_events`, and the temporary
event file is deleted. Match child PIDs: parent table events are also recorded.
Pools must be created and drained within the solve scope; pre-existing external
worker pools are not an instruction-aware interface.

`planner.publish_candidate` is nested, with no public callback parameter.
Without copying or editing engine code, an observer at unchanged native full
checker call sites supplies checked incumbents from publication and finishing
paths. Reports are never weakened. Only checks on the exact original instance
object can publish, excluding peeled copies. Each strictly cheaper native full
pass receives an independent instruction audit before `on_incumbent(event_dict)`
fires; final release performs another full native check. Sorting candidates does
not run another redundant full check. Events contain JSON plan/checker data, rules,
lifts, elapsed time, pinned identity and `native_full_checker_observer` stage
attribution. Consumer exceptions are recorded and do not erase the held plan.
Final evidence retains native per-stage timings and winning lane. A separately
held checked incumbent survives a later optional engine failure/rejected result.
Child callbacks are not transported as events: parent checking of returned
worker plans supplies the publication. Existing and lazily imported checker
aliases are restored at scope exit.

## Budgets, admission and limits

`budget_s=None`, `finalize_s=None`, `config=None` retain native defaults. The
engine documents `budget_s` as extra work, **not an end-to-end deadline**.
Tiny/small keep historical search/constructor floors and a minimum 300-second
finishing allowance. Medium assignment, polishing, peeling, proof and area
work have separate Config budgets. Full checker/replay calls are atomic and
can overrun. `measure.profile('live')` records a 90-second extra allowance and
explicit smaller additive stage budgets; actual wall time is the measurement.
No hard interruption or silent engine ablation is presented as a checked plan.

Before CPU work, admission loops until the fleet telemetry has mask population
at least eight, load1 at most 45, and age below 120 seconds. Missing/stale readings
wait rather than raise. BLAS and in-port workers are one. Measurement launchers
use at most four outer jobs, reserving the fourth whenever verification runs.

`runtime_cache_scope` redirects native stability and linear cache constants,
including function keyword defaults that captured the original directory.
Vessel-cache environment overrides must resolve inside this directory. The
scope covers compilation, search, worker targets and independent final checks,
then restores the original paths. Cache keys and native validation are unchanged.
Early measurement rows predate complete cache isolation and may have accessed
the native shared derived-data cache; shared files were not removed or changed
deliberately. Subsequent rows record exact physical-cache seeding and source
snapshots. These are numerical vessel artifacts, never stored cargo plans.
Cold and warmed cache timings are distinguished in measurement artifacts.

The first medium live profile exposed a native `max(900, budget_s)` warm-polish
allowance. The explicit `live-v2` measurement profile disables `warm_polish`
and `work_count_search`; it is an exposed Config ablation, recorded per row.
Untouched default-budget/config runs remain separate. Interrupted diagnostic
runs have unknown outcomes and are excluded from pass/failure totals.

The original hatch allocation below was one conservative software-chosen
cover/POD mapping. The review-three public API now ranks and tries up to three
distinct mappings, preferring whole-hatch panel pairs and then a split near the
load-TEU demand ratio. This remains a bounded conservative search, not a search
of every semantically possible mapping. Full checking remains mandatory.

Any native optimality claim is relative to the compiled mask domain. Top-level
`proof_scope` explicitly qualifies this, including the chosen hatch allocation.
It does not establish optimality over every semantically possible cover/POD
allocation. Raw engine proof/stage metadata is retained for provenance.

The test matrix exercises all four types against the actual page model, actual
assignment variable pairs, and actual L5 peeled private tables; release tests
also inject noncompliant results from every lane. A real spawned worker rebuilds
a known public C6 development witness through native `Problem.score`, rejects
the otherwise legal 109-lift baseline under the same rule, and passes all 23
original-instance checker components with the compliant 71-lift plan. Child PID
events prove hook activation and forbidden-edge masking. The test-only fixture
supplies known cargo actions and ballast; it is not a cold-search benchmark and
is never a runtime planner start. `verification-worker.json` records the proof.
Current compatible cold planner pass rates and timings come from the detached
campaign's self-updating RESULTS.md; RESULTS-HISTORICAL.md is an earlier snapshot.
Measurements are development evidence, with baseline-violated and
baseline-obeyed rules distinguished. Missing cells remain explicitly unmeasured.

Initial adapter verification (historical snapshot): the prescribed pytest command passed **70 tests**, with zero
failures/errors/skips, including the original-instance callback guard, malformed
worker audit cleanup, late engine-exception incumbent recovery, and runtime
cache path restoration, seven aggregate hatch-allocation regressions, targeted CP construction for all
four types, captured-builder/unary alias restoration, duplicate-mask avoidance,
and qualified instruction-domain optimality attribution.
`verification.json`, `verification.xml` and
`verification.log` record the final suite and read-only engine/native manifest
verification. The suite itself waits for fleet admission.


The original benchmark was an autonomous resumable campaign. Exact recorded sessions/PIDs
and restart arguments are in `measurements/campaign.json`. During the code5
review all recorded processes were found absent; no stop/restart was issued,
and unfinished attempts remain unknown. No full-matrix completion is claimed.
The strict monitor validates compatible required groups and map coverage,
refreshes RESULTS, and writes
`measurements/campaign-complete.json` with a fresh manifest verification only
when the entire requested matrix has finalized. Until that artifact exists
with successful verification, task 0.5 remains measurement-incomplete. Streamed
incumbents and interrupted diagnostics do not satisfy final-row coverage.


## Explicit host confirmation (logic review v6 #17)

`confirm_brief(receipt, source)` is a host action, never a model tool. The source
must truthfully name the approval event or the explicitly labelled automated
batch policy. Its separate MAC prevents a reader-authenticated receipt from
being used as confirmation, and detects edits to the source or embedded reading.
`confirmation_source`, `confirmation_sha256` and the full confirmation record
are included in final evidence and checked-incumbent events. The original
`receipt_sha256` and reader inspection behavior remain unchanged. Confirmation
never resolves a refused/malformed voyage sentence; execution still stops with
`needs_confirmation`. Current-reading/generation freshness belongs to the host.

Required caller migration (outside this directory, deliberately not edited):

- `runner/core.py`: seal only after every voyage reading passed the existing
  approval gate and standing/unresolved questions are cleared, before
  `engine_call`. Preserve the actual callback/batch source. Empty-voyage runs
  need an explicit no-voyage-rules host policy, not an invented human approval.
- `runner/benchmark.py` and `runner/profile_study.py`: seal before invoking
  **any** executor, including injected executors, with a truthful development
  benchmark/synthetic batch source. A descriptive string is not a capability.
- `copilot/tools.py`: replace the stored raw receipt with the confirmed subclass
  inside host-only `confirm_reading`. Existing `_verify`/inspection still works.
  Forward that capability through default/custom replanners, detached jobs and
  ladder calls; never auto-confirm in `_execute_replan`.
- `sealed/worker.py`: retain its exact intended-reading comparison and pass a
  named source such as `sealed_expected_match` into the runner. Sealed execution
  inherits the runner gate; it gets no bypass or automatic human confirmation.

The runner currently uses fork and copilot uses threads, which preserve this
process-local capability. Native spawned engine workers receive compiled
constraints, not receipts. The historical reader checkpoint API transports only
a reading: exporting/loading a confirmed subclass deliberately returns an
unconfirmed `ReadBrief`; executing it fails loudly until a receiving host
explicitly confirms it. There is no automatic checkpoint confirmation upgrade.
The historical Frontier 0.9 `reader.public_brief` executor is unchanged; release
callers must use the binding API. No sealed data was opened for this change.

Confirmation follow-up verification: **51 focused tests passed**, covering the
reading-only-gate regression, exact source/read authentication, input binding,
checkpoint downgrade, old-call rejection, alias identity and callback metadata
isolation. `verification-confirmation.json`, `.xml` and `.log` record this
non-solver run and the unchanged engine/native manifests. The existing full
70-test verification predates this confirmation follow-up. Campaigns were not
paused or restarted; `plan_with_rules` was not changed.


## Code5 review fixes

All changes remain outside the engine and native sources.

| Finding | Correction | Regression evidence |
|---|---|---|
| 1 (P1) | Freeze both manifest digests and required maps; compare initial/final identities and verify before publishing incumbents. | `test_review_provenance.py`: truncated/refreshed native manifests, rewritten engine manifest, hidden native drift, changed release/publication identity. |
| 2 (P2) | Current measurement identity includes raw input bytes, resolved budgets/config, engine/native pins and loaded binding implementation. Cache reuse and coverage require exact compatibility for successes and failures. Finished rows are immutable historical evidence, with separate stale indexes. | `test_measurement_identity.py`, `test_measurement_resume.py`: changed map/config/source/pins/code and stale pass/failure rejection, input changing during load. |
| 3 (P2) | The hatch instruction wrapper preserves placement, replay, retained cargo and physical cover/POD checks; missing loads are classified by the original checker’s peelable T3 completion rule. | `test_partial_hatch.py`: a compliant occupied partial hatch releases only as a named T3 relaxation with ashore boxes and `passed=False`; mixed/illegal/retained violations still reject. |
| 4 (P2) | The direct confirmed-brief API blocks nonempty unsupported standing text before planning and asks for correction/removal/movement to voyage instructions. | `test_standing_gate.py`: hard/refused standing text cannot reach planning; blank text and supported preferences still work. |
| 5 (P2) | Each attempt owns a unique durable directory and fsynced append-only lifecycle. Graceful interruption records unknown before rethrow; resume preserves old progress, stack and incumbent evidence. Repeated recovery reports one original attempt. | `test_measurement_lifecycle.py`, `test_measurement_resume.py`: graceful/abrupt interruption, repeated resume, original evidence isolation and no denominator promotion. |
| 6 (P2) | Reusable native ballast workers are isolated per binding invocation. Pre-existing process/pipe handles are saved untouched; only exact adapter-created handles are retired before audit deletion, then previous handles are restored. A busy external slot is rejected. | `test_worker_lifetime.py`: actual reusable transport A/A → B → unbound, preserved external PID, normal/exception cleanup and audit deletion. |

Scoped child warm caches are discarded between instruction invocations; caches
of a pre-existing external worker survive. Historical timing rows cannot certify
this changed implementation. The two old hatch-compiler examples explicitly
called out by review are `6e254b6c54a41a7950a5068a` and
`b875b10ffab6160fb2493fc0`; their files/verdicts were not rewritten. Stale index
artifacts identify every excluded row and its original byte digest. No stored
finished row was found with the specific partial-hatch rejection pattern; this
is not a retrospective claim that rerunning a row would have the same outcome.

Code5 final verification: the full prescribed pytest command passed **150
tests**, including the actual reusable transport A/A → B → unbound and the
real spawned development witness/full checker. `verification-code5.json`,
`.xml` and `.log` record the complete suite, current frozen engine/native pins,
implementation identity, and SHA proof that all 30 historical row files remain
unchanged. That report predated the separately detached live campaign. Its old
30 rows and four unknown attempts remain historical; current coverage and
activity come from RESULTS.md and the live campaign status, not this snapshot.

## Logic3 policy, scope and evidence

`review3/api.py` keeps the fixed public signatures. The non-hatch path delegates
to the unchanged adapter and adds timing/refusal/reporting fields. The hatch
path (`review3/hatch.py`) builds private dependency namespaces around the
existing aggregate allocator and adapter body. It never patches their globals,
the pinned engine, or the checker. Each mapping remains a strict physical-panel
POD mask and is independently rechecked on the original instance. CP no-good
cuts enumerate at most three distinct mappings. Whole-hatch P/S pairing is a
preference, then assigned capacity is balanced near load-TEU demand ratios;
neither preference becomes a hard instruction. A full pass stops the portfolio;
a relaxed plan is held until the attempted mappings are exhausted. Failed
bounded search says "no plan on the N hatch mappings tried; this doesn't prove
none exists" and records `mappings_tried`.

Explicit extra/finalize budgets are shared across generated mappings. Native
small-map floors and additive assignment/peel/finishing work can still exceed
the requested time. Medium pages retain the pinned 80% extra-budget allocation
and cannot inspect the mapping; `page_budget_note` discloses this. Smaller hatch
trials cap `page_share` at .15. `first_checked_wall_s` and final wall time are
separate. Ladder proposal trials, rather than level-visit labels, identify
actual relaxed search; no-trial outcomes state that milder levels were not
searched and that a found candidate was classified.

`review3/measurement.py` separates immutable planning dependencies from hatch
allocation/reporting dependencies. Approved non-hatch/baseline rows retain
their validity; old hatch rows alone require the new portfolio policy. Input
bytes, exact rules, budgets/config, engine/native pins and actual planning
callables remain checked. Finished rows are never rewritten. The live campaign
is upgraded only outside active bindings, preserving existing function/PID
identities and all three worker slots; hatch catchup, recorded Reader paths and
validation occupy those slots. It creates no fourth measurement worker.

Tiny single-panel/multiple-POD hatch probes are labelled "not a test: section
has one hatch" and excluded from refusal denominators. `baseline_also_failed`
prevents attributing a failed baseline to a rule. Refusals distinguish
`proved_full_load`, `solver_timeout`, and `engine_identity`; only full-load
proofs request correction/reduced cargo and explicitly say no partial plan was
tried. `confirmation_text(receipt)` says **each hatch cover panel**. Host wiring
outside this package is specified in `review3/INTEGRATION.md`.

`review3/recorded.py` measures genuine archived Spark readings through
authenticated explicit confirmation and the current `execute_brief`. The
four-case source fixture pins raw responses, request IDs, original map/context
and source files. These are historical public development inputs, separately
reported from the fresh 32-map cohort. The hatch case transparently reuses the
identical recorded standing interpretation as a newly confirmed voyage rule.
No transferred context or synthetic oracle is labelled a real model reading.
Paired lift changes are search spread, not causal instruction cost or savings.

The live supervisor remains detached (98225, parent 1), but two existing worker
slots (98265 and 98267) stopped advancing after their final rows were durable.
Both logs end in an incomplete periodic traceback dump; the subsequent
`faulthandler.cancel_dump_traceback_later()` cancellation/join is the suspected
stall. The other worker (98266) still has native search in progress. This is a
diagnosis from the final-row/checkpoint boundary and partial stack artifacts,
not a native debugger proof: OS permissions deny sampling these owned PIDs.
One exact SIGINT attempt to the finalized hatch worker 98265 was also denied
with EPERM; no signal was delivered, no PID was stopped or replaced, and no
extra planning worker was launched. Consequently hatch catchup, the four real
recorded-reader executions and the full-suite work item remain queued in the
existing slots, rather than being reported as completed measurements.

`review3/watchdog.py` removes only future measurement calls' periodic dump/cancel
instrumentation through a private module view. Manual/fatal handlers, native
search and process-wide faulthandler remain unchanged. Four regression tests
cover return-to-checkpoint, preserved handlers and the explicit inability to
rescue a cancellation already blocked in C. The top-level discovery module
applies this at a later row boundary; it cannot release the two existing stalls.
The 70-test staged review-three suite passed before this instrumentation-only
addition; its four focused tests also pass. The complete binding suite is queued
for an existing worker slot, with output confined to binding.

## D261: independent 900-second wall

After the user stopped the old processes, `deadline/launch.py` preserved the ten
compatible rows and detached a replacement supervisor. Three independent row
sessions have parent-owned900-second timers that kill exactly their recorded
process groups. Rule selection, checker work and search all stay inside the
killable worker. Finalized and failed workers also have their groups cleaned up.
Fleet admission waits precede launches. The engine's90/30work controls remain
unchanged and are not presented as a wall limit. Missing medium cells run first;
the existing tiny/small cohort follows. Finished cells are never rerun.

Timeouts always report `deadline_exceeded`, separately with/without a checked
incumbent. A timeout passes only if the adapter already completed the original
full checker and every instruction audit before the deadline. Saved events must
match the exact rule and pinned engine; the cheapest eligible plan is retained.
First checked time includes worker startup. Shared-monotonic publication time
allows recovery of a predeadline incumbent read just after the cutoff. No timeout
proves impossibility. The ten legacy rows keep their original timing/provenance;
they are not retroactively labelled wall-bounded.

New row checkpoints live under the new output's `jobs/`; RESULTS self-updates
without engine work in the reporter. The medium remainder is30rows, approximately
2.5hours at the ceiling on three slots, plus admission waits. Focused verification:
24tests passed, including actual group/child termination, an independent0.05s
timer, timer cancellation, incumbent gates, and immutable-ten queue preservation.
`measurements/deadline-current.json` names the new run; its
`detachment-verification.json` records parent1 after the launcher helper exited.
