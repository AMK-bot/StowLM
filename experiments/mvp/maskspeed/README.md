# D111.4 CPU mask replay

Final result: both pre-/post-D115 separate-process gates passed 214 full traces
and 25,454 prefixes with zero differences. Full CPU suite: 569 passed, four GPU
cases excluded. See [REPORT.md](REPORT.md), [MEASUREMENTS.md](MEASUREMENTS.md),
and `out/final2-audit.json`. D115 securing-only changes are applied; MLX/planner
decode-summary hooks remain an out-of-fence owner handoff.

The takeover's release gate is `llm.maskspeed.crossprocess_guard`: old and new
implementations run in separate persistent processes, with only one active CPU
worker at a time. Exact legal-token lists, committed actions, statuses, complete
tokens and final expanded plans are compared as events arrive. Its corpus covers
generated tiny fixtures plus tiny/small TRAIN and VAL maps; test-v2.1 inputs are
excluded. Baseline provenance is in `out/baseline2-verification.json`.

```sh
PYTHONHASHSEED=0 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false \
RAYON_NUM_THREADS=1 .venv/bin/python -m llm.maskspeed.crossprocess_guard \
  --tiny 200 --maps 3 --saved-per-status 1 \
  --output experiments/mvp/maskspeed/out/guard2-final.json
```

`llm.maskspeed.timing2` runs one timing arm per process, checkpoints every map/B
result, verifies source and input stability, and rejects different or truncated
old/new trajectories when summarizing. Run it only after the gate passes:

```sh
PYTHONHASHSEED=0 .venv/bin/python -m llm.maskspeed.timing2 --arm before \
  --output experiments/mvp/maskspeed/out/timing2-full-before.json
PYTHONHASHSEED=0 .venv/bin/python -m llm.maskspeed.timing2 --arm after \
  --output experiments/mvp/maskspeed/out/timing2-full-after.json
.venv/bin/python -m llm.maskspeed.timing2 --summarize \
  experiments/mvp/maskspeed/out/timing2-full-before.json \
  experiments/mvp/maskspeed/out/timing2-full-after.json \
  --output experiments/mvp/maskspeed/out/timing2-full-summary.json
```

The older same-process harness below remains useful for diagnostics and unit
tests; its incomplete archived guard runs do not establish the release gate.

The frozen `baseline/mask.py` and `baseline/securing_mask_filter.py` are loaded
under isolated Python module names. The frozen lookahead wrapper isolates the
old arm; unchanged bounds, support policy and native feasibility/checker code are
shared by both arms. The frozen support-policy file is retained for proposal
patch testing, not imported by the timing or replay arms. No MLX, model weights, GPU, checker acceptance
or approximation is involved. The tokenizer is the locally cached production
tokenizer loaded by `llm.stowlm.format.load_tokenizer`.

Run from the repository root, with one worker per process:

```sh
PYTHONHASHSEED=0 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
  .venv/bin/python -m llm.maskspeed.replay --mode guard \
  --output experiments/mvp/maskspeed/out/guard.json
```

The default guard deduplicates 174 saved model decodes from the speed sweep
(95 complete, 79 dead ends) and supplements them with 32 deterministic tiny
trajectories. Tiny trajectories include partial-action pop/rebranch, successor
prefetch and immediate-successor lookahead. Every queried prefix compares the
ordered legal-token tuple, committed actions and status; final tokens and actions
are compared and complete plans are serialized and compared in full. Each output
records the trace source, deterministic seed via its ID, legal-set digest, and
step count. A mismatch raises immediately. Counts refer to trajectories, not
prefixes. `--saved` and `--tiny` permit smoke runs; `--shards 2 --shard-index 0`
and index `1` partition the full list without overlap if two cores are available.
If a historical saved stream is rejected equally by both pinned baseline and new
arms, it is recorded in `unreplayable` and its partial prefix is not counted as a
guard trace. A deterministic additional tiny seed replaces it, preserving the
requested completed-trajectory count. Any old/new legal-set mismatch still fails
immediately; this classification does not suppress implementation differences.

Whole decode CPU timing on six small training maps, covering three feeder classes:

```sh
PYTHONHASHSEED=0 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
  .venv/bin/python -m llm.maskspeed.replay --mode benchmark \
  --actions 1000 --batches 1,8 --cold-codec \
  --output experiments/mvp/maskspeed/out/benchmark.json
```

The CLI requires the interpreter to start with `PYTHONHASHSEED=0`; setting the
environment variable after startup cannot pin the current interpreter. Archived
unpinned phase-1 outputs are diagnostics and do not qualify as the final gate.

The first pass measures raw allowed-set query and advance CPU (`process_time`)
and wall (`perf_counter`) times. A second pass
replays the identical seeded rows with nested layer timers; it asserts identical
final action sequences. Per-layer **exclusive** time attributes production engine,
lookahead, securing, and token-mask work without double-counting nested calls.
Inclusive time includes inner layers. Layer timings default to `process_time`;
`--layer-clock wall` explicitly selects wall attribution. Instrumentation overhead
is visible as `instrumented_wall_s` versus `wall_s` and `instrumented_cpu_s`
versus `cpu_s`; raw full-stack timing comes only from the
uninstrumented pass. B=8 is the sequential host row loop used by `mask_mlx`, with
shared immutable screen/capacity caches and independent mutable row state. Engine
construction, tokenizer load, model/GPU time and token-index matrix creation in
`mask_mlx` are excluded. End/dead rows leave the active batch. The normal benchmark
reuses one codec for the same map across batches/passes; its B=8 plain pass may
inherit a warm static token memo from B=1. Use `--cold-codec` for a fresh codec at
each batch and timing pass, or run B=8 alone in a fresh process for cold raw B=8.

Use `--profile` only for hotspot investigation; those outputs include cProfile
overhead in their per-layer attribution. Source hashes include mask, securing,
lookahead, baseline, checker, feasibility, securing calculation, and fleet/cache
sources. A run fails its stability check if any changes during measurement.
