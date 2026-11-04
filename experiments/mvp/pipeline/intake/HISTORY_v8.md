Archived SPENT v8 run. Statements below describe that original run and its then-frozen code, not the final pipeline or sealed set.

# Freight intake with document authority

`read_documents(documents, *, base_instance, client=None) -> IntakeResult` and `answer_questions(result, answers)` retain their shared shapes. The live path uses authority policy 8; historical recorded payloads retain their policy version. The current replay is `fixtures/authority_v8/demo/`: **12 synthetic boxes**, with booking, SI, VGM, DG, complete reefer/OOG lists, gate slips, Thai correspondence and a tally photo. It is not a whole-ship or 195-box benchmark.

A typed, grounded authoritative source is enough: booking owns POD/vessel/status, VGM owns gross mass, DG owns its declarations, SI owns equipment/POL/empty/commodity/set point, and gate slips own id/seal. All other actual declarations are still compared. Authority never picks a winner in a conflict. CSV cells are parsed by software from model-proposed physical header bindings. Prose must match actual words and the same box; photos and handwriting still need independent agreeing evidence. Unsupported model proposals remain rejected source traces; a redundant unsupported proposal does not invalidate an independently sourced value.

Closed-world declarations require a host-supplied role, matching booking reference and **explicit `complete_booking_list: true`**, backed by the actual complete-list document. Software reads membership from source bytes, independently of model omissions. Unlisted boxes are non-DG/non-operating/in-gauge only within that booking's readable roster. Missing scope, foreign identities, incomplete lists and photos used to prove omissions stop for review. Missing documents produce one global task; missing per-box declarations remain row tasks. A listed but unread DG box cannot become non-DG because Spark omitted it. Listed OOG cargo needs verified dimensions.

Unstated tare uses a labelled approximate ISO representative and size band; a stated tare wins, including an invalid stated tare that must stop rather than be replaced. Container/yard/rotation/vessel, POL, gross/rating/ceiling, empty/gross, tare-band, reefer/ISO/Fahrenheit/range, UN/class/PG/LQ/MP and amendment gates remain active. Conflicting or missing DG leaves quarantine the entry. A set point is independently blocked when reefer/ISO identity is unresolved. Partial rows preserve unknowns rather than materializing Container defaults. No row-level question excuses another field. Seal and commodity are auxiliary source traces; the shared Container contract is unchanged.

Live schema rejection permits **one bounded retry**. Both responses, request digests, raw text, usage, dollars and validation errors are retained in metadata/StageRecord notes. No HTTP/billing retry occurs. Two invalid responses return one rejected-batch task with no accepted cargo. Recorded replay and benchmark recording use exactly one attempt, including failures. A model/document cannot create a planner confirmation. Stowage sentences still go through the authenticated reader and a separate host confirmation; `shut_out` remains available.

The named **Literal-header parser** uses the same authority policy and cargo gates, with its fixed English label grammar and literal header vocabulary. It has no learned arbitrary-header mapping, Thai interpretation or OCR. Both methods receive the same closed-world and tare policy; derived business facts count as resolved facts for both. Derived classifications and safety-permission defaults are excluded from business-field accuracy. Every current table compares both methods.

The five new blind cases contain **60 boxes, 12 per case**, with three planted problem rows per case. Inputs and header variants were authored independently and frozen before model reads. An initial unscored fixture construction error (SI header/cell alignment) was found in public development, archived intact under `authority_v8_unscored_construction_error`, and replaced with different unused maps and fresh unseen header variants. No model calls or scores touched that discarded set. Replacement input SHA: `b988950c6772d0526fd8e4092b69c7ad2798edb77e9a984677ee77a495ea5b14`. Source/prompt/runtime/dependencies and the one-attempt failure policy are pinned before scoring; every case remains in the denominator. No tuning or retries occur on this fresh comparison.

Development used the now-spent v7 results (including a regression for its silent set point) and public v8 cases. Final public development: Spark stops 3/12 boxes, accepts 9/12 exactly, finds 3/3 problems and has zero wrong fields. One earlier development response omitted supplementary columns; the prompt was strengthened to map every populated cargo declaration even on non-authoritative forms. Those earlier paid responses are retained. The final demo completes after labelled synthetic host answers; its assembled hash equals base (`dcdf1772058fa882dedd24a409b21f42a0eeff2529d80e756e3b70335264e7af`). It also requires an explicit review of an unmapped reefer-list header, so its initial workload is not presented as the development target.

Workload is the share of boxes stopped, including all boxes affected by a global document task. Trap credit requires the planted field and a matching reason, not an unrelated question or whole-call rejection. Raw tally readings are separate from usable cargo. Synthetic host corrections demonstrate completion, never accuracy. Small, intentionally difficult fixtures do not estimate operational prevalence. The earlier v7 one-shot score is spent and preserved in [HISTORY_v7.md](HISTORY_v7.md); older records are in [HISTORY.md](HISTORY.md).

Run offline from the repository root:

```sh
export UV_CACHE_DIR=$PWD/.uv-cache
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
uv run --offline --no-sync python - <<'PY'
from experiments.mvp.pipeline.intake.admission import wait_for_slot
wait_for_slot()
import pytest
raise SystemExit(pytest.main(['-o','addopts=','-q','experiments/mvp/pipeline/intake','-p','no:cacheprovider']))
PY
```

Replay with `RecordedClient('experiments/mvp/pipeline/intake/fixtures/authority_v8/demo/spark_raw.json')`; passing no client makes paid calls. Benchmark commands are `python -m experiments.mvp.pipeline.intake.campaign_v8 freeze|blind|score|demo`; existing blind scores refuse replacement/re-scoring. Host document roles/scope are required inputs, not model authority. See [P22-HANDOFF.md](P22-HANDOFF.md).

Fresh one-shot result: **five cases, 60 boxes**, not a whole-ship test. **Both targets are unmet.** This table retains every failure under the preregistered evaluator.

| Metric | Muse Spark + software | Literal-header parser |
|---|---:|---:|
| Planner workload | 42/60 (70%) | 60/60 (100%) |
| Correct resolved business fields | 446/630 (70.79%) | 227/630 (36.03%) |
| Rows exact before host answers | 18/60 | 0/60 |
| Planted problems found by field + reason | 12/15 | 4/15 |
| Plausible problems found by field + reason | 9/12 | 3/12 |
| Frozen wrong-value counter | 3 | 0 |
| Audited wrong fields actually serialized | 1 | 0 |
| Counter artifacts: absent DG keys treated as null | 2 | 0 |
| Raw tally POD cells read correctly | 60/60 | 0/60 |
| Clean DG accepted without host questions | 4/10 | 0/10 |
| Clean reefers accepted without host questions | 4/10 | 0/10 |
| Reading + validation seconds | 268.35 | 3.37 |
| Fresh API dollars | $0.28447500 | $0.00 |

| Planted problem | Muse Spark + software | Literal-header parser |
|---|---:|---:|
| amendment_pod | 1/1 | 1/1 |
| cod | 1/1 | 1/1 |
| declared_vs_vgm | 0/1 | 0/1 |
| dg_lq_conflict | 1/1 | 0/1 |
| dg_mp_conflict | 1/1 | 0/1 |
| duplicate | 1/1 | 0/1 |
| empty_gross | 1/1 | 0/1 |
| fahrenheit | 1/1 | 0/1 |
| image_pod | 0/1 | 0/1 |
| missing_vgm | 1/1 | 1/1 |
| reefer_iso | 1/1 | 0/1 |
| temperature_range | 1/1 | 0/1 |
| unknown_un | 1/1 | 0/1 |
| wrong_pol | 1/1 | 0/1 |
| wrong_vessel | 0/1 | 1/1 |

The 42 stopped Spark boxes comprise one rejected 12-box reading, two 12-box batches with unresolved reefer-sheet header bindings, and six problem boxes in the two remaining cases. Those two cases reach the development target of 25% workload; the full result is 70%, which is the reported workload. There was no retry, exclusion, tuning or replacement on this set. Live schema retry is implemented/tested but no improved blind retry result is claimed.

The remaining real error is `iso_type=22G1` versus target `22R1` on blind_03/QZXU0000077. Its operating-reefer contradiction and dependent set point are blocked, but the ISO counterpart itself remains unblocked. The audit missed that counterpart; the zero-silent-error target is not met. The frozen wrong-value counter also reports two phantom `dg=null` values on blind_01, although those schema-rejected rows contain only id keys and no DG field. We preserve the counter **3** and separately audit **1 actually serialized wrong field + 2 absent-key counter artifacts**. This is a receipt inspection, not a new score or repaired model reading. `counter_audit_v8.json` records both lists.

Source/prompt/runtime freeze SHA: `873ed707ef15f3cfac7b6296a53f3c11213d5e7e1a4b043412d8171cf0d58a96`. The final source remains byte-identical to that freeze. All paid calls and scores finished in the foreground. Final full offline pytest: **387 passed**, with network blocked and fleet admission. Demo remains `complete` and assembled hash equals base.

Spend: $1.72698300 measured Spark + unchanged $1.409024 unknown hold = **$3.13600700 committed / $10**. V8 development/demo calls are retained, including the demo before the final prompt. Fresh five-call spend is $0.28447500. No Muse Image spend.
