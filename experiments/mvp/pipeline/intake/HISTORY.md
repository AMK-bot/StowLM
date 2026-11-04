# Historical intake record — superseded by v7

This is the preserved pre-v7 README. Its tables describe spent fixtures and retrospective replays, including 92.73%; none is a fresh claim for the rebuilt stage. Historical “after answers” figures are truth-assisted completion checks, not model accuracy. See README.md for the current parser comparison and independently frozen five-case test.

Synthetic voyage documents become sourced cargo rows and blocking questions. Muse Spark reads a forwarded Thai-English booking email, a mixed-unit CSV dump and the actual bytes of a skewed tally-sheet picture. Software validates the proposal. The demo replays the recorded answer without a network, GPU or paid call.

Run from the repository root:

```sh
export UV_CACHE_DIR=$PWD/.uv-cache
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
uv run --offline --no-sync python -c 'from experiments.mvp.pipeline.intake.admission import wait_for_slot; wait_for_slot()'
uv run --offline --no-sync pytest experiments/mvp/pipeline/intake
```

```python
import json
from pathlib import Path
from experiments.mvp.pipeline.intake import read_documents, answer_questions
from experiments.mvp.pipeline.intake.spark_reader import RecordedClient

case = Path('experiments/mvp/pipeline/intake/fixtures/demo_v2')
result = read_documents(
    json.loads((case / 'documents.json').read_text()),
    base_instance=json.loads((case / 'base_instance.json').read_text()),
    client=RecordedClient(case / 'spark_raw.json'),
)
final = answer_questions(result, json.loads((case / 'answers.json').read_text()))
assert final.status == 'complete'
```

The API imports the shared `IntakeResult`, `Question`, `StageRecord` and hashing helpers from `contract/schema.py`. Inputs accept document dictionaries with `id`, `text` or `path`, and `mime_type`. Base instances may be an Instance, its JSON dictionary, or a JSON file path. Outputs are shared Pydantic wire models: use `model_dump(mode='json')` or `model_dump_json()`. Native PDFs must first be rendered into pictures. No client means a real paid Spark call.

Rows with unanswered questions are partial candidates. Consumers must gate on `status == 'complete'`. Answers map question ids to explicit values, optionally `{value, quote}`; duplicate removal requires `keep_one`. Missing-row questions accept a complete declared row. Every answer is recorded as planner evidence and revalidated; invalid answers stay questions. No value is copied from the base cargo list. Expected ids are used only for completeness; yard ids, ship geometry and port/rotation data supply validation context.

`field_sources` contains row/index, field, document and acceptance. Prose requires an exact quote plus deterministic binding to its row, field and value: an entire declarative JSON line or the explicit booking POL/POD grammar. Other prose needs an explicit planner answer. CSV evidence is parsed and bound to the container's actual record, column and JSON path; it includes `csv_row`, `column`, `cell`, original `header`, decoded `cell_value` and `normalized_value`, with `evidence=csv_cell`. CSV escaping cannot invalidate a decoded cell, and a value in the wrong row/column cannot supply evidence. Explicit source precision intervals reconcile rounded lb/MT displays only with the same row's exact EDI weight. No generic tolerance or prose-substring fallback is used for CSV. Photo quotes carry `evidence=unverified_transcription` and block acceptance until explicit planner confirmation; hidden image annotations cannot authenticate a reading. Blocking findings clear acceptance on affected source links. Each stage receipt's `notes` stores JSON containing raw proposals, document hashes/text, base validation context and findings, so answers survive serialization into another process. Preserve it when replaying. A future shared-contract improvement would give this validation context an explicit typed field; no shared schema was changed here.

The deterministic gates check Container types and consistency; explicit declarations for optional/default fields and nested DG/OOG facts; VGM and CSC rating; reefer set point; local IMDG UN/packing group/class/category agreement; yard membership and length; later-call POD; direct-delivery policy; a compatible ship cell under ship mass/height/length/reefer and current port crane-tier limits; conflicting source observations; and repeated CSV cargo ids even when Spark misses the duplicate. Missing, uncertain, contradictory or unsupported readings become questions. An absent UN is absent from the local curated IMDG subset, not asserted nonexistent worldwide. Slot screening does not replace the downstream full checker for placement, stability, segregation or aggregate ship limits.

Each safety check now runs with its own prerequisites before complete-row construction. Missing unrelated fields cannot suppress mass, yard, POD, reefer or IMDG checks. Gross mass is checked independently against the supplied CSC rating, tare, vessel support capability and any explicitly declared `base_instance.rule_overrides['intake.limits']['max_gross_mass_kg_by_length']` ceiling. Without a usable rating or declared ceiling, gross mass itself gets `missing_mass_limit`. No universal per-length CSC rating is inferred. An explicit planner answer may name an ancestor `field`, such as replacing a false nested DG declaration with declared `dg=null`; it is sourced and revalidated normally.

## Measured accuracy

The original development and first held-out inputs were frozen before scoring in `fixtures/freeze.json` (SHA256 `62f6050efd3406bd495a491c6f7091e008447d8895c422114f915de2faca5da2`). They are two approved `quick_small.txt` development maps, split by source map; the pipeline sealed sets were never opened. The derived fixture bases add a documented synthetic −18°C reefer declaration where the legacy map had unknown temperature. This makes the deliberately omitted set point answerable and permits exact base reconstruction. Other source-map cargo facts are retained.

Every case has seven known traps: cross-document destination conflict, missing VGM, unknown UN, impossible weight, missing reefer set point, off-rotation POD and duplicate id. Development exposed an omitted DG provenance field becoming an unsourced schema default; explicit nested completeness now blocks that value. The extraction prompt also asks Spark to flag uncertainty and retain supplied provenance. The first completed held-out recording was scored once with its frozen v1 implementation. An earlier attempt ended with the previous turn, produced no recording and retained an unknown-billing hold; no response from that attempt was inspected. Its completed model output and first score are preserved. After that split was declared spent, v2 fixes were developed using the development recording and separate synthetic regression tests; replacement held-out data were kept out of tuning.

Field accuracy counts correct unblocked JSON leaves divided by all ground-truth leaves; missing/questioned fields count as incorrect. Dictionaries expand recursively; lists and empty objects count as one field. Row-exact accuracy requires a complete exact row without a blocking question. Silent wrong values count emitted, unblocked values that disagree with truth. The planner-assisted figures use explicitly labelled synthetic ground-truth answers and are not model-only accuracy.

| Split | Fields before answers | Rows exact before answers | Traps caught | Silent wrong values | Fields / rows after answers | Spark seconds | Spark USD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Development, original v1 score | 251/269 (93.31%) | 6/13 (46.15%) | 7/7 | 0 | 269/269; 13/13 | 46.885 | 0.08312650 |
| Development replay, v2 validator | 225/269 (83.64%) | 6/13 (46.15%) | 7/7 | 0 | 269/269; 13/13 | 46.885 | 0 additional |
| Development replay, v6 review repairs | 216/269 (80.30%) | 3/13 (23.08%) | 7/7 | 0 | 269/269; 13/13 | offline replay | 0 additional |
| First held out, v1 (spent; frozen first score) | 52/251 (20.72%) | 0/12 (0.00%) | 6/7 | 1 | 250/251; 11/12 | 57.647 | 0.08400150 |
| New held out, v2 (3 cases; frozen first score) | 1096/1155 (94.89%) | 39/62 (62.90%) | 21/21 | 0 | 1155/1155; 62/62 | 235.248 total | 0.36379375 |
| Same v2 recordings, v6 retrospective replay (spent data) | 1071/1155 (92.73%) | 33/62 (53.23%) | 21/21 | 0 | 1154/1155; 61/62 | offline replay | 0 additional |

The original frozen v2 evaluation caught all 21 traps with zero silent wrong values. Clean controls were 39/41 exact (95.12%); two clean rows in case_02 received extra clarification questions. All 62 final rows and 1,155 fields are exact after explicit synthetic planner answers. All three cases have status `complete` and matching assembled/base hashes. Model wall times are 64.862, 93.956 and 76.429 seconds; 235.248 seconds in the table is their sum. These times exclude fleet CPU-admission waits before offline validation. Those first scores used the originally frozen code, prompt, inputs and readings. Later review repairs are reported below as retrospective replays of spent data, never as fresh held-out scores.


The first held-out split is spent and is retained unchanged as diagnostic history, including its raw answer, receipts and first score. It failed the intended zero-silent-wrong-value gate: 182 `unsupported_source` questions rejected mostly decoded CSV assertions, and the 999,999 kg weight on `QZXU0000035` lacked a weight question while its row was incomplete. After synthetic planner answers supplied missing fields, the old validator raised the weight question. Its frozen final result remains `needs_answers`, with 250/251 correct unblocked fields (99.60%) and 11/12 exact rows (91.67%); assembly was not reached. Its initial silent-wrong count remains one. This first score is not recomputed using the fixed validator.

V2 development's 46.15% row-exact result has a fixture-design explanation: seven distinct rows contain planted traps that must become questions; the other six clean controls are all exact (6/6). Thus six exact rows out of thirteen is the safe before-answer ceiling for this case, not a reason to infer missing VGM or choose between conflicting destinations. `clean_controls` and `fixture_design` in new metrics make that distinction visible. V2's development field coverage is lower because an unknown or missing UN now blocks the entire unverified DG entry, including dependent class/category fields, until the planner confirms its declaration. It never fills those facts from a table or base row. The refreshed recorded demo is `fixtures/demo_v2`: status `complete`, all 13 final rows exact and canonical assembled hash equal to its base. Original `fixtures/dev` and `fixtures/heldout` files and shapes are retained for existing demo consumers.

The replacement evaluation uses three unused quick-small source maps, with 18/22/22 rows, seven distinct trap rows each and 41 total clean controls. Fresh documents and known answers were frozen before any model read or scoring: `fixtures/heldout_v2/freeze.json`, SHA256 `f219d62df33036027016d1ee9138c759d59c385200f35494ebb6605eda24b30d`. `maps/dev.txt` was still absent when the cases were selected. The implementation is frozen separately in `fixtures/heldout_v2/SOURCE_FREEZE.json` after development-only tuning and offline tests. `evaluate_v2 heldout` checks both freezes before each case and resume; the public `score_case` and recording entry points also enforce the strengthened gates; every case is model-read and scored once. Aggregate accuracy sums field/row counts, rather than averaging case percentages.

Full measurements are in each case's `metrics.json`, including offline validation seconds and exact assembly equality. Spark seconds measure the entire reading call, including image preparation and any wait for the shared accounting lock; they are single trials, not a latency distribution. The assembly check replaces the base's load-list rows, preserves other cargo/yard/ship context, revalidates through Instance, then compares canonical JSON SHA256 with the base. Source file bytes are separately hashed; JSON whitespace is not part of instance identity. There is no runner stub: `measure.assemble_rows` replicates the documented assembly operation because the runner was absent when this feature started.

## Fixtures and accounting

Each split contains the three labelled synthetic source files, `documents.json`, `base_instance.json`, ground truth/traps in `expected.json`, Spark's untouched response in `spark_response.json`, extracted proposal plus exact response text/usage in `spark_raw.json`, validated `reading.json` with findings/questions, explicit `answers.json`, `final.json`, and `metrics.json`. `oracle_reading.json` is labelled synthetic oracle data for validator tests, never attributed to Spark and never used for the model accuracy table. `make_fixtures.py` refuses to overwrite frozen inputs.

An authorized new recording uses `python -m experiments.mvp.pipeline.intake.record CASE` with the key only in the environment and `SPARK_CREDIT_READY=1`. Existing recordings cannot be overwritten. The folder-local multimodal wrapper subclasses `src/llm/spark/client.py`, preserving its shared ledger, credit gate and conservative reservation/settlement accounting. Calls never retry and an independent $6 task cap includes unknown billing holds. The mandatory shared ledger updates its managed accounting row automatically; no external source files are edited. Muse Image spend is $0: the tally pictures were rendered with PIL. Recorded replay has no new API charge; recorded metadata preserves the original call cost.

5 completed recordings cost **$0.53092175 measured from usage**. The three new held-out calls cost **$0.36379375**; the original development and first held-out calls cost $0.1671280. The earlier interrupted attempt retains a **$1.409024 conservative unknown-billing hold**, giving **$1.93994575 committed including that hold**, within the $6 cap. The hold is not measured spend and has not been released. The shared ledger purpose is `intake-task-1.3`; request ids and receipts are listed in `verification.json`. The v2 evaluation made no retries.

These small synthetic cases are a demo and development check, not evidence of operational accuracy. Equipment JSON in the CSV is an artificial export annex to preserve internal base attributes exactly, not a claim to implement an EDI standard. Image quotations are model transcriptions, not independently verified OCR. The conservative prose grammar intentionally asks for clarification rather than attempting general semantic entailment. Nonempty stowage requests, including dotted list entries and planner-supplied requests, remain blocked with `instruction_requires_reader`. Their unaccepted source link contains a `reader_request` with the original sentence: consumers must send it through the separate reader and its planner confirmation gate; intake cannot turn it into a cargo restriction. The local IMDG table is incomplete, and unknown declarations remain questions. All ships, cargo, companies and documents are synthetic. Labels: Synthetic document, Generated picture, Recorded answer.


## Review v6 hardening

All three P1 and both P2 findings in `experiments/review/v6/code4.md` have dedicated adversarial regressions. A quoted value must be declared for that exact cargo field; unsupported prose and image transcriptions become questions. Document metadata claiming `origin=planner_answer` grants no authority. Planner confirmations and duplicate resolutions are separate receipt context created only by `answer_questions`, never keys in model proposals. Unknown model keys are rejected. Question answers use recorded row identities rather than parsing model-controlled question-id delimiters.

CSV checks collect every declaration before considering Spark's choice, including omitted source documents, repeated recognized columns, annex paths, parent-null contradictions and distinct physical records. Ambiguous identity columns and duplicate JSON keys fail closed. A genuine explicit planner answer may resolve a cargo-fact conflict; it cannot grant a stowage instruction permission to bypass the reader.

Public scoring verifies source/input freezes, effective prompt, transitive project code, IMDG/catalogue data, lockfiles and runtime versions before validation. Held-out recordings must bind to the exact request content and captured response; arbitrary `--raw` substitution is development-only. Paths are resolved before classification. Historical freezes without these dependency pins cannot authorize new scoring or recording. Original frozen receipts and scores remain historical artifacts; no freeze is rewritten to make changed code appear original.


The final admitted offline run passed **149 tests in 98.49 seconds** (`review_v6_pytest.json` also records 99.268 seconds around the pytest call). All three already-scored v2 cases changed behavior under these repairs, so the permitted comparison was run once and stored separately in `review_v6_results.json`. This is a retrospective development replay of spent data, not a fresh held-out score. Every original cohort artifact, including readings, answers, first scores and freeze manifests, remains byte-identical. There was no further model call or additional spend, and no held-out example was used to tune a subsequent repair.

The stricter source boundary reduces unblocked fields from **1096/1155 (94.89%) to 1071/1155 (92.73%)** and exact rows from **39/62 to 33/62**. All **21/21 traps** remain caught, with **zero silent wrong values**. After explicit synthetic cargo answers, **1154/1155 fields and 61/62 rows** are exact; **2/3 cases** complete and match their base hashes. Case_02's `QZXU0000204` declares `special_stowage=["under_deck"]`: the repaired intake deliberately keeps that rule-bearing request blocked for the separate authenticated reader/planner gate. Repeating it as a planner cargo-field answer cannot clear that gate, and no assembled hash is claimed for that incomplete case.

The separate demo was refreshed using the same raw recording and unchanged source documents. Its before-answer result is **216/269 fields, 3/13 exact rows, 7/7 traps and zero silent wrong values**; after its explicit answers it is **complete, 269/269 fields, 13/13 exact rows, with the assembled/base hashes equal**. Its JSON filenames and public shapes are unchanged. Original `fixtures/dev`, first `fixtures/heldout`, and all `fixtures/heldout_v2` artifacts remain preserved.
