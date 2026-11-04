# Task 5.3 restart handoff — 29 October 2026

Tiny batch complete: **870 train + 48 val**, all 918 producer gates and D58 background certificates passed. Each ship/family row has 145 train and 8 val. C2 screened=false, switch OFF. Inputs are loadable with stowage.io; no loading-plan approval is claimed.

15 tiny tests and the full feeder_1700 hero remain byte-frozen, including audit pins. Active manifest counts: {"showcase": 1, "test": 15, "train": 870, "val": 48}. Retained old small/medium inputs are inactive pending v2. No small/medium generation was run after restart.

| Rung / phase | Instances | Mean producer seconds |
|---|---:|---:|
| Tiny train/val | 918 | 14.93 |
| Small v1 test reference; v2 pending | 15 | 11.58 |
| Medium v1 test reference; v2 pending | 15 | 30.60 |

D58 mean on tiny: 10.03s. Generation wall: 86.14min. Estimate was <=100min, 3 cores / 6GB / no GPU; observed maximum worker RSS 1.80GB, sum of worker peaks 3.83GB (upper bound for workers, not simultaneous aggregate measurement). Shards finished in 62.99/72.29/86.14min; reservations released as each ended. Prototype: ~160s discarded on source change; six-case pilot347.37s including five epoch retries. Final freeze180.32s + verify193.64s, versus <=8min QA estimate.

Files changed: corpus/train/tiny/**, val/tiny/**, manifest.json, releases.json and checksum metadata; corpus_tools/build.py, campaign.py, freeze.py, showcase.py, regenerate.py, tiny_batch.py, pin_tiny_epoch.py, summarize_tiny.py, status.py, retired supervise.py, scoped tests, README, tiny_epoch/** and evidence logs. Production src/** and tests/** were not edited by this executive during restart. No generator fixes.

Verification (native threads capped at one; UV_CACHE_DIR=$PWD/.uv-cache):

- `uv run pytest experiments/mvp/corpus_tools/test_campaign.py experiments/mvp/corpus_tools/test_regenerate.py experiments/mvp/corpus_tools/test_freeze_v2.py tests/generate/test_mvp_corpus_tools.py -q`: 28 passed, exit0; restart-tests.log.
- `uv run python experiments/mvp/corpus_tools/summarize_tiny.py`: exact counts/balance, all918 gates, source-pin integrity and three successful shard exits; tiny-summary.json.
- `/usr/bin/time -p uv run python experiments/mvp/corpus_tools/freeze.py --partial`: pass180.32s; freeze.log.
- `/usr/bin/time -p uv run python experiments/mvp/corpus_tools/freeze.py --verify`: pass193.64s; verify.log.
- `uv run python experiments/mvp/corpus_tools/regenerate.py --booking-slack 0.25 --dry-run`: pass; API available, balanced45test, 15tiny preserved, disjoint28m/29m replacement seeds. 0.25 is only an example, not adopted.

After leader GO and compute admission, one command regenerates test/showcase v2 first, then small/medium train/val:

```sh
nice -n 10 env UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 TOKENIZERS_PARALLELISM=false uv run python experiments/mvp/corpus_tools/regenerate.py --booking-slack ADOPTED_FRACTION --execute --workers 3
```

It stages/validates 30 new tests and three Laem Chabang DG/reefer/buried-yard showcases, archives v1, preserves the 15tiny tests/fullhero, publishes with rollback/recovery, records setting and v1->v2 transition, then resumes fitting. New test/showcase/train/val namespaces28m/29m/30m/40m are disjoint from retained tiny seeds. `--evaluation-only` stops after test/showcase.

APPROXIMATION: 918 tiny counts target7.98% of the original3000-instance prompt+mandatory-placement token proxy; actual full SFT shares and any smaller total await gold/5.9. A smaller balanced subset remains possible.

APPROXIMATION: 878 tiny inputs use a pinned D79 source epoch against concurrent opt-in edits, with all source/parameter hashes retained;40 earlier-epoch tiny inputs are retained. Live-source drift is reported honestly. Current full checker and D79 mask replay remain required for gold admission, via the new build_mvp_example / mvp=True path.

APPROXIMATION: familiar ship/family splits and presentation-conditioned showcase selection retain their documented v1 scope. No unseen-ship claim.

Limitation: sandbox rejected nice/setpriority (effective nice0) and an attempted SIGSTOP of an owned recorded PID. Configured generation concurrency stayed<=3, then2, then1. No foreign PID was signalled.

Pending: leader GO with the adopted setting; small/medium v2 is intentionally unrun. Proposed plan status:5.3 tiny tranche + regeneration tooling done; v2 remaining pending4.7 adoption.
