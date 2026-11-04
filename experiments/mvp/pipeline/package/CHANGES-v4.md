# Handover and freeze fixes prepared for pipeline v4

D310/code13/logic10 and D311 item 10 addressed. No real manifest was built and
no stored fixture or published hero anchor was changed. The 3.1 measurements
remain on v3; these changes affect handover and freeze code only.

Paths below are relative to this folder unless prefixed `../freeze/`.

| Fix | Implementation | Regression |
| --- | --- | --- |
| Approval transplant (code13 P1-1): recover missing flags only from an intact signed runner receipt bound to this packet and exact ordered sentence/reading payload. Recompute the recorded full reading `receipt_sha256` and confirmation-stage input/output digests. Without that payload, only an independently anchored original receipt digest can supply recovery. Explicit invalid flags always refuse. Sign and re-audit the same original evidence. | `provenance.py:47`; `__init__.py:85`, `:248`, `:399`, `:554` | `test_approval_v4.py:191` (changed sentence/reading/same-count writer replacements), `:211` (authentically re-signed verifier replacements), `:135` (evidence/digest tampering); `test_handover_review.py:107` (missing evidence refusal) |
| Original approval fix: require literal True and complete ordered packet coverage, including duplicates; continue auditing intrinsic rule violations. | `__init__.py:49` | `test_approval_v4.py:42`, `:65`, `:76`, `:92`; `test_logic_review.py:61` |
| Hero acceptance (code13 P1-2): compare package rules with independently audited normalized original runner rules; preserve the original receipt unchanged. | `hero_acceptance.py:121` | `test_hero_acceptance.py:196` (real retained runner-shaped rows → fresh writer → verifier → acceptance, temporary key/output, recorded checker, original fixture bytes unchanged) |
| Automatic regeneration (code13 P2-1): refuse every source override before side effects; force the disclosed batch source. | `regenerate.py:197` | `test_bay9_regeneration.py:27` |
| Canonical aliases (code13 P2-2): normalize `canonical_envelope` once to `reading`, preserving original evidence. | `__init__.py:85` | `test_presentation_v4.py:118` (writer → render → verifier); `test_approval_v4.py:243` |
| Labels (logic10 S1–S3): user-authorised batch disclosure; plain expected-rule harness label; unknown sources explicitly described as recorded; missing identity says who confirmed is not recorded. Recorded planner timestamps remain visible. | `confirmation.py:7`, `:11`; `note.py:196` | `test_presentation_v4.py:35`, `:63`, `:95`; `test_note.py:125` |
| Refusals (logic10 S4): sentence-specific request for planner confirmation and heading “Instructions failed confirmation or deterministic recheck”. | `__init__.py:70`, `:254` | `test_approval_v4.py:42`, `:92` |
| Bay plan (4.1 item 3, logic10 S5): every confirmed verbatim sentence and label beside the reader line; short “Canonical rule: close bay 09” with complete JSON on a smaller second line. | `render.py:145`, `:209`; `confirmation.py:29`, `:51` | `test_presentation_v4.py:47`, `:76`, `:114` |
| Runtime classifier (original P2-1): explicit data formats only; executable bits, shebangs and unknown formats count as source drift; symlink and pinned-file checks retained. | `../freeze/runtime_outputs.py:19`, `:29`, `:120`; `../freeze/verify.py:208`, `:267` | `../freeze/test_runtime_data_formats.py:12`, `:32`, `:51`, `:60`, `:74` |
| D311 item 10: register the explanation development current-cache. A changed live policy never expands an older manifest: retain its snapshot only when reconstructed config bytes match the independently pinned old hash; report config-file drift. | `../freeze/runtime-outputs.json:4`; `../freeze/runtime_outputs.py:139`, `:156`; `../freeze/verify.py:145` | `../freeze/test_development_runtime_cache.py:16`, `:30`, `:47` |

Runtime allow-list: `.bin`, `.csv`, `.html`, `.json`, `.jsonl`, `.lock`, `.log`,
`.md`, `.npy`, `.npz`, `.safetensors`, `.sqlite`, `.txt`. HTML is an observed
report format; `.bin`/`.safetensors` preserve existing tested compatibility.
This is suffix admission plus executable/shebang checks, not a content-schema audit.

Read-only inventory of all five registered runtime directories: **3,737 files,
none rejected**. Formats: CSV 14, HTML 4, JSON 2,305, JSONL 29, LOCK 2, LOG 17,
MD 4, NPZ 1,362. The newly registered
`explain/fixtures/development/current-cache/` contains **193 JSON + 2 LOCK**.

Verification: package folder **221 passed in 49.69 s**; freeze folder
**362 passed in 10.57 s**. Total **583 passed**, zero failures or skips.
Only package/ and freeze/ tests were run, offline, cache provider disabled.

Dry integration: fresh writer → verifier → hero acceptance passes on real
retained runner rows using a temporary test key and recorded checker report.
An additional standalone acceptance audit returned no errors and verified the
new package's original runner-receipt file is byte-identical to the retained
one. Result: `/private/tmp/stowlm-v4r2-package-final/test_fresh_writer_normalized_r0/new-hero/dry-hero-acceptance.json`.
This does not rerun planning, models or numerical physics. Full regeneration
is deferred until v4: the live v3 guard rejects intended drift, and the current
regeneration API publishes an anchor and writes application caches/signatures.

V3 drift (relative to `experiments/mvp/pipeline/`; only files changed in these rounds):

- `package/CHANGES-v4.md` (added)
- `package/__init__.py`
- `package/__main__.py`
- `package/confirmation.py` (added)
- `package/hero_acceptance.py`
- `package/note.py`
- `package/provenance.py`
- `package/regenerate.py`
- `package/render.py`
- `package/test_approval_v4.py` (added)
- `package/test_bay9_regeneration.py`
- `package/test_handover_review.py`
- `package/test_hero_acceptance.py`
- `package/test_hero_regeneration.py`
- `package/test_note.py`
- `package/test_presentation_v4.py` (added)
- `freeze/runtime-outputs.json`
- `freeze/runtime_outputs.py`
- `freeze/test_development_runtime_cache.py` (added)
- `freeze/test_runtime_data_formats.py` (added)
- `freeze/verify.py`
