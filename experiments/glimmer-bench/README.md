# Muse Glimmer 30B on the M5 Max — speed and memory (26 Oct 2026)

Model: `meta-models/Muse-Glimmer-30B` (snapshot `cec94fc`), text tower only, converted with `convert.py` to MLX 4-bit
(group size 64 → 4.501 bits/weight, 15 GB on disk) → `models/glimmer-30b-4bit` (git-ignored). mlx 0.32.4, mlx-lm 0.32.2.
Download: 59.6 GB in 19.4 min (~51 MB/s, unauthenticated).

## Inference (`mlx_lm.generate`, system prompt `Reasoning strength: low`)

| Prompt | Prefill | Generation | Peak memory |
|---|---|---|---|
| 55 tokens | 73 tok/s (too short to mean anything) | **33.8 tok/s** (336 tokens) | 15.8 GB |
| 8,976 tokens | **868 tok/s** (~10 s) | **30.8 tok/s** (1,000 tokens) | 16.7 GB |

Output was coherent and correct on one restow question. This is anecdotal evidence only; numerical
conversion correctness and systematic quality remain unverified. Glimmer writes a short
analysis channel before its answer even at `low`; StowLM's adapter will be trained to emit actions directly.

3-bit vs 4-bit (same short prompt, `convert.py OUT 3`): 3-bit is 11 GB on disk (3.5 bits/weight), **39.3 tok/s vs
33.3**, 12.4 GB vs 15.8 GB peak. Answer still correct but slightly less precise (said a restow is "an extra crane move";
4-bit said "at least two"). One prompt is an anecdote, not a quality measurement.

## QLoRA training (`mlx_lm.lora`, rank 8, Adam; data from `make_data.py` — random, so loss is meaningless)

| Setting | Trainable | Throughput | Step time | Peak memory |
|---|---|---|---|---|
| seq 1,024 · batch 4 · last 16 layers | 16.1M | **~352 tok/s** | ~11.6 s | not captured (≤ C) |
| seq 4,096 · batch 1 · last 16 layers | 16.1M | **~300–320 tok/s** | ~13 s | not captured (≤ C) |
| seq 4,096 · batch 1 · all 52 layers · grad checkpoint | 52.4M | **~130 tok/s** | ~31 s | **28.3 GB** (MLX peak counter) |

## What it means

- **SFT stays local** (no Modal needed). Example: 3,000 gold plans × ~2,000 tokens = 6M tokens → ~5 h per epoch on
  16 layers, ~13 h on all layers. Booking LLM similar scale → ~3 h. Overnight runs, not interactive.
- **Memory is not a constraint** (≤ 28 GB of 128 GB): two jobs fit at once, but they'd share the GPU, so no speed-up.
- **Generation speed is the constraint for the demo.** The draft action format costs ~33 tokens/action; a 150-action
  plan would take ~160 s. Target ≤ 8–10 tokens/action (≤ ~1,000 tokens/plan → ~30 s). Day 5 format design must do this.
- Bug found: mlx-lm 0.32.2's `muse_glimmer` sanitize doesn't drop Meta's `model.vision_*` weights; `convert.py`
  patches it. Worth reporting upstream.

# Long plans, batching, mask overhead and long-sequence QLoRA (27 Oct 2026, stow-gpu)

Raw data: `results_2026-10-27.json` (built by `collect_2026-10-27.py` from `long.jsonl`, `calib*.jsonl`, `step*.json`,
`qlora_logs/`). Scripts: `bench_long.py` (batched generation via mlx-lm `BatchGenerator`, EOS disabled, temp 1.0,
per-sequence logits processors), `bench_step.py` (one decode step of the bare model, no generator), `qlora_peak.py`
(`mlx_lm lora` + MLX peak-memory counter), `make_long_data.py` (~16.7k-token synthetic texts). Runners:
`run_long.sh` → `run_long2.sh` (trimmed grid) → `run_extra.sh` → `run_phase2.sh`. Model `glimmer-30b-4bit` unless noted.

**Read this first: the GPU slows down ~35–40% under sustained load.** The same b16 / 2k-prompt / 300-token run gave
127 tok/s cold (first job of the day), 114 tok/s after 3 min idle, and ~80 tok/s in the middle of the sweep;
prefill went 825 → ~420 tok/s. `pmset -g therm` records nothing (it reports CPU, not GPU), so "thermal/power
throttling" is an inference, not a measurement. **All tables below are sustained numbers** unless marked cold;
plan hours-long jobs on them. The 26 Oct numbers above are cold.

## Generation (decode tok/s aggregated over the batch, per output segment)

| Prompt | Batch | 0–500 | 500–2k | 2k–5k | 5k–10k | Prefill | Peak |
|---|---|---|---|---|---|---|---|
| 2k | 1 | 31.5 | 31.1 | 27.8 | 21.3 | 650 tok/s | 16.5 GB |
| 2k | 8 | 61.3 | 54.7 | 55.6 | 51.9 | 530 tok/s | 19.3 GB |
| 2k | 16 | 81.3 | 83.5 | 79.0 | 76.9 | 450 tok/s | 21.7 GB |
| 2k | 32 | 121.7 | 130.2 (to 1k) | – | – | 445 tok/s | 22.8 GB |
| 2k | 64 | 195.6 | 201.1 (to 1k) | – | – | 410 tok/s | 30.8 GB |
| 8k | 1 | 17.7 | 18.0 | 19.7 | – | 455 tok/s | 16.7 GB |
| 8k | 16 | 78.4 | 76.3 | 74.5 | – | 445 tok/s | 22.4 GB |
| 2k, **3-bit** | 16 | 82.3 | 82.2 | – | – | 425 tok/s | 16.7 GB |

Per-sequence speed at b16 is ~5 tok/s, so one 10k-token plan in a batch of 16 takes ~35 min; alone (b1) ~6–7 min.

**Bare model, one decode step** (`bench_step.py`, no generator, sustained): batch 1/2/4/8/16/32/64 at KV 2k =
36.8 / 45.1 / 69.6 / 126 / 185 / 206 / 289 ms → **27 / 44 / 57 / 63 / 87 / 156 / 222 tok/s**. At KV 12k: b1 35.9 ms,
b8 162 ms, b16 196 ms. So:
- **Batch 8–16 is a bad spot in MLX's 4-bit matmul**, not generator overhead: b16 costs 5× b1, but b32 costs only
  1.1× b16, and b64 1.4×. **Use batch 32–64 for rollouts and eval** (b64 ≈ 2.5× the throughput of b16, ~31 GB).
- Long context costs little in the bare model (b1 at 12k ≈ 2k, thanks to 39 of 52 layers being 2,048-token sliding
  window), but `BatchGenerator` at b1 with an 8k prompt runs at 18 tok/s vs 28 bare. For single-stream use
  `generate_step`/`stream_generate` (26 Oct: 30.8 tok/s at a 9k prompt), not `BatchGenerator`.
- 3-bit gives only ~3% at b16 (it helped 18% at b1 on 26 Oct): batched decode isn't bandwidth-bound.

## Mask (logits processor) overhead at b16, 2k prompt, 2k output

| Processor | tok/s (500–2k) | vs none |
|---|---|---|
| none | 80.4 | — |
| `lazy`: `mx.where` with a constant 202k-entry mask (GPU only) | 77.5 | −4% (noise-level) |
| `sync`: reads the last token on the host (`.item()`), looks up a mask in a dict, builds a 202k-entry `mx.array` | 76.6 | **−5%** |

The sync processor is the realistic shape of a Python feasibility mask; its cost is ~8 ms per step at b16, i.e.
**~0.5 ms per sequence per step** (calibration, cold: 126 → 132 ms/step). Not included: the feasibility engine's own
compute (4.4) — budget it at ≤0.5 ms per sequence-step so the mask stays under ~10%. mlx-lm calls processors one
sequence at a time and **computes `logprobs` after the processors**, so the sampled-token log-probs it returns are
already the masked log-probs D9 asks for.

## QLoRA (rank 8, Adam, batch 1, 4 iterations; tok/s per iteration shown first → last)

| Seq | Layers | Grad ckpt | tok/s | Peak (MLX counter) |
|---|---|---|---|---|
| 8,192 | last 16 | no | **OOM** (Metal out of memory) | >~110 GB |
| 8,192 | last 16 | yes | 195 → 176 | 41.3 GB |
| 8,192 | all 52 | yes | 90 → 86 | 46.1 GB |
| 12,288 | last 16 | no | **OOM** | — |
| 12,288 | last 16 | yes | 147 → 149 | 65.9 GB |
| 12,288 | all 52 | yes | 76 → 77 | 72.9 GB |

Memory grows ~quadratically with sequence length (attention is not fused in the training path), so 16k+ sequences
would need ≥100 GB; keep SFT/GRPO training sequences ≤ 12k tokens on the Mac, and nothing else heavy beside a 12k run.

## What this changes (numbers for docs/compute.md)

| Quantity | Old estimate (compute.md) | Measured (sustained) |
|---|---|---|
| Single-stream decode | ~31 tok/s | 31 cold; **21–28** at 5–10k tokens out (`BatchGenerator`); use `generate_step` |
| Batched decode b8 / b16 | 100 / 138 tok/s | **55 / 80** tok/s |
| Batched decode b32 / b64 | — | **125 / 200** tok/s |
| Prefill | ~870 tok/s | 825 cold, **~420–450** sustained |
| Mask overhead | not included | **~5%** for a Python host-sync mask (excl. engine compute) |
| QLoRA, 8k seq, 16 / 52 layers | ~330 / ~130 tok/s (4k seq) | **~180 / ~87 tok/s** (grad ckpt required) |
| QLoRA, 12k seq, 16 / 52 layers | — | **~150 / ~77 tok/s**, 66 / 73 GB |
| Mac training ceiling | 28M tokens/day | **~15M tokens/day** (16 layers, 8k seq) |

Worked examples at 5–10k output tokens per plan (2k prompt):
- **One demo plan** (b1): 5k tokens ≈ 3–4 min, 10k ≈ 7 min. Too slow live; this is why 3.8/4.5 should aim at ≤3k
  output tokens per plan and keep D9 token-skipping in reserve.
- **Eval 9.1, 300 plans × 7.5k at b64**: 2.25M tokens / ~190 tok/s (with mask) ≈ **3.3 h** (+ prefill 0.4 h);
  compute.md's 45 min assumed 900-token plans. Quick eval (50 plans): ~35 min.
- **SFT 8.1, 3,000 plans × 8k tokens** = 24M tokens/epoch → **~37 h/epoch on 16 layers**, ~77 h on all layers.
  Doesn't fit next to everything else; ~1,000 examples × 8k (≈12 h, overnight) or shorter plans is the realistic size.

## Provenance and validation follow-up (CPU review fixes)

`convert.py` now pins full source revision `cec94fcfd6512f2cfe708138c32533b346ea4f69` and writes
`conversion_provenance.json` with conversion settings and software versions after success. Existing artifacts
predate this metadata; their provenance has not been retroactively established.

`bench_long.py` resumes only when model path, full content SHA-256 (weights plus tokenizer/config), prompt text
(or seeded random fallback), benchmark schema and Python/MLX/mlx-lm/NumPy/Transformers/tokenizers versions match. Historical rows lack
that identity and are retained as historical evidence but cannot suppress a fresh run. Hashing streams the model
on CPU before loading it. `MAX_OUT=1` records prefill and null decode throughput (there is no decode interval).

Numerical validation is queued, **not run** in the CPU sandbox. After reserving the local GPU, use:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run --offline python experiments/glimmer-bench/validate_conversion.py models/glimmer-30b-4bit experiments/glimmer-bench/conversion_validation.json
```

The check loads the cached pinned source then the converted model sequentially and compares full teacher-forced
logits over four fixed public/synthetic prompts: mean/max KL (probability disagreement, in nats), top-token
agreement and logit error. It requires no downloads. Source loading uses the same text-tower sanitizer as conversion;
this is not an independent backend verification. Establish an appropriate quantization tolerance against a trusted
independent conversion and inspect failures before claiming conversion correctness. Provisional queue estimate
(unmeasured, admission required): one GPU job, 1–2 CPU cores, ≤85 GB peak RAM, 5–15 minutes; first measure one
prompt and scale. No GPU or cloud work was performed during this fix.
