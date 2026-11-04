# D219 reader data v2

Frozen inputs are synthetic v1 `train`, `valid` and family `test` only. No sealed file is an input to the builder or its tests. No Spark generation was needed: new spend is **$0.00** against the $3 paraphrase cap.

| Split | Rows | SHA-256 |
|---|---:|---|
| train | 2,224 | `fbb1c63d06e04290787f7ba326ce1c021914b1669a7e390171cbb9930108ec9d` |
| valid | 772 | `0506518767ce4c404d5734571b628e0803d33a61774d8e5e40d61332a62d80f3` |
| family test | 746 | `897d081f815f51c3aa2291bd3c99b375a3dba042b6d0401ede99029cd73ed9af` |

Validation and family test are byte-identical to the original heldouts. Training retains all 122 original training families at up to eight examples per family and adds 22 authored construction families. Every surface variant of an authored construction belongs to the same train-only family. The checks require disjoint family IDs, normalized sentences, and sentences with numbers/classes/ports replaced by placeholders across train/valid/test. The new examples do not copy the heldout error sentences.

Training contains **700 loosening examples (31.47%)** and **410 hedged examples (18.44%)**. The corresponding v1 shares were 364/4,325 (8.42%) and 328/4,325 (7.58%). Of the new examples, 352 explicitly present a larger tier cap or smaller DG separation as a strict limit; old and new values are both stated, so the direction of relaxation does not require guessing existing operational state. Other loosening families cover exceptions to closure, relaxation within a seemingly restrictive mixed instruction, cancellation of extra restrictions, and partial removal of the hatch rule.

Targeting uses the five completed `checks/*/valid-sample.json` artifacts, whose hashes and failing-family counts are recorded in `manifest.json`. The latest check (002000) scored 74/80: `grammar.deck.06` was 2/4, with weather deck confused for hold; `grammar.unsupported.06` was 0/4, with destination-specific bay reservation confused for closure or hatch grouping. Earlier checks also exposed hedged, commentary, bad-range, bad-bay, and loosening errors. The additions cover these error categories. A completed `results/family-test-tuned.json` was absent at build time, so it contributed no error evidence.

All targets are authored from sentence intent, then checked against the strict schema and synthetic vessel context. No rule parser or model supplies labels; there is no reader-output repair. Tier-increase examples remain physically in range, making the refusal depend on semantic relaxation. Contexts are retained from the existing synthetic training data. Rehearsal examples are uniformly capped to allow one complete epoch within the three-hour stop; this deliberately replaces the v1 3,000-row minimum with a token-budgeted v2 dataset.

Reproduce from the repository root, CPU only, one worker:

```sh
UV_CACHE_DIR=$PWD/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false uv run --offline --no-sync python experiments/mvp/reader/build_data_v2.py
UV_CACHE_DIR=$PWD/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false uv run --offline --no-sync pytest experiments/mvp/reader/test_data_v2.py -q
```

`tests.log` records all six passing tests, covering schema/target integrity, byte-identical heldouts, stronger split separation, safety mixture, variant grouping, no sealed-file reads, and byte-reproducible rebuilding. CPU tokenizer length/prefix verification is recorded separately in `token-preflight.json`; it loads no model weights. Exact full-chat token totals are **1,482,786 train**, **511,223 valid**, and **493,355 family test**; maximum lengths are 717, 716, and 718 respectively, all below 2,048. A full epoch has 2,224 microbatches and 556 optimizer updates at accumulation four. GPU execution and the final evaluation gates belong to `../GPU_HANDOFF.md`.
