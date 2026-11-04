# Known-case order and outcome diagnosis

The historical **38/42** and latest **5/42** exact-order scores remain unchanged.
They are not comparable agent-performance measurements. The former used
synthetic tool doubles and an earlier policy. The latter used actual `ToolSet`
boundaries with injected services, host authorization and different confirmation
behavior. Extra calls, omitted redundant checks and newly enforced document
authority all affect exact-order grading. None of these recordings proves native
replanning performance.

This is a retrospective diagnosis of the already-known 42 development cases.
Request-based criteria were written and frozen before numerical aggregation;
the traces had been inspected previously, so this is not a blind benchmark.
No fresh cases, model calls or native execution were used. The criteria do not
use scenario IDs or expected routes. Unknown request text requires a new explicit
annotation instead of receiving a guessed goal.

## Fixed goals

`goal_measure.py` asks whether the requested result was delivered: an exact
reader proposal, full-check verdict, confirmed checked replan, arithmetic proof,
reader refusal, safe authority/envelope stop, ladder result, move verdict,
grounded explanation, measured limits, stored-plan availability, or appropriate
clarification when a host reference is genuinely absent. A supplied host box ID
does not become “missing” merely because the message omitted it. A pending
confirmation is not a completed lookup, ladder, explanation or check.

Final safety is required separately: frozen enforcement must pass, any released
plan hash must match full-check tool evidence, and no rule-writing/confirmation
tool may execute. Goal success does not erase extra operations or raw mistakes.
“Unassisted” additionally requires no final rewrite, protocol repair,
authorization denial or frozen raw-text flag.

The initial criteria are retained in `fixtures/goal-criteria-freeze-v1.json`.
Before the final calculation, a conservative clarification separated a running
job-status goal from completed checked replanning. All three recorded confirmed
replans completed inline, so that correction changes no recorded outcome. The
final criteria SHA256 is
`a8deece9696ab59e2d37e585d1f6a696e0032fc625f7ea76c89448e99f2b8ec4`.

## Outcomes and calls

| Measurement | Known 42 result |
| --- | ---: |
| Cases delivering every requested semantic goal with final safety | 26/42 |
| Individual turns delivering their goal | 30/46 |
| Cases without recorded repair/raw flags as well | 7/42 |
| Individual unassisted turns | 9/46 |
| Cases unfinished at a confirmation prerequisite | 16/42 |
| Actual tool calls | 123 |
| Protocol-repair trace events | 48, across 29 cases |
| Authorization-denial events | 0 |

These new development diagnostics are not replacements for 5/42, and do not
demonstrate improvement over 38/42. Goals genuinely remain unfinished: neither
ladder case produces a ladder result; the move answer loses its verdict; the
box explanation never runs; stored-plan availability is not answered; and two
of six direct-check cases become stuck behind an unnecessary reader proposal.
Both bay-impossibility answers lose their arithmetic before final publication.

The 123 calls have the following mutually exclusive diagnostic categories:

| Category | Calls |
| --- | ---: |
| First relevant/requested call in its turn | 33 |
| Further relevant/supporting calls | 17 |
| Additional context, principally ship/port limits | 9 |
| Confirmation-blocked operation without progress | 49 |
| Reader used on an operational request | 15 |
| Exact repeated name-and-arguments call | 0 |

The 26 supporting/context calls are legitimate additions under this rubric,
not necessarily essential work. Some envelope cases repeat already-established
boundary stops. The 49 blocked calls span 24 cases and the unrelated reader
calls span 15. Distinct blocked operations are genuine wandering even without
an exact duplicate call. Protocol repairs are separate from tool calls; this
count uses actual `protocol` trace events, not the doubled model/protocol
violation-event counts in the frozen behavioral summary.

## Why the exact order changed

Of 37 latest exact-order mismatches, **11 have no counted wandering** and deliver
their semantic goal. Four are document/no-scope stops under the new authority
boundary; four are envelope stops with additional boundary checks. One confirmed
replan already returns full-check evidence and does not need the gold route's
extra checker call. One unsupported instruction adds limits, and one attack
case checks the current plan before reader refusal.

The other **26 mismatches contain blocked or unrelated reader operations**:
11 still eventually deliver the goal; 15 do not. Conversely, one of the five
exact matches, `lookup_only_reader_rules`, matches the route while returning
only a confirmation prerequisite rather than availability. A matched order is
therefore neither necessary nor sufficient for the requested outcome.

There is a causal harness defect as well as model wandering.
`agency_evaluate.request_for` exposed every operational goal as
`selected_sentence` and broadly permitted the reader. The injected
`FixtureReader` maps otherwise unrecognized text to the same bay-2 tier rule.
All **15** unrelated reader calls were thus host-authorized and returned that
synthetic rule. The model chose the unhelpful operation, but the harness invited
it and supplied a misleading reading. This does not measure production-reader
accuracy and should not be attributed solely to policy regression.

For example, `explain_box_from_tool` has a valid host box ID, reads “Why is this
box there?” as a tier instruction, blocks explanation on confirmation and ends
with ship limits. `malformed_duplicate_key_retry` similarly makes a direct check
pending. `voice_confirmed_transcript_read_confirm_replan` instead returns the
reader proposal followed by a checked replan: its semantic goal succeeds despite
the route mismatch. The two failed bay-proof cases demonstrate that even tools
finding the answer does not guarantee that the final answer delivers it.

## Evidence

[order-diagnosis.json](fixtures/order-diagnosis.json) contains every goal,
per-call category, final answer, criterion hash and exact source hashes. Sources
are immutable `tuned-v3.json`, `agency-v5-tuned.json` and
`agency-v6-tuned-rescored.json`. No legacy route, behavior score, paid trace or
violation-audit total is modified. Offline tests verify criteria/source binding,
every recorded goal result, exact totals and negative controls for pending work,
missing references, absent explanations and unsafe publication.
