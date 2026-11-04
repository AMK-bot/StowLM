# Small TEST v2 reference tooling review

Reviewed `run_small_v2.py`, `summarize_small_v2.py`, release selection and their regression tests before the small pilot. No solver workloads executed by this review.

The driver selects only released small inputs and verifies frozen input/audit hashes, accepted audits, active v2 manifest identities and per-ship settings. Runtime/configuration hashes bind the attempt; completed resumes validate input, audit, source and artifact hashes. C2 is OFF; ballast v4 is scoped to finalization, cleared for solve/bounds/checker. Solver and finalizer limits are recorded. Plans receive the full checker; failed finishers cannot produce shippable references.

The renderer reads canonical direct checkpoints, repairs stale journals, includes failed attempts and explicit missing cases, and preserves the accepted tiny prefix byte for byte. Exact is absent from small; two-step is displayed as waiting unless explicitly released.

Issues found and fixed by the driver author during review:

- Finalizer exceptions/timeouts previously skipped checking an available raw plan. The driver now retains/checks that plan and independently prevents shipping.
- Interrupted-checkpoint resumes previously omitted identity validation. They now validate configuration/input/source/audit/artifact bindings before preserving the interrupted failure.

Verification: `UV_CACHE_DIR=$PWD/.uv-cache uv run pytest -q experiments/mvp/refs_tools/test_small_v2_driver.py experiments/mvp/refs_tools/test_summarize_small_v2.py` — 13 passed.

Remaining limitation: a worker validation error on resume is turned into a generic failed checkpoint by the launcher, replacing the previous checkpoint metadata. Its denominator is retained, but prior metadata can be lost. Fresh immutable runs do not exercise this path; preserve the run folder before diagnostic/tamper tests. The source snapshot shares existing warm caches and parameters; cold-cache behavior remains unverified.

## Post-campaign amendment review

The ten-instance campaign records 8/10 full-checker passes and 8/10 shippable plans, with all ten attempts in denominators and ten bounds. The two failing feeder_800 cases are seed 28100009 (arrival ballast timeout) and seed 28100007 (constructor timeout); both carry first hard rule `iscode.loading.draft`. The parent reports ten report bindings validated and unchanged tiny-prefix bytes.

The producer's live v2.1 amendment relabelled the original ten inputs without changing their bytes. Reviewed the narrow epoch gate allowing `v2` or `v2.1`: frozen release hashes, input/audit hash validation, active status, accepted audits, C2 OFF and per-ship settings still gate selection. The driver regression fixture now uses `small-v2-release-frozen.json`, containing exactly the authorized ten small hashes. No new small instances are admitted by this amendment handling.

The pre-amendment producer driver is archived at `frozen-runtime-small-v2/producer-run_small_v2.py`. A lightweight SHA256 check confirms its bytes match the completed solver run manifest's recorded driver pin. This preserves historical provenance despite the subsequent selection-gate edit. New driver bytes require a fresh configuration/output rather than resuming the old campaign.

The report now renders waiting two-step result cells as missing values while retaining 0/10 attempts and ten unattempted cases. The displayed small solver row remains 8/10, mean weighted moves 20.25 over eight eligible plans. No blocker found in these post-campaign changes; no heavy jobs or repeated solver tests run during this review.
