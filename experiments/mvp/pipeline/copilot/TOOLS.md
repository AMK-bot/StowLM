# Copilot tool contract

`Copilot(tools, client).turn(message, *, request=None) -> contract.schema.CopilotTurn`. The client
implements `complete(prompt)` returning text or an object with `.text`.
`live.LiveClient` adapts the existing managed Spark client. No native function
calling is assumed or probed: the existing completion endpoint is sufficient.

The dispatch boundary builds an immutable per-turn authorization from the planner's
own message or an explicit host `TurnRequest`. Documents, memos, notes, attachments, code blocks
and quoted content appear only as quoted data in the prompt. They grant no tool,
including the reader, and document arguments cannot widen a planner operation.
The guard rejects unauthorized actions before calling any tool. Prior turns and
host context identifiers confer no operational permission by themselves.

The convenience message grammar is conservative: affirmative operational clauses
may select known host references; negations, tool descriptions and document
inspection do not. Precise UI operations use explicit host grants:

An explicitly supplied `allowed_tools` set is a hard ceiling on inferred
permissions, including an explicitly empty set. Planner wording cannot add a
reader or any other operation outside that ceiling. Explicit argument-bound
`ActionGrant` events remain authoritative; omitting the scope uses the
conservative convenience grammar.

```python
from experiments.mvp.pipeline.copilot import ActionGrant, TurnRequest
sentence = 'Keep bay 2 clear.'  # Explicit planner selection, never model replacement.
request = TurnRequest(selected_sentence=sentence, actions=(
    ActionGrant('read_instruction', {'sentence': sentence}),
))
turn = copilot.turn('Read the selected instruction.', request=request)
```

The host constructs that request from a planner UI event. Model JSON cannot
construct or amend it. Reader arguments must match the original selected string
exactly; proposing a different bay or paraphrase is rejected. Empty-argument
operations also need permission, so a document cannot trigger a ladder/search.
A selected reading still needs independent host confirmation before execution.

For an investigative goal, the host may grant a scope rather than a sequence:

```python
request = TurnRequest(
    allowed_tools=frozenset({'check_plan', 'plan_lookup', 'replan', 'explain_box', 'list_limits'}),
    references={'reading_id': confirmed_reading_id, 'rules': confirmed_reader_envelopes,
                'box': selected_box_id},
)
turn = copilot.turn('Can I use this arrangement? Help me understand my options.', request=request)
```

Scope is host-owned and exists only for this turn. Fixed `references` support
`reading_id`, `job_id`, `box`, `slot` and reader `rules`; a reader scope also needs
an exact selected sentence. The model chooses order, questions and recovery from
misses/errors within the authorized set. Tool results may supply authenticated
follow-up references only for already permitted operations. Neither document
contents nor context IDs supply permission. `ActionGrant` can instead preset
several exact operations, with no required ordering.

One completion contains exactly one JSON object:

```json
{"tool":"read_instruction","arguments":{"sentence":"Keep bay 2 clear."}}
```

Or `{"say":"<the model's explanation or question>"}`. Prose need not copy tool
sentences. Extra action fields,
duplicate keys, nonfinite numbers, unknown tools, wrong argument types, markdown,
and multiple actions are rejected. The default cap is eight completions per turn,
including rejected actions. Tool/provider exceptions expose fixed safe messages.

The current tuned recording exercises actual `ToolSet` boundaries with injected
service fixtures. Independent software enforcement passes 46/46 returned
outputs; 24/46 raw prose samples have conservative flags. A corrected scorer
rescored the same actions without changing agent policy. Literal number forms,
expired evidence windows and 64-hex reading references limit interpretation of
those raw flags; they are not a general hallucination rate. The single frozen
fresh run completed with 15/15 enforcement passes and flags in 8/14 raw prose
samples; a progress turn has no prose. Both campaigns precede the runtime7
native receipt adapter fix, with no post-fix live 42/15 result claimed. Remaining
native recovery work is pending; see [RESULTS.md](RESULTS.md).
One native full-check recovery does not supply a valid lattice catalogue:
the saved baseline failed its separate full-crane gate on `iscode.loading.draft`.

| Tool | Arguments | Result and authority |
| --- | --- | --- |
| `read_instruction` | `sentence: str` | Splits the exact host-selected text on newlines, sends every row through `public_brief.read_brief`, and displays the complete draft under one opaque reading ID. No rule is applied. Compound refusal lookup uses every accepted proposed voyage row, including unconfirmed rows. |
| `check_plan` | none | Native publication requires the original-instance full checker, every crane frame and all confirmed instructions. A missing/failed gate or pending reading prevents acceptance. |
| `plan_lookup` | `rules: list[dict]` | Rules must exactly equal confirmed reader envelopes. Real lattice hits retain their complete freshly regated checker/rule/frame attestation, validated without repeating physics. A real miss starts background replanning only with a current host-confirmed reading and `lookup_fallback=True`. Injected lookup misses retain their offline behavior. |
| `replan` | `reading_id: str` | Only the current host-confirmed `ReadBrief` can submit detached background work to `binding.execute_brief`. Worker runs the full checker and instruction audit; initial progress does not accept a plan. |
| `wait_replan` | `job_id: str` | Polls an existing background job without waiting. Returns measured progress, a stale/error result, or the prepared completion after host freshness checks. Never runs the native checker on the polling path. |
| `try_ladder` | none | Submits binding's instruction-preserving ladder through the same background boundary with the confirmed receipt; an injected ladder has the same interface. |
| `try_move` | `box: str, slot: str` | Calls the real sibling `sandbox.try_move(instance, plan, box, slot)` by default. Slot is the existing seven-character ship address. A move verdict never installs a plan. The service remains injectable. |
| `explain_box` | `box: str` | Fully checks the current plan and confirmed instructions, then calls the real sibling `explain.explain_box(instance, plan, report, box)` by default. Import is lazy; the service can be injected. |
| `list_limits` | none | Returns synthetic ship, hull, port and envelope facts. Repeats a matching current instruction's refusal proof and labelled alternatives during negotiation. No limit can be waived. |

For `plan_lookup`, each `rules` item is exactly the reader result's nested
`proposed_reading.parsed` object, with `schema_version` and `constraint`. The
`proposed_reading` wrapper is not a rule. Wrapper fields such as `reading_id`,
`sentence`, `parsed`, source and confirmation metadata are rejected by the action
validator before tool dispatch. The fixed protocol retry hint says to copy only
`.parsed`; it never rewrites or supplies a rule. The actual tool then verifies
exact equality with the authenticated confirmed reader envelopes.

An authorized `list_limits` call can discover a measured limit, maximum or
threshold rather than inventing a value. The model may choose a useful question
when references or evidence are absent; an ordinary question does not require a
tool. A request for an unsupported number can be declined.

The registry deliberately excludes `confirm_reading`. The application calls
`tools.confirm_reading(reading_id)` after an explicit planner UI event. A message
such as “yes”, a document instruction, a model assertion, or a stale ID cannot
confirm a reading. A subsequent reading includes every confirmed sentence.
On this explicit host event, the default native path separately seals the
receipt with `binding.confirm_brief`, using source
`copilot_planner_confirmed:<reading_id>`. The default worker validates this
`ConfirmedBrief` and passes it to `binding.execute_brief`; the public reading ID
does not substitute for the capability. The capability participates in job
identity/freshness checks. Injected replanners keep their existing `ReadBrief`
receipt interface. This native-adapter-only fix leaves model prompt,
authorization and output safety unchanged.
The proposal exposes `rows` for all standing and voyage sentences, alongside
`reading_consistent`, `changed_rows` and `standing_pending`. Earlier confirmed
rows are compared by sentence, parsed rule, status/binding and parser identity.
Any drift blocks the entire new draft and its confirmation; earlier confirmed
rules remain in force. Review every row, not only the last sentence.

Nonempty standing text must be a valid standing preference. Changed/invalid
standing text blocks plan operations until a complete current reading is valid
and host-confirmed. A host edit increments its revision so an older receipt
cannot silently authorize new standing text. Reader refusals expose deterministic
`refusal_code` and `refusal_template`; outward structured `refusal.reason` values
also use templates. Empty standing rows are not active voyage refusals.

`tools.context()` exposes `active_job_id`, `pending_reading_id`,
`confirmed_reading_id`, `confirmed_rules`, the current reader proposal and the
envelope. These are host state, not permission for the model to create a rule or
confirm a reading. Box and slot identifiers must come from the user or supplied
host/tool selection; the copilot must ask if the required identifiers are absent.

Background progress has `status: "running"`, a `job_id`, execution `phase`,
`elapsed_s`, `elapsed_label: "measured"` and software-owned `text`. Elapsed time is
measured from submission, including queue time; it is not a projected completion
time. A running/queued/pending result from `replan`, `wait_replan` or `try_ladder`
ends the turn deterministically. The next user/UI event may poll once again.
There is no tight polling or sleep inside the copilot turn.

On completion `wait_replan` returns the prepared planning outcome, such as
`checked_plan`, `no_checked_plan` or `relaxed_plan`, plus `job_status: "completed"`
and measured elapsed time. Only a fresh full pass includes an accepted plan hash.
The worker already ran the checker and independent instruction audit; polling
does not repeat that work. Check/lookup/move/explain requests during active work
return its status rather than entering native work concurrently. Synchronous
native calls use the same nonblocking guard. Legacy scenario doubles may return
synchronous results and remain explicitly labelled doubles.

`jobs.JobManager` is host infrastructure, not another model tool registry.
`start(operation, *, identity, context, snapshot=...)` queues detached work;
`poll(job_id, *, identity=...)` returns immediately. `result` exposes a completed
native payload only to the host. The host compares state before publication.
Changing the instance, plan, confirmed/pending reading or generation makes the
old result stale. `cancel` revokes publication without interrupting an executing
native call. Jobs are memory-only daemon work serialized within this process;
there is no durable restart or cross-process coordination guarantee.

All model-facing tools check the envelope before reader/engine work. Outside the
envelope they return its measured reasons and invoke no downstream service.
The runner's gate is preferred; the local fallback fails closed on unknown ship
shape/capacity and supports the same design limits.

Results are JSON objects with `status`, software-owned `text`, and `passed`.
A full success also has `checker: {passed: true, screen: "full"}`,
`instruction_recheck`, and a canonical `plan_sha256`. Check and reader results retain their evidence. Background publications retain a
compact checker/instruction verdict, sourced lifts and `native_evidence_sha256`;
the host retains only the latest native plan/report. `result_sha256` hashes the
returned evidence. Scrubbed evidence is rehashed. `wall_s` is measured locally.

The final software gate releases a plan hash only from current full-pass tool
evidence. Results carry software-owned `planning_request_id` and `planning_generation`.
Every new planning request revokes earlier acceptance, including inline failure,
relaxed results and errors. Late older/unmatched certificates cannot restore
success text, hashes or numeric proof. A failed native replan also blocks a later
check from republishing its retained seed plan, across turns; a genuinely checked
replacement is required. Reader proposal lifetime is tracked separately: only a
later reader request supersedes a proposal. Unchecked hashes are stripped from nested results as well as the
top level. Numeric tokens must occur in the current request window's tool
results; previous turns, superseded requests and user text are insufficient.
The model may write its own prose and questions. Positive accepted/approved,
safe/legal/checked claims require matching current full-check evidence; ordinary
questions and negative cautions are permitted. This is a conservative language
guard, not a semantic verifier for every nonnumeric statement. Invalid numbers,
acceptance or hashes use a software evidence template. Static impossibility
explanations retain mandatory arithmetic facts and active refusal templates.

`Copilot.last_trace` retains each original model response in `model_raw`, including
invalid/denied actions. Its separate `final_gate` entry records `raw_say`,
`candidate_say`, `final_say`, `rewritten`, diagnostic reasons and termination.
`last_raw_say` and `last_gate_report` expose the same per-turn distinction.
Software-only termination has no raw model `say`. Independent behavior scoring
uses original model decisions, never treats repaired prose as model success, and
marks incomplete/provider-unavailable turns not applicable.

Negotiation never applies an alternative. Spark may compare and rank several
tool-supplied checked options in its own words, citing their IDs and explaining
costs or restrictions. A nested full checker plus instruction audit can support
“these alternatives passed” while the top-level accepted-plan hash stays absent.
Approving/applying a selected option still requires its new confirmed reading
and matching current plan check. Unchecked/historical offers retain a software
check label and requirement for planner choice. A screen means only “not proved impossible”.
Historical witnesses are labelled historical, and lift costs are stated only
when present in checked tool evidence. `ToolSet(..., refusal_witness=path)` can
reuse the reader's existing historical-witness candidate selection. With the
default or an injected lattice, each proof-owned hypothesis can obtain a fresh native checker
and independent instruction audit; successful alternatives carry their measured
lift costs, with a `cost_reference` to checker evidence. These checks never install a plan, change confirmed rules or release
an accepted top-level plan hash. The planner must choose, reread and confirm a
changed instruction before applying it. Never invent alternatives to fill a quota.
`list_limits` repeats the matching proof so a generic limits response cannot hide
the instruction's arithmetic impossibility. When no alternative has a full pass,
it explicitly says that none has been fully checked and no replacement plan is
accepted. Zero checked alternatives is a valid outcome: stop at the sourced
arithmetic and facts rather than claiming a candidate works or inventing lifts.
The curated `recorded_refusal_card` host helper reuses the existing proof-bound
`refusal_card.py` loader; it never installs a current plan.

Hero playback supplies host-only dependencies, never JSON arguments chosen by the model:

```python
tools = ToolSet(instance, parser=reader, plan=base_plan,
    lattice_store_dir=pinned_store,
    catalogue_alternatives=[
        {'id': 'dg-away', 'sentences': [original_catalogue_reader_sentence],
         'canonical_rules': [original_catalogue_reader_envelope]},
    ],
    refusal_lookup=host_refusal_lookup,  # Optional; default is real lattice.refusal_lookup.
    release_gate=host_release_gate,      # Optional; default is real lattice.gate_plan.
    lookup_fallback=True)
```

The release host pins the catalogue independently and warms each real lookup.
`lattice.lookup` reuses its full gate only while
entry bytes, engine identity and physics epoch remain unchanged. Cold lookups
can require expensive admitted regating. `refusal_lookup` itself reruns its
necessary-condition proof and admission; a host memo must retain the actual
proof and exact instance/rule identity. A `release_gate(instance, plan, rules)`
cache must likewise pin exact instance, plan and canonical-rule identities and
retain the original full checker and complete crane-frame evidence. Returning
a successful departure report alone never qualifies. Rejected gates expose the
actual outside-limit steps and release no accepted plan hash.

The recorded hero also supports authenticated cross-process handoff of exact
same-campaign gate receipts. It verifies current source/engine/physics identity,
instance/plan/rule binding, clean full-check reports and complete frame evidence
before reuse. Reused tool results say `mode: stored`, `reused_recorded: true`
and `freshly_rechecked: false`; identity validation is not a new numerical run.
Offline playback instead says `mode: recorded` and performs no checker call.

The catalogue option list contains trusted saved reader captions and envelopes,
not new copilot-created rules. Offers can change instruction family or contain
several reader sentences. They replace the unconfirmed draft while preserving
earlier confirmed instructions. Each offer must independently obtain its actual
fully gated catalogue plan; otherwise it stays unchecked with no lift cost.
Even a checked offer never binds rules or installs a plan: application requires
resubmitting its original sentence(s) through the reader and host confirmation.
Checked lift comparisons include explicit arithmetic differences so Spark can
rank options using current-turn measured costs without inventing numbers.

Voice uses `CopilotVoice` over `voice.flow.VoiceFlow`: prepare audio/typed draft,
confirm or edit the transcript, read it, then independently confirm the reading.
Neither preparation nor transcript confirmation authorizes replanning.

Rejected terminal payloads are discarded and clear active state, allowing a new
request. Consumed native payloads are forgotten immediately. `ToolSet` caches at
most `job_history_limit=16` compact records by default; `JobManager` bounds
terminal records with `history_limit=16`. Both limits are configurable positive
integers. Recent repeat polls are idempotent; evicted IDs are unavailable.
Queued cancellation releases captured inputs. Executing native work is never
force-stopped or forgotten until it finishes.
