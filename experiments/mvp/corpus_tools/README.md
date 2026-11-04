# MVP corpus

## D90 v2.1 amendment (29 Oct)

D90 adopts feeder_1700 small at slack0.35/mass fraction0.45. The original
25 test and three showcase instance/audit pairs remain byte-identical; v2.1
adds five balanced-family small tests and one small Laem Chabang showcase.
The full evaluator is30tests (15tiny+15small); showcases are three small calls
plus the unchanged full hero. `EVAL-V2-REPORT.json` records current hashes.

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 MKL_NUM_THREADS=1 uv run python experiments/mvp/corpus_tools/run_d90.py
```

This admitted wrapper freezes the amendment first, waits for the old wave to
stop at `d90-hold.json`, applies the inventory-only rebalance, refreshes/verifies
the manifest and resumes generation. Do not run two supervisors concurrently
on the same fitting inventory. `amend_v21.py` and `rebalance_v21.py` default to
read-only dry runs; `--execute` performs their separate steps.

Train quotas500/800/1700 are945/945/810; val quotas25/24/21 round the requested
35/35/30 mix. Existing completed shards and every tiny request are retained.
New1700 fitting seeds use31.1m/41.1m; test28,100,010–014, showcase29,000,200–299.
The same scoped V4 policy and full-checker requirements continue. D90 adopts
the measured5/8 feeder_1700 setting; no stronger future yield is asserted.

`d90-runtime.json` records the detached supervisor and its PIDs. A watcher writes
measured end counts/timing to `D90-FINAL-REPORT.json`. `D90-inventory-amendment.json`
in the corpus records protected-file hashes and requested per-ship counts.

## D84 qualified small release (leader GO 29 Oct 14:12 EDT)

Tiny is complete and accepted: 870 train + 48 val. Generate no tiny top-up.
Small uses the per-ship settings in `ship-settings-d84.json`: feeder_500 slack
0.25/mass fraction0.55; feeder_800 slack0.25/mass fraction0.45. Medium is excluded.
Five feeder_1700 small test slots and its small showcase remain explicitly pending.

The measured study requires `STOWAGE_BALLAST_V4` **unset during generation and
construction**, and `1` only for finalization; checker and replay restore the
unset environment. Audits record this workflow. A global V4 generation run is
not the qualified workflow. These synthetic demand settings reduce bookings;
they do not relax the checker.

One admitted command freezes evaluation first, then generates train/val:

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 MKL_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false uv run python experiments/mvp/corpus_tools/regenerate.py --ship-settings experiments/mvp/corpus_tools/ship-settings-d84.json --max-showcase-seeds 100 --workers 4 --execute
```

Use `--evaluation-only` for the evaluation publication step alone. The detached
`run_d84.py` wrapper queues evaluation on one free core, then admits small
generation in resumable waves on up to four free cores. It records owned PIDs,
resource estimates/actuals and its own ledger reservation. Sandbox priority
changes are denied and recorded. `d84-runtime.json` is the supervisor journal;
`EVAL-V2-REPORT.json` holds the frozen evaluation paths and hashes once published.

The actual v2 evaluation contains 15 preserved tiny +10 new small tests and
two new small Laem Chabang showcases plus the preserved full hero. Instance and
audit SHA lists live in `corpus/{test,showcase}/SHA256SUMS`. Deferred slots are
manifest rows, not fabricated instances; `release_ready` remains false until
the later amendment resolves them. Active qualified rows are usable meanwhile.

Small fitting requests are 2,700 train (1,350 per qualified ship) and70 val
(35 per ship), balanced across both families. Audits appear last as completion
markers under `corpus/{train,val}/small/`; train is emitted before val. Every
draw retains its own accepted producer/D58 gate and source hashes across fleet
edits. The gold worker must still pass the full checker for each loading plan.

## D79 restart: task 5.3

Small/medium generation with the original bookings is stopped. The tiny target
is 870 train + 48 validation, sized from `corpus/token_allocation.json` against
3,000 total examples. These are approximate token shares until checked gold
completion lengths exist. A later total-size decision may select a smaller
balanced subset.

Tiny generation uses an immutable copy of the switch-OFF source package in
`tiny_epoch/`, with every Python/JSON/TOML source hash recorded. Parameter inputs
remain bound by each producer audit. This avoids rejecting work whenever another
executive edits an opt-in constructor or booking setting. Existing producer and
D58 gates remain enabled. Qualified numerical caches retain their ordinary
checker validation; no pass is inferred from a cache hit.

```sh
uv run python experiments/mvp/corpus_tools/pin_tiny_epoch.py
# One single-core command per admitted shard; at most three concurrently.
PYTHONPATH=$PWD/experiments/mvp/corpus_tools/tiny_epoch/src:$PWD/src UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 TOKENIZERS_PARALLELISM=false uv run python experiments/mvp/corpus_tools/tiny_batch.py --shard-index 0
```

The runner requests nice=10 and records the effective priority. This sandbox
denies `setpriority`, so effective nice is 0 here; concurrency stays capped at
three. Child PIDs are recorded, and every instance/audit is a resumable shard.
The old `supervise.py` is retired and refuses to launch its original campaign.

The following D79 command is historical; the D84 instructions above supersede
its count/rung and per-ship settings:

```sh
nice -n 10 env UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 TOKENIZERS_PARALLELISM=false uv run python experiments/mvp/corpus_tools/regenerate.py --booking-slack 0.25 --execute --workers 3
```

Omit `--execute` for a read-only API/inventory check. Regeneration stages and
validates 30 new small/medium test instances and three Laem Chabang showcases,
archives v1, preserves all 15 tiny test and full-hero instance/audit bytes,
publishes v2 with rollback recovery, then generates non-tiny train/val. Use
`--evaluation-only` to stop after publishing test/showcase. New seed namespaces
are 28m test, 29m showcase, 30m train, 40m val; tiny retains its original seeds.
`releases.json` and the final manifest record the adopted setting and transition.

## Original v1 contract

`build.py` runs the current public random-yard adapter and simulated-yard producer
with the rule-freeze switch **OFF**. It refuses an active switch. D58 background
acceptance and the producer's normal necessary capacity screens stay enabled.
No plan is included or certified: the gold-plan/checker pipeline must still check
every loading plan before training or display.

The legacy `eval.splits` policy always holds out feeder_1700 medium. This corpus
uses `mvp-v1-seed-disjoint-all-ships-all-rungs`, recorded explicitly in its own
manifest. Consumers must use these split folders/manifest, rather than reassigning
them with the legacy policy.

Integration handoff: use `src/llm/stowlm/sft_data.py:build_mvp_example` and its
MVP dataset path (`mvp=True`), added during D79. They admit checker-bound plans
with a C2-OFF mask replay and treat this seed-disjoint corpus as split authority.
The separate production SFT path still requires the historical pinned v2
inventory; passing this manifest to that old loader is unsupported.

APPROXIMATION: train and test share ship/rung/generator families; root seeds are
disjoint. Results measure new draws in familiar families, not unseen-ship transfer.

Each instance is written using `stowage.io`, loaded back, and accompanied by
`.audit.json`. An audit is the shard completion marker. Interrupted instances
without an audit are regenerated. Audits preserve requested quantity, actual
quantity, producer rejections, D58 witness acceptance, source/parameter hashes,
component versions, timing and switch state. A source change stops generation;
workers must restart under the new source epoch. Optional numerical cache
inventories can grow during other jobs, so D58's own witness identity remains
separate from the source snapshot.

Only instance JSON files belong in a solver queue. Exclude `*.audit.json`,
`*.selection.json`, `*.failure.json`, `manifest.json`, and `*.requested.json`.
The top-level manifest provides the authoritative instance file list.

Read/verify commands (Python 3.12 via uv; CPU threads capped):

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 TOKENIZERS_PARALLELISM=false uv run python experiments/mvp/corpus_tools/measure_tokens.py --help
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 TOKENIZERS_PARALLELISM=false uv run python experiments/mvp/corpus_tools/freeze.py --help
```

`test/SHA256SUMS` and `showcase/SHA256SUMS` pin the frozen inputs and their audits.
Do not regenerate a frozen test or showcase in place. Showcase seed selection is
presentation-conditioned (DG, reefers, buried targets), with excluded candidates
reported; it must not be included in representative evaluation results. The
full feeder_1700 showcase is the full **ship geometry**, not a claim that 1,700 TEU
of cargo is loaded. Its actual cargo count/TEU is recorded.

Prompt token measurements use the local Glimmer tokenizer, without model weights.
They measure input prompts and a mandatory-placement completion lower bound
(the shortest encoding for placing each target once, plus end-of-plan). No
feasible plan is inferred. Full supervised token shares depend on checked gold
plans, whose completion lengths do not exist at corpus generation time.

The admitted 3,000-instance allocation is tiny 918, small 1,308, medium 774:
train 870/1,242/738 and validation 48/66/36. Every split/rung is balanced across
three ships and two generators. Frozen-test measurements predict token shares
7.98%/30.01%/62.01% using the stated lower-bound proxy.

```sh
uv run python experiments/mvp/corpus_tools/campaign.py --help
```
