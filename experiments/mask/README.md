# Feasibility mask: measurements (task 5.1)

## Review correction: completion and measured overhead

The historical measurements below do **not** establish zero overhead or production correctness. The worker-thread
change improved full-ship throughput by 44% in the recorded C2-off run, with 71.3 s of exposed host wait (17.5% of
the loop). The separate C2-on base-mask pilot reported a 52–62% host share. Those are different configurations;
neither supports a blanket zero-overhead claim. Current-engine GPU throughput needs a fresh qualified measurement.

Decoder `complete` means that the action sequence and end marker were emitted. Only ballast finalization followed
by the full checker, including D50 departure and arrival, determines whether that plan passes. The current CPU
integration regression and outcome records are documented in `fix-t5-integration.md`; historical completed plans
that failed the checker remain failures, not dead ends.

## Update, 28 Oct 03:10–04:00 ICT: engine v2.3 index arrays + prefetch

**What changed in the mask.**
- **The state is read through `legal_index()` / `legal_discharges()`; `legal_actions()` is never called.**
  - Heads come from `np.unique` over `flat // n_slots`, one per legal box.
  - A tail is that box's slice of flat indices. `decode_index` creates action objects only for the box the model has
    written.
  - Field token IDs are cached per (engine box index, engine slot index) from the same `action_fragments`.
  - `MaskConfig(index_arrays=False)` keeps the old `legal_actions()` build as the reference.
  - Verified: run on fresh instances with `legal_actions` monkey-patched to raise, and no call happened.
- **New `MaskedDecoder.prefetch()`.** Once one candidate is left under the prefix (typically the tier and newline
  remain), it pushes that action on an engine copy and builds the successor state's mask ahead of time.
  - Why: in lock-step decoding all rows reach an action boundary on the same step, so 64 engine evaluations land in
    one GPU window.
  - `generate_masked(prefetch=True)` (default) spends what is left of each step's estimated forward time
    (0.9 × min of the last 8 steps) on prefetches.
  - It never changes an allowed set (tested).

**Tests.** `tests/llm/test_mask.py` now has 66 tests (was 36), all passing:
- the index-array path equals the `legal_actions()` path, on every prefix, with lookahead 0 and 8, for stage A and
  layover fixtures;
- prefetch changes nothing.

GPU tests: 2/2 pass, with the index path and prefetch.

**Instances.** Fresh 3.5 generator (`generate_random_instance(rung, seed)`).
- C2 is still off: the linear model fails closed with "production linear policy epoch is stale; requalify".
- Unlike the v2 sets, the fresh instances are feasible at the root.
- At 04:00, fresh `medium` generation itself raised "cannot balance slice background" for seeds 1–5 (another
  executive's in-progress work), after the CPU runs below had finished.

### CPU: random sampling, index path (lookahead 0), `v23-*.jsonl`

| rung (loads) | samples | complete | dead end | mean dead-end depth | complete: replay legal and 0 static hard | mask ms/action p50 / p95 (engine included) | max legal set |
|---|---:|---:|---:|---:|---:|---:|---:|
| tiny (6) | 100 | 89 | 11 | 7.0 | 89/89 | 1.06 / 1.31 | 24 |
| small (20) | 100 | 19 | 81 | 32.7 | 19/19 | 1.45 / 1.99 | 252 |
| medium (75) | 60 | 2 | 58 | 166.7 | 2/2 | 2.95 / 5.14 | 4,280 |
| full (262) | 12 | 0 | 12 | 542 | – | 7.84 / 17.79 | 32,496 |

- **Old vs new path on the same seeds.** Trajectories are identical: 20/20 medium and 6/6 full. Total mask time,
  new / old: 10.6 / 10.1 s on medium, 29.9 / 32.7 s on full.
- **The engine's state evaluation dominates.** A cProfile of a full sample (566 actions, 8.6 s of mask time) puts
  `_evaluate` at 8.1 s (94%, mostly C1 `_c1`). The mask's own work is ~0.9 ms per action.
- So the index arrays remove object building but don't move the total, which is engine-bound: about 14 ms per new
  full state under load.
- Dead ends: `expert.e6.heavy_under_deck`, IMDG segregation and E1/E2/E8 combinations. 68% of small dead ends carry
  no rule ids; the engine's diagnostics leave them empty.
- **Full checker on completed tiny plans: 0/19 pass** (`v23-tiny-check.jsonl`). Ballast is `not_found` on all 19
  (finalize ~30 s each, which is the time limit). Violations:
  - `iscode.a2_3.weather`, `iscode.b2_3.containership_second_check`, `iscode.loading.draft`, `iscode.loading.trim`:
    19/19 each;
  - `iscode.loading.list`: 7/19.

  `checker.missing_vessel_data` is gone; the remaining failures are ballast and stability.

### GPU: index path, no prefetch (`gpu-v23.log`, fresh seed 1, base model, temperature 1)

| | batch | unmasked ms/step | masked ms/step | difference | masked tok/s |
|---|---:|---:|---:|---:|---:|
| medium (prompt 1,672) | 32 | 216.4 | 207.5 | −4.1% | 154 |
| medium | 64 | 274.2 | 262.1 | −4.4% | 244 |
| full (prompt 3,882) | 32 | 208.0 | 192.4 | −7.5% | 166 |
| full | 64 | 278.9 | 269.7 | −3.3% | 237 |

**Host share over whole generations, b64, 1,300-token cap** (the requested re-measurement):

| plans | loop s | host mask s | host share | step p50 / p95 ms | tok/s | outcome |
|---|---:|---:|---:|---:|---:|---|
| medium, v2.0 mask on sim.medium.0 (27 Oct) | 469.9 | 93.8 | 20% | 293 / 780 | 177 | 62 cap, 2 dead ends |
| **medium, index path, fresh seed 1** | 429.1 | 61.7 | **14.4%** | 267 / 707 | 158 | 61 dead ends (mean depth 151), 3 cap |
| **full, index path, fresh seed 1** | 581.2 | 185.0 | **31.8%** | 293 / 1,277 | 143 | 64 cap (mean depth 185) |

- The instances differ (v2.0 row: an older instance set), so the medium drop is indicative, not a paired measurement.
- p95 steps of 0.7–1.3 s are the boundary bursts: all 64 rows need a new engine state on the same step.
- At 03:29 stow-gpu's ledger reservation began, and its 6.2 job overlapped the end of this run. Step times in the
  last minutes may be slightly inflated.

### Prefetch + host worker thread: GPU A/B (28 Oct, 17:33–17:54 EDT)

**Finding first: the host mask work was never overlapped with the GPU.** A probe at b64 on a 3.8k-token prompt
shows that `mx.async_eval` **blocks for ~270 ms** of the ~290 ms forward pass; only ~22 ms remain after it returns.
So in the 27 Oct and early-28 Oct runs, every millisecond of host mask work beyond ~22 ms added to the step time.
The earlier claim that "the host work runs during the GPU forward pass" was **wrong**, and the prefetch deadline
(measured from step start) always expired: `prefetched: 0` in `gpu-v23-prefetch.log`.

The same probe shows that `async_eval` **releases the GIL**: a Python thread kept ~84% of its solo speed during the
block. So `generate_masked` now runs the host mask work in a **worker thread during the forward pass**:
- first the allowed sets that every row needs;
- then `prefetch()` of determined actions until the forward returns.

`GenerationStats.host_wait_s` is the time the main thread still waits for that worker: the exposed stall. Sampling
is unchanged; the GPU tests pass, including the log-prob consistency check.

All rows below are full fresh seed 1 (`random.full.1.0.3`), b64, 1,300-token cap, temperature 1, seed 7; every row
ended at the cap with mean depth 185.

| run | loop s | exposed host time | share of loop | step p50 / p95 ms | tok/s | prefetched |
|---|---:|---:|---:|---:|---:|---:|
| no prefetch (`gpu-v23.log`) | 581.2 | 185.0 (host mask) | **31.8%** | 293 / 1,277 | 143 | – |
| prefetch, deadline bug (`gpu-v23-prefetch.log`) | 590.0 | 208.1 (host mask) | 35.3% | 302 / 1,482 | 141 | 0 |
| **worker thread + prefetch** (`gpu-v23-thread.log`) | **407.1** | **71.3** (`host_wait_s`) | **17.5%** | 279 / 683 | **204** | 5,895 |

- Loop time −30%, throughput +44%, step p95 −47%.
- The remaining 17.5% stall comes from boundary steps where the 64 new engine states (~14 ms each, engine C1) don't
  fit in one forward pass even in parallel.
- Next levers (engine side): faster `_evaluate`/C1, or engines in worker processes.
- Medium couldn't be re-run: fresh medium generation raised "cannot balance slice background" for seeds 1–5 at
  17:33 EDT.

The CPU lock-step simulation (`simulate_lockstep.py`, `simulate_lockstep.jsonl`; +15.0% → +2.1% extra time on
full b64) assumed full overlap. It is superseded by the GPU result above.

---

# Original measurements (27 Oct, mask v1 on engine v2.0)

Measured 27 Oct 2026, 18:20–19:30 ICT, on the shared M5 Max. Machine load average was 6–56 during the runs, because
other executives' jobs were running too. Code: `src/llm/stowlm/mask.py` (framework-neutral) and
`src/llm/stowlm/mask_mlx.py` (MLX). Raw data: `cpu-*.jsonl`, `gpu-plans-v20.jsonl`, `gpu.log`, `gpu-plans-check-v20.json`.
Tables: `uv run python experiments/mask/tables.py`.

> **Three caveats apply to every number here.**
> 1. **The engine ran with C2 off** (`global_screen: 0`). The 3.7 linear model can't currently be built for the v2
>    KCS hulls. On v2 tiny, engine construction took 286 s and then failed closed: "validity-band sweep accepted only 0
>    of 64 required conditions … no condition reached equilibrium". So stability and strength are not screened by the
>    mask.
> 2. **v2 instances beyond tiny dead-end at depth 0** in the engine (C2 off). Root dead ends:
>    - small: 165/200 random-yard, 169/200 simulated-yard (rule ids `expert.e2.weight_order`, `expert.physical.slot_occupancy`);
>    - medium: 100/100 and 100/100 (same rule ids);
>    - full: 40/40 and 40/40 (`expert.physical.size_fit`, `expert.physical.slot_occupancy`);
>    - tiny: 11/50 and 19/50.
>
>    So small, medium and full were measured on the older **`experiments/instances-sim`** set (no DG, all
>    root-feasible): small 50, medium 20, full 5 instances.
> 3. **No plan from any source can pass the full checker on these instances today.** Every completed plan fails
>    with `checker.missing_vessel_data`, and the 4.2 ballast finisher returns `not_found`.

## What was built

- **`MaskedDecoder`** (framework-neutral, one per sequence): it maps (state, tokens written so far) to the sorted
  allowed next token IDs.
  - It is driven by `legal_actions()` and the 4.5 codec.
  - The trie is built per field: an eager trie over action *heads* (op + box index + separator, one entry per box),
    then a lazy tail trie over that box's slots. A tail is built only when its head has been written, so the mask
    never materialises all box × slot strings.
  - The per-state structures sit on a stack that follows push/pop, so re-reading the mask on a visited state is free.
  - The expensive build (engine `legal_actions` + head trie) is lazy. It happens in `allowed_token_ids()`, which the
    batched loop calls while the GPU runs.
  - Helpers: `allowed_token_ids(engine, codec, partial)` (one-shot form), `allowed_sets(...)` (deterministic replay,
    for D9 log-probs), `random_masked_plan(...)` and `pop_action()` (CPU search).
- **Dead ends.** When no token is allowed, the decoder sets `status="dead_end"` and keeps the engine's `DeadEnd`. The
  partial plan for repair (5.7) is `decoder.actions`: complete actions only.
  - Optional one-action lookahead (`MaskConfig.lookahead=k`): once ≤ k candidates remain under the prefix, candidates
    whose successor state is a dead end are removed. The rule is a pure function of (state, prefix, k).
  - **It is off by default** (see the measurements below).
- **`mask_mlx`**:
  - `generate_masked`: a lock-step batched loop. The prompt is prefilled once and its KV cache tiled to the batch.
    (Superseded 28 Oct: `mx.async_eval` blocks, so this did not overlap; see the update at the top.) Allowed logits are gathered into a small
    (batch × allowed) matrix and sampled there. Finished and dead-end rows are dropped from the cache.
  - `masked_logprobs`: teacher-forced log-softmax over the allowed set (D9).
  - `MaskLogitsProcessor` for mlx-lm `generate_step` at batch 1.
  - `generate_masked(decoders=None)` runs the same loop unmasked (greedy over the full vocabulary) as the baseline.

### Why no multi-action backtracking during LM decoding

A token that has been fed sits in the KV cache. In lock-step batched decoding, one row can't be rewound without
re-prefilling it. Two measurements say rewinding wouldn't pay anyway:
- 4.4's DFS with 200 pops never escaped a dead end; the mistake is made earlier.
- Here, a one-action lookahead (equivalent to "back up the last action and resample it") did not change the
  dead-end rate or depth (table below).

So a dead end is reported and handed to repair. `pop_action()` exists for CPU search.

## Correctness tests

- **CPU** (`tests/llm/test_mask.py`, 36 tests, default suite):
  - with lookahead 0, the allowed set equals the 4.5 `TokenTrie` over `legal_actions()` at every prefix of random
    walks (stage A and layover fixtures, so `D` discharge tokens are covered);
  - every completed action's tokens equal `encode_action`;
  - completed plans decode canonically and replay legally;
  - dead ends are reported;
  - determinism and replay;
  - lookahead successors are live;
  - `pop_action`, and `MaskError` on a disallowed token.
- **GPU** (`tests/llm/test_mask_mlx.py`, marked `gpu` + `slow`, 2 tests, 19.7 s including model load):
  - batched masked generation on v2 tiny (every token is inside the replayed mask, statuses valid);
  - recomputed `masked_logprobs` match the sampled ones within 0.25 nats (batched bf16 decode vs one teacher-forced
    pass);
  - the unmasked baseline;
  - the batch-1 logits processor through mlx-lm `generate_step`.

## Random sampling under the mask (CPU, uniform over allowed tokens)

The sampler draws one token at a time, uniformly from the allowed set (this is not uniform over actions). Per-sample
cap: 120 s (medium) and 300 s (full).

| rung | source | lookahead | samples | complete | dead end | timeout | mean depth at dead end | complete plans: replay legal and 0 static hard | mask ms per action p50 / p95 | engine share of mask time |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| tiny (6) | v2 random-yard | 0 | 50 | 39 | 11 | 0 | 0.5 | 39/39 | 0.91 / 1.07 | 84% |
| tiny (6) | v2 random-yard | 8 | 50 | 39 | 11 | 0 | 0.4 | 39/39 | 3.11 / 3.53 | 87% |
| tiny (6) | v2 simulated-yard | 0 | 50 | 31 | 19 | 0 | 0.0 | 31/31 | 0.83 / 1.07 | 84% |
| tiny (6) | v2 simulated-yard | 8 | 50 | 31 | 19 | 0 | 0.0 | 31/31 | 3.06 / 3.67 | 83% |
| small (36) | sim | 0 | 100 | 0 | 100 | 0 | 65.0 | – | 1.34 / 8.44 | 84% |
| small (36) | sim | 8 | 100 | 0 | 100 | 0 | 65.5 | – | 4.92 / 29.02 | 89% |
| medium (150) | sim | 0 | 100 | 0 | 88 | 12 | 190.3 | – | 21.08 / 1355 | 99% |
| medium (150) | sim | 8 | 20 | 0 | 20 | 0 | 195.8 | – | 18.43 / 96.73 | 96% |
| full (280) | sim | 0 | 20 | 0 | 20 | 0 | 416.8 | – | 9.89 / 25.98 | 84% |

"Mask ms per action" is all mask work per committed action (allowed sets, trie walks, advancing), including the
engine's `legal_actions`. "Engine share" is the part spent inside the engine. Depth counts actions, including
restows, so it can exceed the box count.

**Result against "Done when", part 1** (random sampling under the mask always yields plans that pass 4.1, or reports a dead end):
- **Every one of the 690 CPU samples** ended in one of three ways:
  - complete: 140, all replaying legally through 2.3 with 0 hard 2.4 static violations;
  - a reported dead end with a `DeadEnd` (rule ids, depth): 538;
  - the wall cap, on 12 medium samples whose engine states took seconds.
- No sample ever produced a token that was outside the mask, mis-decoded, or failed to push.
- **The full checker 4.1 passes 0/23 completed tiny plans** (`cpu-tiny-check.jsonl`, finalize + check). The finisher
  returns `not_found` on all 23. The hard violations are:
  - `checker.missing_vessel_data`: 23/23;
  - `iscode.b3_1.free_surface`: 15/23.

  `missing_vessel_data` is a property of the vessel record, not of the plan, so this criterion **can't be met until
  the fleet data and the ballast finisher work on v2 hulls** (not mask work).
- **Completion is the real bottleneck:**
  - uniform sampling never completes small/medium/full;
  - dead ends are 94–100% `expert.e2.weight_order` + `expert.physical.slot_occupancy` (D25 weight classes; 4.4 found
    the same mechanism);
  - lookahead 8 costs 3–4× the mask time and changes neither the rate nor the depth, hence the default of 0.
- **Engine latency spikes.** Some states take **0.9–4.1 s** in `legal_actions` (medium p95 per action: 1.36 s; one
  medium sample: 3–30 s per lookahead step; full max 0.92 s), against the 4.4 target of p95 ≤ 5 ms. The spikes come
  after forced hatch restows. This is engine work (4.4), not mask work.
- The mask's own overhead on top of the engine is **0.1–0.4 ms per action** (16% of 0.9–1.3 ms on tiny/small).
  Within-action trie steps cost ~1–20 µs (p95 0.018 ms on medium).

## GPU: masked vs unmasked step time (base Glimmer 30B 4-bit, untuned)

Setup:
- 48 steps after a 4-step warm-up, on one instance per rung, with lookahead 0;
- baseline = the same loop unmasked with greedy argmax over all 202k logits;
- masked = temperature 1 under the mask;
- the median ms/step is taken over the steps where the masked batch is still full.

Load average was 15–56 during this run, so expect about ±15% run-to-run noise.

| rung | prompt tokens | batch | unmasked ms/step | masked ms/step | masked − unmasked | masked tok/s |
|---|---:|---:|---:|---:|---:|---:|
| tiny | 509 | 1 | 65.9 | 59.6 | −9.6% | 16.8 |
| tiny | 509 | 32 | 212.1 | 205.5 | −3.1% | 155.7 |
| tiny | 509 | 64 | 255.9 | 249.9 | −2.3% | 256.1 |
| small | 868 | 1 | 83.3 | 55.6 | −33.3% | 18.0 |
| small | 868 | 32 | 222.9 | 218.4 | −2.0% | 146.5 |
| small | 868 | 64 | 289.2 | 279.5 | −3.4% | 229.0 |
| medium | 1,827 | 1 | 55.4 | 56.1 | +1.4% | 17.8 |
| medium | 1,827 | 32 | 234.9 | 230.2 | −2.0% | 139.0 |
| medium | 1,827 | 64 | 319.7 | 278.6 | −12.9% | 229.7 |
| full | 2,853 | 1 | 44.2 | 42.8 | −3.3% | 23.4 |
| full | 2,853 | 32 | 226.0 | 184.9 | −18.2% | 173.1 |
| full | 2,853 | 64 | 312.7 | 312.1 | −0.2% | 205.1 |

**Result against "Done when", part 2** (per-step overhead and batched throughput are measured):
- **The selected typical-step medians below did not resolve a slowdown at batch 1/32/64.** This is not an
  end-to-end zero-overhead result; boundary-step stalls and whole-generation host shares are reported separately.
  The original explanation was:
  - (superseded 28 Oct: this did not actually overlap; `mx.async_eval` blocks; see the update at the top);
  - sampling from the gathered allowed logits is cheaper than reducing over 202k logits.
- Batched throughput is **~140–175 tok/s at b32 and ~205–255 tok/s at b64**. That is in line with `docs/compute.md`
  (125 / 200 sustained).
- Over whole generations the host share grows, because engine states with spikes stall steps:

| plans run | batch | loop s | host mask s | steps | p50 / p95 ms/step | tok/s |
|---|---:|---:|---:|---:|---:|---:|
| tiny ×4 instances (complete in 43 steps) | 32 | 9.5 each | 0.32–0.38 (3.5%) | 43 | 218 / 274 | 143 |
| small sim.small.0 | 64 | 156.3 | 10.4 (6.6%) | 581 | 276 / 436 | 166 |
| small sim.small.1 | 64 | 176.9 | 10.1 (5.7%) | 600 | 276 / 408 | 166 |
| medium sim.medium.0 | 64 | 469.9 | 93.8 (20%) | 1,300 (cap) | 293 / 780 | 177 |
| full sim.full.0 | 32 | – | – | – | – | – |

The full-ship plan batch (b32, 700-token cap) **was stopped by the executive after ~25 min**. It was host-bound on
multi-second engine states, the GPU job had passed its 30-min cap, and stow-gpu was queued. Its overhead rows above
are complete; its plan results were not written. On full, the mask cost is dominated by `legal_actions` spikes. Fix
those in 4.4 (or run engines in worker processes) before long full-ship rollouts.

### Plans written by the base model under the mask

`gpu-plans-v20.jsonl`, temperature 1, seed 7. `check_plans.py` replays every plan's tokens through `allowed_sets`.

| rung | plans | complete | dead end | hit max_tokens | mean actions |
|---|---:|---:|---:|---:|---:|
| tiny (v2, 4 instances × 32) | 128 | 96 | 32 (all on the one root-dead-end instance, depth 0) | 0 | 4.5 |
| small (sim, 2 × 64) | 128 | 0 | 123 (depth 44–76) | 5 | 61.8 |
| medium (sim, 1 × 64, cap 1,300 tokens) | 64 | 0 | 2 (depth 168, 176) | 62 | 184.6 |

On medium the untuned base model reaches a mean depth of 185 actions under a 1,300-token cap, with only 2/64 dead
ends. Uniform-random sampling under the same mask dead-ends at a mean depth of 190, 88/100 of the time. A completion
rate for medium needs a cap of ≥ 2,500 tokens: **not measured**.

`check_plans.py` results (`gpu-plans-check-v20.json`):
- **all 320 sampled token sequences lie inside the replayed mask**;
- all 96 completed tiny plans replay legally with 0 hard static violations;
- the full checker passes **0/4** checked: ballast `not_found`, with `checker.missing_vessel_data` (caveat 3 at the top).

## Reproduce

```sh
./experiments/mask/run_cpu.sh                                     # CPU part, 4 workers, ~20 min on a loaded Mac
uv run python experiments/mask/measure_gpu.py --out experiments/mask/gpu.json   # GPU: add a ledger line first
uv run python experiments/mask/check_plans.py --full 4            # CPU check of the GPU plans
uv run python experiments/mask/tables.py
uv run pytest -q tests/llm/test_mask.py; uv run pytest -q -m gpu tests/llm/test_mask_mlx.py
```

## Compute used

| job | estimate | actual |
|---|---|---|
| GPU tests | 1 core / 24 GB / GPU / 5 min | 19.7 s |
| GPU pilot | 1 core / 30 GB / GPU / 5 min | 71 s, MLX peak 19.9 GB |
| GPU measure | 1 core / 32 GB / GPU / 25 min | stopped at 45.6 min (the full-ship plan batch was host-bound); MLX peak 24.1 GB |
| CPU sampling | 4 cores / 3 GB / 20 min | first try killed at ~25 min (lookahead-8 stalls); rerun ~20 min; ≤ 1 GB per worker |
| plan check | 1 core | ~6 min |

Everything is logged in `experiments/fleet/history.md`. No cloud spend.
