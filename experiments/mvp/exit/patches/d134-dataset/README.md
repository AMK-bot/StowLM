# D134 parallel dataset replay — staged, not applied

Patch: `parallel-dataset.diff` (targets `src/llm/exit/data.py`, new
`dataset_pool.py`, and the training call/flag in `runner.py`). Production source
and tests were not edited. Apply at the D132 fresh-process boundary only.

The loop now uses a spawn process pool for per-row full admission and mask
replay. Spawn avoids inheriting the resident Metal model. One CPU tokenizer is
loaded per worker; current winners, recovery, replay, and loss-validation groups
reuse the same pool. Ordered map preserves first-occurrence deduplication,
seeded replay ordering, and the existing writer's exact row bytes. The parent
checks held-out identity before dispatch; workers repeat identity/seed/hash
checks and all existing checker/mask/recovery/sequence admission. A worker
failure aborts dataset publication.

`--dataset-workers 0` (default) chooses up to the verification worker cap, capped
by free ledger cores **and the existing active loop lease**: one host core,
64 GB resident allowance, 2 GB per replay process. It never silently increases a
reservation. Unknown/malformed ledger capacity falls back to serial. Explicit
`--dataset-workers N` is capped by the same limits. The decision is saved as
`gen-NNN/dataset-capacity.json` and in the dataset phase metadata.

Native library threads, tokenizer parallelism, and nested in-port checker
workers are set to one before spawn (and for standalone serial execution).
Previous environment values are restored after the pool joins. No GPU or model
weights are imported by the new pool helper.

## Verification

`git apply --check experiments/mvp/exit/patches/d134-dataset/parallel-dataset.diff`
passes against the frozen source. The runner hunk is confined to training and
CLI flags; it does not overlap D133 audit identity functions.

Commands run:

```sh
UV_CACHE_DIR=$PWD/.uv-cache uv run pytest experiments/mvp/exit/patches/d134-dataset/tests/test_dataset_pool.py -q
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/mvp/exit/patches/d134-dataset/tests/run_overlay.py tests/llm/exit/test_data_metrics.py tests/llm/exit/test_runner.py tests/llm/exit/test_review_boundaries.py tests/llm/exit/test_recovery.py tests/llm/exit/test_d129_salvage_integrity.py -q
```

The original 12-test suite passed; the added spawn-order/fault test and strengthened
native/nested-worker guards were checked separately. There are now 13 tests.
The staged-source overlay passes **65 existing tests**, including training epoch
integrity, recovery, inline held-out exclusion, and runner training phases.

The real integration fixture admitted 2 winners + 2 current-epoch replay targets
+ 1 loss-validation row, without checker/mask stubs. Serial versus 2 processes:
**19.706 s vs 16.096 s**, and **all bytes matched** for `train.jsonl`, `val.jsonl`,
`stats.json`, and `exit_build_report.json`. Evidence and hashes are in
`measurements/byte-equivalence.json`. This small fixture includes spawn overhead;
full-generation scaling is unmeasured.

Both runs use identical, current-epoch bound physics reports. Fresh checks of
stale replay rows legitimately contain different measured timestamps/timings,
even in two serial runs, so independently regenerated fresh-check reports are
not asserted byte-identical. No report fields were normalized or fabricated.
Stale replay still goes through the original current-epoch verification path.

Compute: fixture estimated 2 cores/3 GB/no GPU/60 s; measured dataset work
35.802 s total. A turn interruption delayed ledger cleanup to 315.935 s after
admission; this is explicitly recorded in fleet history. The separate fast
spawn safety check reserved 2 cores/1 GB/no GPU/10 s and completed <1 s, with
automatic release. All test leases are released.
