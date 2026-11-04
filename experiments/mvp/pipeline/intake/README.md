# Freight intake: frozen for the sealed run

`read_documents(documents, *, base_instance, client=None) -> IntakeResult`, `answer_questions(result, answers)`, and the named `parser_baseline` retain their shared shapes. The demo remains `fixtures/authority_v8/demo/`: **12 synthetic boxes**, complete after explicit synthetic planner decisions, assembled SHA-256 equal to base. Completion after answers is a demo interaction, never accuracy. See [P22-HANDOFF.md](P22-HANDOFF.md).

Final tuning is finished. ISO is compared with every grounded explicit length, height, operating-reefer, reefer-equipment and tank declaration, even when the row or an input already has another question. Contradictions question both ISO and the conflicting field and block dependent software dimensions, tare, category and set point. Rejected, uncertain, foreign-booking or genuinely superseded claims cannot manufacture evidence. An explicitly non-operating reefer remains valid equipment. Explicit dimensions require their actual labels/units; software still derives final dimensions from validated ISO. Multiple explicit declarations remain separate claims.

The wrong-value counter now distinguishes an absent key from an explicit null, including absent DG and set point keys. Explicit wrong facts on otherwise questioned rows still count. The one workload-policy pass only rejects a redundant model CSV transcription without another task when the **same document, field and normalized value** already has software-parsed cell evidence. That model proposal never becomes evidence. Unsupported proposals, actual cell contradictions, unbound headers, amendments and every existing safety gate still stop.

Authority policy 8 remains: booking owns POD/vessel/status; VGM gross; DG its declarations; SI equipment/POL/empty/commodity/set point; gate id/seal. All actual contrary declarations are compared. Photos/handwriting require independent agreement. Closed-world negatives require explicit complete-list scope, matching booking reference and independently readable membership. Missing documents create one global task. Representative tare is explicitly approximate and derived; stated tare wins and invalid stated tare stops. Live schema repair is bounded to one retry with the first response retained; scoring stays one attempt. Words requesting stowage rules still route through the authenticated reader and a separate genuine host confirmation.

All earlier scores are **spent**, available only as historical results or development recordings. The last tuning round changed validation/counter behavior, so the following offline development replay reports old and new side by side. The original paid responses, manifests, metrics and score seals remain unchanged. No new model reading was obtained. [final_development_checks.json](final_development_checks.json) records the replay; [HISTORY_v8.md](HISTORY_v8.md), [HISTORY_v7.md](HISTORY_v7.md) and [HISTORY.md](HISTORY.md) preserve the earlier reports. Their former held-out/blind labels describe those original runs; none is an unseen test now.

**Spent v8 recordings: five cases, 60 boxes.** Each method uses the same deterministic gates and policy. These are development numbers, not a new blind score.

| Metric | Spark, original spent | Parser, original spent | Spark, final development replay | Parser, final development replay |
|---|---:|---:|---:|---:|
| Correct resolved business fields | 446/630 (70.79%) | 227/630 (36.03%) | 446/630 (70.79%) | 227/630 (36.03%) |
| Rows exact | 18/60 | 0/60 | 18/60 | 0/60 |
| Planner workload | 42/60 (70%) | 60/60 (100%) | 42/60 (70%) | 60/60 (100%) |
| Planted problems found by field + reason | 12/15 | 4/15 | 12/15 | 4/15 |
| Plausible problems found by field + reason | 9/12 | 3/12 | 9/12 | 3/12 |
| Wrong serialized values / original counter | 3 (1 real + 2 artifacts) | 0 | 0 | 0 |
| Raw tally POD cells read | 60/60 | 0/60 | 60/60 | 0/60 |
| New API spend for this replay | — | — | $0 | $0 |

The workload remains 70%; the target of about one third was not reached. A rejected batch and unresolved sheet-header bindings still require people. Zero wrong serialized values does not claim every plausible problem was specifically identified: the evaluator still flags one Spark plausible problem and three parser plausible problems without a matching field stop/reason. Small, intentionally difficult documents do not estimate production prevalence. The historical retrospective parser replay remains on record; it is not a fresh baseline score.

A separate **sealed set contains six fresh cases / 72 boxes**, generated from previously unused development-map sources with fresh documents/header variants. The generator validated construction in memory, then hashed its output. Its case documents, expected answers and base instances have never been reopened, model-read or scored. Only manifest metadata and byte hashes may be checked before Task 3.1. No accuracy is claimed for it.

Sealed manifest: [fixtures/sealed_intake_v1/blind_freeze.json](fixtures/sealed_intake_v1/blind_freeze.json)

SHA-256: `c3b7cb0d553ae516c447984c7efaffde0d0c39c7c4b3233b93bbb90b43d67e1e`.

Task 3.1 runs this **once** from the repository root:

```sh
bash experiments/mvp/pipeline/intake/score_sealed.sh
```

The launcher supplies the prescribed environment and silently sources the key. `SOURCE_FREEZE.json` pins intake source, launcher, prompt, project import closure, IMDG data, package/lock files and runtime. Code/input mismatch refuses before the run claim. An atomic `RUN_CLAIM.json` consumes the only invocation, including interruption; there is no second run or resume. Each case gets one paid attempt. Parser and Spark are scored beside each other; schema/recorded transport/validator failures remain in the denominator with rejected facts, full workload and no specific trap credit. Unknown billing remains labelled unknown. Recorded requests/responses and per-case/aggregate scores receive immutable hash receipts. The scorer has been verified only on throwaway toy data; it has not been invoked on this set.

Offline verification, with network blocked and fleet admission:

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

Spend is unchanged: **$1.72698300 measured Spark + $1.409024 unknown hold = $3.13600700 committed / $10**. No calls were made in this final round; no Muse Image spend. Task 3.1 still uses the shared ledger and cap. Verification and source-seal receipts are in [verification.json](verification.json).

Final verification: **437 offline tests passed**; launcher syntax passed. Source seal SHA-256: `30144ec25c1f5e7dff1d0f7e38576a9a297d2a53f736ae2d1d2808148b92acac`. The hash-only freeze check passed and `RUN_CLAIM.json` is absent.
