# Box explanations, English and Thai — v3

Synthetic ships and cargo only. This feature shows checked placement facts and
sampled move evidence. It does not choose a slot, search for a replacement plan,
or prove that the selected position is necessary or optimal.

```python
from experiments.mvp.pipeline import explain

answer = explain.explain_box(instance, plan, report, box_id, language='th')
move = explain.explain_box(instance, plan, report, box_id,
                           what_if={'target_slot': '0020384'})
# In a trusted worker that already called sandbox.try_move on these exact inputs:
move = explain.explain_move(instance, plan, report, box_id, target_slot,
                            actual_move_verdict, language='en')
```

Inputs accept the existing stowage models or JSON dictionaries. Outputs use the
shared contract's `Explanation`. Unknown boxes, boxes absent from departure,
unbound checker reports, tainted inputs and unsupported languages are refused.
`en` and `th` are supported; Thai voice input is outside scope.

## Spark's writing job and honest labels

Spark writes original framing for a two-sentence summary above the fact list,
in English and Thai. Each sentence contains an indivisible fact clause selected
from a code-generated menu. The model supplies connecting prose around that
clause; code inserts every value together with its label. Framing cannot supply
numbers, number words, box IDs, rule IDs, LOCODEs or acronyms. The validator also
rejects sentence boundaries and specified unsupported necessity, safety and
optimality claims. Thai must use the same fact clauses, in the same order, as
the admitted English summary.

A protected-token inventory is an additional gate. It is insufficient on its
own: numeric roles are bound by the inserted clauses and exact code-rendered
fact sentences. A measurement/limit swap fails even if all tokens remain.
The model's language feedback cannot authorize a changed numeric role.

Only an accepted, actually generated summary carries **Recorded Spark summary**
and `mode='recorded'`. A missing client or rejected summary displays **Template
placement facts (no model summary)** with `mode='live'`; this mode describes the
local rendering, not a model call. Templates are never labelled recorded model
answers. Prefetch may contain genuine model summaries for selected boxes and
local templates for the remaining boxes; each answer keeps its own label.
`explain_box` never creates a network client.

## Facts and evidence

Every statement has an ID and content evidence: source, SHA-256, JSON pointer,
original value and `href`, such as `report:<hash>#/passed`. Evidence addresses the
supplied artifacts, replayed departure state, or an embedded actual sandbox
verdict. These links do not imply external approval.

The fact list includes:

- Box type and length, gross mass, DG class and UN number when present, reefer
  status, empty status, position and POD.
- Stack neighbours and support gaps; support-column mass and declared limit.
  Spanning boxes share mass equally between supports, following the checker.
- Weight rank in its stack and support-bay group, hold/deck position against
  that group's mean, and contiguous POD block. These comparisons do not claim
  a causal reason that the planner selected this slot.
- For reefers, free length-compatible powered physical cells. Empty cells alone
  do not establish supported, legal slots.
- For DG boxes, length-compatible footprints allowed by declared Document of
  Compliance permissions and geometry. This count does not screen occupancy,
  support, stowage provisions or segregation. The named segregation rules
  `imdg.segregation.7_2_4` and `imdg.segregation.7_4_3` are reported as a
  plan-scope pass only when the supplied rules report establishes that result.
  A numeric box-specific distance is never inferred from a plan-scope pass.
- A **projection** of restowed boxes, restow lifts and hatch-cover lifts at the
  box's POD, plus earlier-port boxes below and later-port boxes above. The
  replay projection must match the report's future counts. Whole-plan counts
  include hatch and indirect blockers; they are not attributed solely to this
  box. The projection fixes departure stow, assumes no future loads and does
  not construct an executable future policy. An overstow is not presented as
  a neutral POD-order observation without that qualification.

The `reefer` flag means a powered-reefer requirement. An unplugged reefer can
retain its reefer ISO type; the Thai wording states the power requirement and
does not reclassify the box. Written numbers and Unicode numeric characters
are also prohibited in model framing; their values belong in code slots.

## Sampled alternatives and actual moves

For selected demo boxes, prefetch screens up to six actual same-size compatible
slots, spread deterministically across the ship geometry. The sample does not
filter out occupied, unsupported, unplugged or otherwise failing positions.
`sandbox.fast_screen` supplies the evidence. Counts and rule groupings are
labelled **Fast screen**, including unknown or unresolved outcomes. A surviving
screen is not a full-checker pass or proof that a slot is free to use. Later
lift changes are stated only when reported, rather than inferred from survival.

`what_if` reaches the real `sandbox.try_move`. The answer quotes actual rule
IDs, measurements, limits and `extra_lifts` **beside**
`projected_later_delta`, explicitly marked projection. Missing lift counts stay
unavailable. The edit retains crane order and ballast and does not replan;
a refusal therefore does not establish that no alternative arrangement works.
Invalid requests are explicitly not checked. No Spark credit is used.

`explain_move` lets a trusted worker reuse the real verdict it just computed,
without another full check. The adapter is not an authentication boundary for
arbitrary caller-supplied verdicts. `integration.patch` contains the reviewable
app changes: visible “Explain this move”, target-slot router/worker plumbing,
verdict reuse, bilingual target retention and summary display above facts.
**The patch awaits the app owner. User-facing app wiring is not claimed here:**
this task's file fence forbids edits to `app/`.
App promotion must align the renderer/before-plan, demo `source_fixture`, config
`sandbox_source`, baseline report/gauges and prepared drag cases with this exact
hero binding. The shared source selector prefers an existing demo
`source_fixture`; changing only config `sandbox_source` can retain the old plan.

## Named hero, recording and replay

`hero.load_hero()` considers only the explicitly published lattice hero
manifest, default `experiments/mvp/pipeline/lattice/hero.json`. It never picks an
incumbent, screening candidate, test fixture or arbitrary plan. It checks the
named source instance bytes and normalized content, catalogue-authenticated
entry, exact plan/report bindings, completed every-frame gates, current engine
and runtime identities, and producer `0.13.0` or labelled `lattice.sequence`.
This validates recorded gates against exact artifacts; it is not a fresh full
checker execution. A release must independently pin the lattice catalogue.

The current named hero is published: a synthetic 87-box feeder_500 section with
144 recorded crane frames, produced by labelled `lattice.sequence` repair after
Frontier Engine. Strict source, catalogue, input, report, frame and current
runtime checks passed; `fixtures/readiness.json` records **ready**. This is
recorded-gate validation, not a new checker execution. Hero prefetch completed
87 bilingual fact lists (174 payloads), ten admitted English model summaries
and 60 recorded fast screens across the ten selected demo boxes. The selected
model coverage is a budget decision: all 87 boxes have facts, while model
framing and six-cell comparisons focus on ten boxes. Thai template/summary
normalization and the final language refresh are complete: the ten selected
boxes also have ten admitted Thai model summaries. All ten selected Thai
sense checks remain **flagged** for fact-list issues; the flags are retained
and none targets a displayed summary. The old
13-box Proposer fixture
is retired to `fixtures/development/`; it remains development regression and
language-QA material, never a substitute hero. Its historical payloads and
feedback remain under `fixtures/development/retired-cache/`.

The default hero replay directory is **`fixtures/hero/cache/`**, shared by the
API and CLI. The CLI selects about ten demo/model boxes for cargo, story, POD
and mass variety before seeing any alternative-screen outcomes. Every departure
box gets bilingual facts; selected boxes additionally get model summaries when
`--spark` is explicitly supplied and six-cell screens after CPU admission.

From the repository root:

```sh
export UV_CACHE_DIR=$PWD/.uv-cache
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
uv run --offline --no-sync python -m experiments.mvp.pipeline.explain.run_prefetch --estimate
# Optional wait for that exact named hero; no search or detached watcher starts:
uv run --offline --no-sync python -m experiments.mvp.pipeline.explain.run_prefetch --estimate --wait-seconds 30
# After publication, with Spark credentials and credit readiness in the environment:
uv run --offline --no-sync python -m experiments.mvp.pipeline.explain.run_prefetch --spark
uv run --offline --no-sync python -m pytest experiments/mvp/pipeline/explain -q
```

A missing hero exits with waiting status before screens or model calls; an
invalid or changed binding is withheld. Estimates are printed and written before
spending. `--estimate` performs no screening or model call. Without `--spark`,
prefetch uses templates. Poll sleeps are bounded and the preparation PID is
recorded. No process starts automatically.

Instance, plan and report hashes bind each cache. Payloads and index have
integrity hashes. Replay rederives current facts, numeric roles, evidence
pointers, code clauses, summary slots and display text; it does not trust a
matching token set or a self-consistent edited hash. Stored alternative evidence
also validates its input/source bindings and rederives its grouping. Valid
complete bilingual entries can be reused without new screens or paid calls.
A missing cache returns `None`; stale or corrupted caches raise `ValueError`.
The public `explain_box` can fall back to freshly rendered, honestly labelled
local facts. Replay requires no model or network and does not rerun the checker.

The **$5 total task allocation** includes historical recordings, subsequent
language QA and eventual hero recording. `budget.task_client` seeds its durable
accounting from retired spend and enforces one allocation across directories;
the per-cache guard is additional. Each request reserves before dispatch,
settles measured cost and retains unknown billing as a conservative hold.
Retries are disabled. Reconcile settlements with the shared Spark ledger before
reporting measured spend. A cost projection is not measured provider usage.
Current summary and Thai sense requests each allow up to 4,096 output tokens;
estimates include those bounds. Truncated or invalid output is retained as
billed evidence and cannot become an admitted model summary.

<!-- V3_MEASUREMENTS_START -->
The completed hero prefetch cost **$0.23913425 measured**. Its stage wall time
was **1,426.2135 seconds measured**, including fleet admission waits and network
calls. This is recording time, not click-response latency.

Final [hero verification](fixtures/hero/verification.json) covers **87 boxes,
174 bilingual payloads, ten English and ten Thai model summaries, and 60 fast
screens**. It freshly rederived the 20 selected bilingual answers, their
facts/evidence/summary slots and recorded alternative groupings. Across all
payloads it checked integrity, input bindings, bilingual roles/evidence and
1,313 protected statement pairs. Nonselected facts were not individually
rederived from inputs by this verification. No checker or fast screen was rerun.

**All ten selected Thai sense checks are flagged.** Current concerns include
dropped earlier/later POD distinctions, reefer status rendered as plug need,
untranslated “unavailable”/“Fast screen”, added deterministic-sampling wording,
and unclear weight/background-cargo wording. No current issue targets a
displayed model summary. These fact-list flags remain visible alongside 37
prior language-check records and nine archived flag records. Development QA
separately records six passed and seven flagged answers; it is not hero coverage.

Cumulative task spend is **$1.68955915 measured of $5**, including historical
recordings, development QA, hero recording and language follow-ups. All **214
paid request IDs match 214 shared-ledger settlements**, with **zero billing
holds**. Model language feedback remains a check, not proof. **No native Thai
reader has reviewed these explanations.** App integration remains outstanding.

Final offline folder suite: **188 tests passed**, including published-hero
replay, numeric-role swap regressions and the nine app-patch checks. The patch
was checked against the current app without applying it.
<!-- V3_MEASUREMENTS_END -->

## What was checked in Thai

Code binds each numeric value to its own role in both languages. Deterministic
checks preserve numbers, box IDs, rule IDs, LOCODEs, fact clauses and paired
summary slots. Tests include swapped measurement/limit prose, changed cache
payloads, stale input bindings, missing gates and invalid model summaries.

A separate Spark call reads the English/Thai facts and admitted summary for
sense, fidelity, unclear or unnatural wording. Results, issue IDs and reasons
are retained as **model feedback, not proof of translation accuracy or safety**.
Flagged or unavailable summary sense checks suppress the Thai model summary.
Fact-language flags remain visible in the audit; prior flags and archived
feedback survive reruns, including the earlier wording concern about
“ลงน้ำหนัก”. Fresh review does not erase that history. Checker messages may
remain in their original English when quoted in Thai facts.

`review_recording.py` rederives current recordings before fresh sense calls;
`verify_recording.py` audits current development payloads, bilingual roles,
summary slots, prior flags and ledger settlements offline. The development
cache is `fixtures/development/current-cache/`, separate from hero playback.

The [portable spend snapshot](fixtures/spend-snapshot.json) retains the 214
measured usage records and matching settlement receipts without raw model prose.
When the local task state or shared ledger is absent, offline verification uses
that snapshot automatically. Verify the shipped hero and receipt arithmetic
without credentials, a model call or a checker rerun:

```sh
uv run --offline --no-sync python -m experiments.mvp.pipeline.explain.verify_hero
```

**No native Thai reader has reviewed these explanations.** No Thai input parsing
or Thai voice accuracy is claimed.
