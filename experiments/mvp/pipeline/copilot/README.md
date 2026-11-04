# Planner copilot

Spark selects validated JSON actions, investigates tool evidence, writes its own
prose and asks ordinary questions. The reader alone supplies rules; the planner
confirms every row, through `binding.confirm_brief`, before execution. Software
gates quantities, acceptance and plan references. Documents grant no authority.
Synthetic ships and cargo, simulated port; the hero is an 87-box half-ship section.

[RESULTS.md](RESULTS.md) separates historical tuned/fresh scores, the failed hero
attempts, and the final post-fix development variants. The historical 38/42 and
5/42 exact-route scores remain visible alongside the 26/42 semantic goal audit;
they used different harnesses and are not a matched policy comparison.
[VIOLATIONS.md](VIOLATIONS.md) gives the small raw-text violation table.

The real lattice supplies a baseline, proved refusal, DG clearance and bay-closure
plans from a host-pinned catalogue projection. Native preparation checked their
full original-instance reports, reader rules and all crane frames. Authenticated
handoff reuses those exact campaign receipts only while current source, engine,
physics epoch, report binding and rule audits agree. It is labelled recorded
reuse, not a new numerical check for every turn. Missing keys use the real
lattice and detached binding jobs, with actual elapsed seconds.

The original `hero-final/` attempt had a loaded-code freeze defect. The
`hero-verified/` continuation exposed a separate import side effect: importing
the runner envelope replaced the caller's vessel-cache environment. Receipts
correctly failed closed. The adapter now preserves that environment; regression
tests exercise the first actual tool call. Both failed attempts and all charges
remain available. [HERO-ATTEMPT-AUDIT.md](HERO-ATTEMPT-AUDIT.md) and the diagnostic
fixture preserve their evidence.

`hero_postfix.py` runs newly frozen development variants once, after that fix,
and records a separate shot 10 take. Its inputs were frozen before the run,
with unchanged semantic success criteria; they are not a blinded new-domain
benchmark. The reader label is **unadapted cloud Spark**, not the adopted local
fine-tune. Exact validated reader-response reuse is marked, and host
confirmation remains a separate recorded action.

`hero_replay.replay_shot10()` reads the hash-pinned conversation and facts from
`fixtures/hero-shot10-delivered/`; it constructs no client and runs no checker. Facts
separate available native evidence from observed conversation acceptance. The
p22 manifest and audit live in `fixtures/hero-shot10-delivered-report/`, with
`playback-manifest.json` beside the conversation for the default replay. After
two scripted planner clarifications, Spark delivers its own ranking of checked
70.0/82.0 weighted-lift cards with a sourced 12.0 difference. The planner chooses
DG clearance, the real Spark reader shows its rows, the host confirms them, and
the stored checked plan is returned. These weighted nonproductive lifts include
projected later-port lifts. Earlier withheld rankings remain recorded. This is
a separate demonstration after software repairs, not a new benchmark score.
Playback does not install a plan. Paid calls are closed: **$9.12395475 measured +
$1.319936 retained unknown-billing hold = $10.44389075 / $12**; replay costs nothing.

Real `plan_lookup` and `refusal_lookup` now default to the sibling lattice APIs.
Stored candidates and negotiation choices require the full original-instance
checker, confirmed-rule audit and every crane-frame gate. A confirmed lookup
miss starts a detached binding job and returns measured elapsed seconds; later
status turns do not wait for native search. Native cache receipts reused within
the recording are labelled as preparation checks rather than new per-turn runs.

Spark chooses one validated JSON action at a time, investigates results and writes
its own explanations and questions. The reader supplies rules, the planner
confirms the complete reading, and software checks every accepted plan.
Synthetic ships and cargo, simulated ports. This is a forward pilot inside the
pipeline envelope, not an operational or certified ship-planning product.

From the repository root:

```sh
UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run --offline --no-sync pytest experiments/mvp/pipeline/copilot
UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab uv run --offline --no-sync python -m experiments.mvp.pipeline.copilot.live
```

The second command reads the recorded score report and makes no paid call.
`replay.replay_conversation('plain' | 'negotiation' | 'attack')` returns the three
saved conversations with explicit recorded labels and no network/model/GPU.
Replay returns evidence; it does not install a plan or turn a historical check
into a current check.

`replay.replay_real_conversation(name)` plays the additional real-tool recordings.
These use the real reader schema/receipt path with
an explicitly labelled unadapted cloud Spark reader. The supplementary recordings
exercise the actual reader and arithmetic-proof tools only; no plan is accepted.
The earlier native checker/binding attempts were interrupted and their evidence is saved
separately. They do not claim to use the final fine-tuned reader.
`python -m experiments.mvp.pipeline.copilot.real_demo`
prints their measured results without a paid call. Its `--live` path records only
missing cases, sharing the same task-wide cap; `--read-only` selects the reader
and proof paths used by the saved conversations. On small maps the engine retains
historical search/finishing floors; its requested short budget parameters are
not total wall-time caps.

```python
from experiments.mvp.pipeline.copilot import Copilot, ActionGrant, TurnRequest
from experiments.mvp.pipeline.copilot.tools import ToolSet

tools = ToolSet(instance, parser=reader, plan=current_plan)
copilot = Copilot(tools, client)  # client.complete(prompt) -> str or .text
sentence = 'Keep bay 2 clear.'
draft = copilot.turn('Read the selected instruction.', request=TurnRequest(
    selected_sentence=sentence,
    actions=(ActionGrant('read_instruction', {'sentence': sentence}),),
))
# The planner reviews ALL proposed rows before an explicit confirmation UI event:
tools.confirm_reading(draft.proposed_reading['reading_id'])
# A separate host UI event grants investigation scope and fixes its references.
request = TurnRequest(
    allowed_tools=frozenset({'check_plan', 'plan_lookup', 'replan', 'explain_box', 'list_limits'}),
    references={'reading_id': tools.context()['confirmed_reading_id'],
                'rules': tools.context()['confirmed_rules'], 'box': selected_box_id},
)
result = copilot.turn('Can I use this arrangement? Help me understand my options.', request=request)
payload = result.model_dump(mode='json')
# Native replanning runs in the background. On a later planner/status event:
if tools.context()['active_job_id'] is not None:
    progress = copilot.turn('What is the status of the active replan?')
```

`ToolSet` accepts a validated instance or its JSON dictionary. An unavailable
reader fails closed; no fallback model or hand-written language parser is used.
Inject the final on-device reader's `parse(sentence, context)` interface.
`CopilotVoice` is in `voice.py`. Full tool schemas, confirmation handling and
safety invariants are in [TOOLS.md](TOOLS.md).

A host scope permits tool choices and follow-ups, without prescribing their order.
`ActionGrant` remains available for exact operations. Documents, memos, notes and
quoted text cannot grant tools or change fixed references. Ordinary questions do
not need a tool call, and incomplete context should lead to a useful question.

The explicit host `confirm_reading` event also seals a separate
`binding.confirm_brief` capability for the default native worker. The worker
requires that `ConfirmedBrief`; a reading ID alone is insufficient. Injected
replanners retain their existing `ReadBrief` interface. This runtime7 adapter fix
landed after both current model campaigns; prompt, authorization and safety
policy did not change, and no post-fix live 42/15 score is claimed.

A stored lookup copies only each reader result's `proposed_reading.parsed`
envelope into `rules`, including `schema_version` and `constraint`. Passing the
surrounding proposal or its reading metadata is rejected before dispatch with a
fixed correction hint. A requested measurement can be investigated with an
authorized `list_limits` call; the model cannot invent the missing value.

Default `replan` and `try_ladder` submit detached work to `jobs.JobManager` and
return a job ID. `wait_replan(job_id)` polls once without waiting for the native
call. Progress reports `elapsed_s` with `elapsed_label: measured`; it measures
wall time since submission, including queue time. A running result ends the
copilot turn. A later user or UI event may request status again; there is no
polling loop or invented completion estimate.

The worker runs binding, the full checker and the confirmed-instruction audit.
Completion publication compares the instance, plan and reading identity, then
installs only a fresh checked result. Changed input makes a job stale and keeps
its result from publication. Cancelling discards publication; it does not kill
an executing native call. The in-memory daemon queue serializes this folder's
native work within one process. It does not coordinate unrelated processes.
Polls expose progress and prepared results; they do not rerun native checking.

`scenarios.run_scenarios()` retains scripted control-flow tests and historical
exact-sequence measurements. They do not measure agency or engine success.
`behavior_scores.score_trace` independently scores raw model text and attempted
actions before the final software gate, using evidence chronology. Provider
incomplete/unavailable turns are not applicable, rather than safe successes.
Software enforcement and Spark's own raw behavior are reported separately.
The tuned run observed multiple tools in 34/46 turns and left model prose
unchanged in 14/46. Its legacy exact-sequence score is 5/42; extra investigative
calls count as mismatches, and no route-score improvement is claimed. Raw flags
include literal number forms, expired evidence windows and 64-hex reading-ID
classification. Full evidence and the $2.33421220 campaign cost are in
[RESULTS.md](RESULTS.md). The fresh run cost $0.66731095, used multiple tools in
8/15 turns, left model prose unchanged in 4 turns and matched 5/15 legacy exact
sequences. These model recordings precede the native receipt adapter fix.
The injected tool tests cover authenticated receipts, checks, instruction audits,
arithmetic refusals, envelope gating, confirmation and recovery permissions.

Free prose is allowed; the final gate checks current-request numeric provenance,
acceptance claims and plan hashes. It preserves mandatory impossibility facts and
trusted refusal templates. It does not certify every nonnumeric factual sentence.
`last_trace` preserves original `model_raw` outputs and a separate `final_gate`
record. `last_raw_say` is absent for software-only termination, such as a running
job. `last_gate_report` records raw/final prose, repairs and termination reason.
A repaired answer is software enforcement evidence, not safe model behavior.

Paid recordings use `src/llm/spark/client.py`, its shared ledger, zero retries,
bounded output, and `allocation_budget` with an eight-dollar cap. The runner
reads existing recordings by default; `--live` is the explicit paid path, and an
existing recording is never silently regenerated. The managed client maintains
shared accounting metadata; this folder does not manually edit shared docs.

Known limits and stubs:

- Lattice lookup, sandbox movement and box explanation now default to the real
  sibling APIs through lazy imports. Services remain injectable; the named
  `lattice_unavailable`, `sandbox_unavailable` and `explainer_unavailable` stubs
  are explicit test/cut options. Missing stored candidates or unavailable work
  remain honest failures. A lattice hit is freshly checked, and a sandbox
  verdict never installs a plan.
- `explain_box` imports the real sibling explainer only after
  the current plan passes the full checker and confirmed-instruction audit.
- Binding has landed: background replan and ladder work call
  `binding.execute_brief`. The scenario campaign uses synchronous synthetic
  doubles and measures orchestration separately. A submitted job or elapsed-time
  reading is not evidence that a native plan has completed successfully.
- The final fine-tuned reader is a host-injected dependency. No GPU is loaded.
- New readings expose every standing and voyage row. If an earlier confirmed
  reading or its parser identity drifts, the whole new draft is blocked; the old
  confirmed rules remain in force. Invalid or changed standing text also blocks
  plan operations until a complete valid reading is explicitly confirmed.
- Reader refusal reasons are software templates, including structured outward
  reasons; free provider refusal text is not shown as a trusted reason.
- The existing bay-closure proof leaves no supported same-family alternative.
  We report that honestly. There is no claim that two checked bay alternatives
  or their lift costs have been produced. The tier example offers only the
  alternatives supported by the reader proof, with their check status. With a
  host-injected lattice, proof-owned alternatives receive fresh checker and
  instruction audits and sourced lift costs; no plan or rule is applied until
  the planner chooses, rereads and confirms it.
- `list_limits` reuses the current instruction's arithmetic proof and alternatives
  when that evidence still matches the instance and reading. Without a full
  check, alternatives remain screen-only or historical hypotheses. If none is
  checked, it says so; negotiation can stop at the arithmetic proof and limits
  without inventing a replacement plan or lift cost.
- These are development/control-flow scenarios, not a sealed accuracy measure.
- Four native recovery cases completed: recovery evidence was observed in 2/4,
  while strict goal scores are 0/4. A separate real explanation produced tool
  evidence but no completed model answer, and is scored N/A. The final native
  binding ablation completed without a passing replacement; replan returned in
  0.568 seconds and status turns remained responsive. It used a scripted model.
  The saved baseline's separate full-crane lattice gate
  failed on `iscode.loading.draft`, so it supplies no valid native catalogue or
  checked native alternatives. A complete hero demonstration remains open.

Final verification: **663 offline tests passed**. Cumulative canonical spend is
**$5.48637435**, with **$1.319936** retained for unknown billing, totaling
**$6.80631035**, below the earlier $8 cap. The new $12 cap remains gated on the
leader's hero announcement. All recorded runs replay without paid calls.

Measured counts, live failures and cost are recorded in [RESULTS.md](RESULTS.md)
and the historical [tuned recording](fixtures/tuned-v3.json), [fresh recording](fixtures/fresh-v3.json),
plus the retained [original live evidence](fixtures/live-scenarios.json).
That historical fresh cohort was frozen before its evaluation and scored once.
`python -m experiments.mvp.pipeline.copilot.evaluate tuned` and `evaluate fresh`
replay existing recordings; an absent recording authorizes paid work only under
the task cap. A fresh-start marker prevents a second campaign across revisions.
The fresh background case uses an explicitly synthetic clock; real elapsed-time
measurement is verified separately by job tests.

`fixtures/plain.json`, `negotiation.json` and `attack.json` now hold final-policy
recordings. Earlier versions are preserved with a `-v1` suffix;
`negotiation-bay.json` also records the arithmetic-only stop with limits.

For precise planner UI operations, pass a host `TurnRequest` containing explicit
`ActionGrant` values and the original selected sentence. Untrusted documents are
quoted data and never grant operations. The convenience `turn(message)` grammar
fails closed when an action/reference is unclear; it does not guess reader text.
See [TOOLS.md](TOOLS.md) for the API.

The offline scripted suite now uses `fixtures/review-scenarios.json`: explicit
synthetic host UI setup, with zero-tool expectations for document attacks.
The original 42 and fresh 15 fixtures and paid traces remain unchanged.
`python -m experiments.mvp.pipeline.copilot.review_revalidate` independently
rescores saved outputs and replays old raw actions under the new guards. It
keeps those two measurements separate, reports unused actions, and makes no new
model decisions. Replaying an old route is not a new model routing score.
