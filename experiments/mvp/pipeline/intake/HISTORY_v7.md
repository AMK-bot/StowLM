# Freight document intake

`read_documents(documents, *, base_instance, client=None) -> IntakeResult` reads synthetic booking confirmations, shipping instructions, VGM and DG declarations, EIR gate slips, Thai correspondence and photographed tally sheets. `answer_questions(result, answers)` applies explicit host decisions and reruns every check. The current demo contains **eight boxes**; the blind comparison contains **five labelled cases, 40 boxes**. Neither represents the 195-box plan or a whole ship.

Spark interprets arbitrary sheet headers, reads paper and images, and returns individual declarations and exceptions. Software parses CSV cells with physical coordinates and verifies their units. There is no fixture-header alias table, JSON cargo cell or generator oracle in the model input. Software derives length, height, category, weight class and IMDG classification. Administrative sheet fields have bounded cell checks and remain separate from cargo.

The named **Literal-header parser** is the no-model baseline. It recognizes canonical field names and a fixed small freight vocabulary, plus a fixed English prose-label grammar. It has no OCR, Thai semantic interpretation or learned header mapping. Both methods use the same business gates. Taking the model out preserves deterministic parsing and validation, and removes arbitrary-header interpretation, image transcription, Thai readings and model-reported exceptions. The comparison measures the resulting difference rather than assuming a benefit.

A model proposal never supplies a conversion rule, default permission or planner confirmation. CSV evidence binds document, physical row, column, header, raw cell and normalized value. Prose quotes must exist literally and bind the same container and field. Paper/image readings need independent agreeing sources; forwarded copies do not supply another attestation. Image transcriptions remain model claims until corroborated. Conflicts retain both source values. Unknown populated sheet columns, independent amendments and every unresolved business predicate stop for a person. A question on one field never excuses checking another.

Gates check the `Container` model, yard and booking membership, current-port POL, rotation POD, vessel/voyage, ISO geometry and reefer group, VGM presence, mass against a stated rating or per-length ceiling, approximate tare bands, empty/gross consistency, explicit temperature range and Fahrenheit flags, and UN/class/packing-group agreement with the repository IMDG table. LQ and marine-pollutant declarations are sourced, not inferred. Multiple UN entries are checked independently. The fixture's vessel policy states its screening ceilings; the tare and empty tolerances are approximate screening assumptions, not operational limits. Unsupported equipment and IMDG entries remain questions. Consignee changes and operational date validation are outside the cargo contract.

Stowage words go through `route_special_stowage` to the authenticated reader. The host must separately confirm its receipt before `accept_reader_confirmation` clears the referral. Unsupported instructions remain unresolved or may be shut out. See [P22-HANDOFF.md](P22-HANDOFF.md) for the stable fixture filenames, new raw-reading contents and answer actions. A recorded demo needs no network, GPU or paid call.

Run from the repository root:

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

Replay the current demo with `RecordedClient('experiments/mvp/pipeline/intake/fixtures/realistic_v7/demo/spark_raw.json')`. Passing no client makes a paid call. Paid recording uses the shared ledger and a $10 total task cap, including the unresolved historical billing hold. The five blind inputs were authored independently and their exact document/header bytes frozen before development tuning. Implementation, effective prompt, dependency closure and runtime are pinned before the first blind call. Each captured response is bound to the exact request and scored once; changed inputs, substituted readings and repeated blind scoring are refused.

Planner workload is the share of boxes stopped by a row question or a document-wide review. Problems count as found only when a question matches the planted field and reason; unrelated missing fields do not count. Tally coverage counts actual returned image POD cells, including correctly read conflicts. Business-field accuracy excludes derived classifications and permission defaults. A silent wrong field counts even if another field on that row has a question. These are deliberately difficult synthetic cases, not an estimate of production prevalence. Explicit synthetic planner corrections demonstrate completion and assembly equality; they are never an accuracy score.

Previous held-out scores are spent. Their artifacts are untouched. The **92.73% result is a retrospective replay**, retained in [HISTORY.md](HISTORY.md), not a current headline. Historical completion after answers was truth-assisted and is not model accuracy. Old recorded payloads retain the legacy compatibility path; the rebuilt stage uses interpretation schema v7.

The final-prompt development replay (the same public eight-box packet, recorded again during tuning) returned 82/86 correct business fields for Spark and 55/86 for the parser; stopped 4/4 versus 2/4 planted problems; and stopped 8/8 boxes for both methods. The earlier two development recordings used an older prompt and remain explicitly development artifacts. No blind formats or answers were consulted during these changes.

The positive end-to-end regression uses the actual final-prompt development reading plus a separately labelled generated SI in-gauge declaration. It accepts clean mixed-class DG, LQ/marine-pollutant cargo, two UN entries and a reefer with all canonical rows equal to their targets. That supplement is not part of a paid request or scored fixture. The original public packet's lone EIR in-gauge declaration still causes a planner question. This limitation is included in workload rather than removed from the test.

The new recorded demo reaches `complete` after explicitly labelled synthetic host decisions, with assembled SHA-256 `b259068639ea6cfc9a3346850188abb2cd420cf575630bba2cdf7237ba6824fc`, equal to its base instance. Its initial strict score includes five wrong DG representations on one box: the key says non-DG while booking, SI and the DG form all say DG, with an LQ contradiction that the stage stops. The declarations are sourced; the fixture key/document mismatch is preserved. Do not describe these as five independent unsafe boxes or silently discard them.

One blind response was rejected by the frozen nested-field whitelist before a successful raw-reading artifact existed. The response and charge are preserved as `spark_response.json` and `spark_rejected.json`; there was no retry or schema relaxation. Its comparison receipt gives zero accepted fields/rows, all fields unresolved, full workload and no reason-specific trap credit. This is a scored failure, not an excluded case. Its request timing was not emitted before validation raised, so the sum of successful-reading seconds is a lower bound. Raw image readings from the rejected response are reported separately from usable tally cells. The live API currently raises this schema error; the rejected `IntakeResult` in that failure fixture is the scoring/playback representation, not a successful live return.

Fresh one-shot comparison: **five independently authored synthetic cases, 40 boxes**. One rejected eight-box reading stays in every denominator. Source SHA `1938292189da074778a793de2495ee377d55531f30a26f37bf1f67b0233f90dd`; input-manifest SHA `dfb86c1e226d8a08e26a43c60bf810ec88ca8a4e35441cd3f12f1e4d25c9744c`.

| Metric | Muse Spark + software | Literal-header parser |
|---|---:|---:|
| Correct business fields | 315/433 (72.75%) | 119/433 (27.48%) |
| Rows exact before host answers | 0/40 | 0/40 |
| Planner workload | 40/40 (100%) | 40/40 (100%) |
| Planted problems found by field + reason | 20/24 | 5/24 |
| Plausible problems found by field + reason | 15/19 | 3/19 |
| Silent wrong business fields | 1 | 8 |
| Usable tally POD cells read correctly | 32/40 | 0/40 |
| Raw tally POD cells read correctly (includes rejected output) | 40/40 | 0/40 |
| Clean DG boxes accepted without host questions | 0/10 | 0/10 |
| Clean reefers accepted without host questions | 0/5 | 0/5 |
| Known reading + validation seconds | >= 322.11 | 3.69 |
| Fresh API dollars | $0.38851925 | $0.00 |

| Planted problem | Muse Spark + software | Literal-header parser |
|---|---:|---:|
| amendment_pod | 1/1 | 1/1 |
| cod | 1/1 | 1/1 |
| duplicate | 1/1 | 0/1 |
| empty_gross | 1/1 | 0/1 |
| fahrenheit | 1/1 | 0/1 |
| lq_contradiction | 0/1 | 0/1 |
| marine_pollutant_contradiction | 0/1 | 0/1 |
| missing_setpoint | 1/1 | 0/1 |
| missing_vgm | 1/1 | 1/1 |
| net_as_vgm | 0/1 | 0/1 |
| off_rotation | 1/1 | 1/1 |
| paper_tally_pod | 4/5 | 0/5 |
| plausible_lb_kg | 1/1 | 0/1 |
| reefer_iso | 1/1 | 0/1 |
| shut_out | 1/1 | 1/1 |
| tare_band | 1/1 | 0/1 |
| temperature_range | 1/1 | 0/1 |
| unbooked_vessel | 1/1 | 0/1 |
| unknown_un | 1/1 | 0/1 |
| wrong_pol | 1/1 | 0/1 |

The zero-silent-error target is **not met**. The one fresh Spark error is an extra -18 C set point on the box with a stopped reefer/ISO contradiction. The point remains in the returned partial row without its own blocking question. The official direct-field plausible-trap counter is zero, but that narrow counter does not attribute this dependent-field error to the reefer trap; it is not a zero-error safety claim. No held-out tuning or rescoring was performed. The three schema-valid cases plus the final case found every planted problem; the rejected case received no specific-trap credit. All boxes still need a host, primarily because one EIR in-gauge declaration lacks independent corroboration; some also have incomplete model paper readings. The fresh set demonstrates useful reading, not automatic clean-list completion.

The schema rejection was caused by duplicated DG path components such as `dg.additional.0.dg.un_number`, not an invented source value. The whitelist stayed frozen. Its fail-closed scoring representation was created after the observed exception, gives no accepted cargo or trap-finding credit, and is disclosed separately from the frozen successful-case scorer. The captured response remains unchanged.

Verification: **318 offline tests passed** in the final complete run; no network/GPU. Demo is complete and assembly equals base. Total measured Spark spend is **$1.18894375**, plus the unchanged **$1.409024 unknown hold**: **$2.59796775 committed**, below the $10 cap. The fresh five-call comparison cost $0.38851925; no Muse Image spend. All recordings and scores finished in the foreground.

Input assumptions: synthetic cargo facts on eight existing yard IDs from each independently selected unused quick-small map; exact map paths and hashes are in each expected.json. Administrative dates are checked for format/calendar validity, not operational cutoffs. Callers must mark CSV MIME/path and reliably classify amendment documents with role="amendment"; independent amendment review currently uses that host metadata. Bare arbitrary CSV text or an unclassified amendment can remain dependent on model interpretation. The corpus is small and intentionally problem-heavy.
