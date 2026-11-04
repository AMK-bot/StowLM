# Corpus 5.3 — D84 small regeneration

Leader GO: 29 Oct14:12 EDT. Tiny remains870 train/48val, with no top-up.
Qualified small settings: feeder_500 slack0.25/mass0.55;
feeder_800 slack0.25/mass0.45. Medium excluded. Feeder_1700 pending.

Tooling changed: build.py, campaign.py, regenerate.py, showcase.py, freeze.py,
README.md, ship-settings-d84.json; new resumable small_batch.py and detached
run_d84.py and report_d84.py. Tests: test_settings_d84.py, test_small_batch.py, test_run_d84.py,
test_regenerate.py and test_freeze_v2.py. No src changes.

Verification command:

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 MKL_NUM_THREADS=1 uv run pytest experiments/mvp/corpus_tools/test_small_batch.py experiments/mvp/corpus_tools/test_run_d84.py experiments/mvp/corpus_tools/test_settings_d84.py experiments/mvp/corpus_tools/test_campaign.py experiments/mvp/corpus_tools/test_regenerate.py experiments/mvp/corpus_tools/test_freeze_v2.py tests/generate/test_mvp_corpus_tools.py -o addopts='' -q
```

Result:44 passed in1.35s. Read-only regeneration
dry run exits0, both producer APIs accept per-ship mass settings.

The bounded showcase search was extended from10 to100 after the first10
feeder_500 draws lacked combined features.
Selected seeds29000022 (500:13targets,1DG,1reefer,9buried) and29000102
(800:18targets,1DG,1reefer,13buried). Selection rejections remain logged.

Execution and progress: d84-runtime.json; supervisor and phase logs are adjacent.
EVAL-V2-REPORT.json is written only after evaluation is published and verified.
The intended active evaluation is25 tests (15tiny+10small) and3showcases
(two small calls+preserved full hero). Pending rows represent five1700small
tests plus one1700small showcase. Fitting requests2700smalltrain/70smallval.
Actual emitted counts and timing must be read from the runtime/final manifest.

Evaluation frozen and independently verified18:23:44Z (14:23:44 EDT), exit0.
Test SHA-list digest ae0c8c6646781aadc2b8e652f915855ae31e56973ddc506dbfef68ad95b36af4;
showcase digest2d6f1300419c75dfc61d53347cfced65feca1bb83df663c3a7c6bedfe5bca37f.
All per-instance hashes: EVAL-V2-REPORT.json. New test mean12.362s/instance,
max reported test-workerRSS825704448B. Successful evaluation phase550.521s
against<=35min estimate; earlier source-drift attempt34.114s and initial
feature-search exhaustion196.948s retained. No generator fixes.

Detached supervisor19081 and reporter21653 own all future child PIDs in their
journals. Reporter writes D84-FINAL-REPORT.json with actual end counts/timing
after complete or failed status; generation completion is not claimed now.
The bulk pilot initially scaled warm test timings to~2.76h at4cores; first
fresh-process fitting draws take~19–23s end-to-end, suggesting~3.9h at4cores
before contention. Actual admission currently1core because others reserve15.

ASSUMPTION: reproduce the qualified study's staged environment: V4 unset for
generation/construction, V4=1 for later plan finalization, unset for checker and
mask replay. Global V4 generation is not qualified; its study probe failed8/8.

APPROXIMATION: lighter synthetic booking distributions replace the former full
demand distribution. Hard checker rules remain unchanged. No generated input is
a certified loading plan.

APPROXIMATION: retain completed, same-config accepted shards across fleet source
edits with their original source pins and D58 evidence. New draws import fresh
sources; source changes interrupt and retry the affected draw. No source epoch
is relabelled. Optional strict-current-source verification can reject drift.

Compute: native threads1, noGPU/cloud; own ledger/history contain each admitted
phase. Evaluation estimate1CPU/2GB/<=15min from measured study~20s/draw and prior
6.23min rootQA. Small bulk estimate is recalculated from actual frozen tests,
then admitted in32-instance waves on1–4 free cores,2GB reserved per core.
Priority requestnice10 is denied by sandbox; effective0 is recorded. Initial
evaluation child17722 exited1 after34.114s on source drift; accepted shard kept,
reservation released, supervisor17981 retries fresh source imports.
