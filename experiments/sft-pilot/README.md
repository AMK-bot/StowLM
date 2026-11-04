# SFT pilot for 8.1: long sequences, memory, learning rate (27 Oct 2026, stow-gpu)

All runs use the 8.1 training code (`llm.stowlm.train`) on `models/glimmer-30b-4bit` (MLX 4-bit QLoRA). The setup is
LoRA rank 8, scale 20 on all linears, Adam, grad checkpointing on. The data is **stub-tainted and timing-only**:
nothing here is a quality result.

## Recommended 8.1 config

| Setting | Value | Evidence |
|---|---|---|
| LoRA | rank 8, scale 20 (= PEFT `lora_alpha` 160), all 8 linears per block | Unchanged from the tiny chain. MLX↔PEFT conversion verified at this rank (D26). Other ranks not tested. |
| Layers | **last 16** for the first full run. All 52 only as an ablation if time allows. | 16 layers is 2× faster (≈200 vs ≈100 tok/s). The quality difference is unmeasured. |
| Attention | **`--attn-block 1024`** (exact query-blocked attention, below) | Same outputs and gradients (tested). Peak memory −7 % at 6k, −21 % at 9k, −38 % at 14k; 7–9 % faster on all-layer runs at 9–14k. |
| Max sequence | **18,432** (fits the largest v2 example, 17.7k: realistic_1000) | 17.7k at all 52 layers peaks at 61.9 GB with blocked attention, well under the 100 GB cap. |
| Batch | `--batch-size 1 --grad-accumulation-steps 4` for full-size examples; batch 4 is fine for tiny/small | One long example per step. Effective batch 4, matching the lr pilot. |
| LR | **peak 5e-5, `--lr-schedule cosine --warmup-frac 0.05 --min-lr-frac 0.1`** | Best and stable in the pilot (below). 2e-5 is the safe fallback. 1e-4 diverges with or without warmup. |
| Grad checkpointing | on | Required at ≥ 8k: without it, OOM at 8k even on 16 layers (26 Oct bench). |
| Checkpoints | `--steps-per-save 100` (a multiple of `--grad-accumulation-steps`; so must `--iters` be) | Since fix-t8-sft (trainer `stowlm-train@2.0`): each checkpoint is adapter + Adam state + `<step>_state.json` (commit marker, lineage), also at the final step. A resume restores the exact trajectory (batch order and dropout RNG are functions of seed and step); tested bitwise on the real model (`synthetic_e2e.json`). |

Command (the data dir must be a 7.1 `production_gold` dataset; anything else is refused unless `--diagnostic true`
and the adapter dir is under `sft-pilot/` or `tiny-chain/`, which stamps the adapter as never shippable):

```
uv run python -m llm.stowlm.train --data-dir <7.1 production_gold dir> --adapter-dir <out> --rank 8 --scale 20 \
  --num-layers 16 --attn-block 1024 --max-seq-length 18432 --batch-size 1 --grad-accumulation-steps 4 \
  --grad-checkpoint true --learning-rate 5e-5 --lr-schedule cosine --warmup-frac 0.05 --min-lr-frac 0.1 \
  --iters <N, multiple of 4> --steps-per-report 10 --steps-per-eval 100 --steps-per-save 100 --val-batches 25
```

The pilot logs and adapters below predate trainer 2.0: they have no weight-bound sidecar, so the planner refuses
them (fail closed), and their checkpoints can't be resumed (no state marker).

## Synthetic end-to-end loop check (fix-t8-sft, 28 Oct 2026)

`synthetic_e2e.py` trains the real 4-bit Glimmer (16 layers, cosine + warmup, D31 settings, seq ≤ 1k) for 6 steps on a
**clean synthetic fixture** (seeded random token IDs, labelled `synthetic_fixture`; not gold, never shippable), then
repeats the run with an interruption after step 3 and a resume. Results (`synthetic_e2e.json`): resumed adapter
identical to the uninterrupted one (max |Δw| = 0.0); sidecar stub `stowlm.train.dataset`; the 6.2 planner's
`require_clean` refuses the adapter. 53 s wall for three train calls, 17.8 GB MLX peak (32 GB max RSS incl. load).
Numbered checkpoint weights were deleted afterwards to save disk (1.2 GB); state markers, sidecar, run records and
the final published adapter (`synthetic-fixture/split/`) remain.

## Per-token cost, for sizing the gold-plan budget

Every token of an example costs time, prompt tokens included, even though the loss covers only the completion (the
actions). Prompts are 60–70 % of a full-size example (e.g. 11.6k prompt + 6.0k plan at 17.7k). So D24-style prompt
compaction pays off directly in training time.

| | Processed tok/s (sustained) | Hours per 1M tokens | Tokens per day |
|---|---|---|---|
| 16 layers | **~200** (188–245 measured at 6–17.7k) | **1.4 h** | ~17M |
| all 52 layers | **~100** (94–135 measured) | **2.8 h** | ~8.6M |

Examples (one epoch, 16 layers):

| Gold set | Tokens | Time |
|---|---|---|
| 1,000 full examples (~6k tokens) | 6M | ~8.3 h |
| 300 realistic_1000 examples (~14–17k) | 4.7M | ~6.5 h |
| 3,000 tiny/small examples (~0.6–2k) | ~3M | ~4 h |

At ~17M tokens a day for SFT, and with eval (9.1) also needing the GPU, plan on **≈10–15M training tokens in total**.
These rates come from 3-step runs after 2 h of continuous GPU load (sustained, not cold). Longer runs could lose a
little more to throttling (UNVERIFIED).

## 1. Long-sequence feasibility (batch 1, grad checkpointing, 3 steps each; `long_results.json`)

| Example (tokens; prompt + plan) | Layers | Attention | Peak memory | Step time | Processed tok/s |
|---|---|---|---|---|---|
| 5,784 (3,823 + 1,961) | 16 | default | 30.3 GB | 25.8 s | 224 |
| 5,784 | 16 | blocked 1024 | 28.2 GB | 25.1 s | 229 |
| 5,784 | 52 | default | 33.8 GB | 42.7 s | 135 |
| 8,635 (5,834 + 2,801) | 16 | default | 43.7 GB | 35.4 s | 244 |
| 8,635 | 52 | default | 48.7 GB | 79.0 s | 109 |
| 8,635 | 52 | blocked 1024 | 38.5 GB | 73.8 s | 117 |
| 13,824 (8,223 + 5,601) | 16 | default | 76.1 GB | 73.7 s | 188 |
| 13,824 | 52 | default | 83.7 GB | 146.7 s | 94 |
| 13,824 | 52 | blocked 1024 | **51.9 GB** | 133.8 s | 103 |
| **17,668** (11,627 + 6,041) | 16 | blocked 1024 | **53.0 GB** | 87.3 s | 203 |
| **17,668** | 52 | blocked 1024 | **61.9 GB** | 184.8 s | 96 |

**17.7k fits, and the 100 GB cap leaves ~40 GB of headroom.** 17.7k was not run with default attention.
Extrapolating the measured default-attention curve (30 → 44 → 76 GB at 6/9/14k on 16 layers) gives ≈110–130 GB.
That would break the cap, so 17k needs the blocked attention.

The first step of every run includes graph compilation (e.g. 116.8 s, then 43 s). Step times above leave it out.
A pre-reboot 6k/16-layer run measured under heavy host load (77 s/step) was replaced by a clean rerun; the old one
is kept as `adapters/prereboot-long-6k-l16`.

**Examples are format-only** (`make_long_format_only.py`). The prompt is the real `encode_prompt` of a real v2 instance
(random_yard full / realistic_500 / realistic_1000). The completion is a format-valid placement list (box i → cell i),
not an engine-legal plan. This was necessary: **all 100 full-size v2 instances I tried are depth-0 dead ends in the
feasibility engine even with C2 off**. C1a rejects every root candidate (`expert.physical.size_fit`,
`expert.physical.slot_occupancy`), and the scalable solver returns `dead_end` in 0.5–3 s (`make_long-*.log`). Token
counts and costs are the same as for a real plan; the loss values aren't meaningful. **Flag for the 4.4 / instance
owners:** no full-size v2 instance is plannable today.

### Blocked attention (`train.blocked_attention`, `--attn-block`)

**The cause.** In training, mlx-lm's attention backward pass stores the full (heads × L × L) score matrices. Memory
therefore grows with L², and that, not the weights, is what limits sequence length.

**The fix.** Softmax works on each query row separately, so the queries can be split into blocks of 1,024 rows and
attended one block at a time. The output is exactly the same. Each block is wrapped in `mx.checkpoint`, so the
backward pass recomputes that block's scores instead of keeping them; only one block's scores are alive at a time.
Glimmer's 39 sliding-window layers (window 2,048) also skip keys outside their window, which cuts their compute.
This is FlashAttention's tiling idea (Dao et al. 2022, arXiv:2205.14135), done block by block with recomputation
instead of a fused kernel.

**Exactness.** `tests/llm/test_train.py::test_blocked_attention_is_exact_forward_and_backward` checks forward output
and all three gradients against mlx-lm's attention, for causal and sliding-window masks with grouped-query heads.
This is not an approximation.

## 2. Learning rate and warmup (`lr_results.json`)

Setup: the stand-in tiny set (161 train / 5 val, ~567 tokens), 16 layers, batch 4, 150 iterations (~3.7 epochs).
All runs use seed 0, so data order and initialisation are identical. Val is evaluated every 10 iterations.
"Spikes" counts train reports more than 2× the best train loss so far.

| Peak lr | Schedule | Val at it 29 | Val min | Val last | Spikes |
|---|---|---|---|---|---|
| 2e-5 | 5 % warmup + cosine → 10 % | 0.267 | 0.204 | 0.217 | none |
| **5e-5** | 5 % warmup + cosine → 10 % | 0.329 | **0.181** | **0.209** | none |
| 1e-4 | 5 % warmup + cosine → 10 % | **4.466** | 0.502 | 0.518 | 7 (it 30–65) |
| 1e-4 | constant, no warmup | 3.125 | 0.505 | 1.018 | 22 (it 25–140) |

**Conclusion.** The instability comes from the 1e-4 peak itself. Warmup alone doesn't fix it: the spike arrives just
after warmup ends, and cosine decay only shortens it. 5e-5 reaches the lowest val loss with no spikes, and 2e-5 is
almost as good. With 5 val examples of 43 loss tokens each, the 5e-5 vs 2e-5 difference is within noise. The safe
range is "≤ 5e-5".

**Untested.** Whether this transfers to long examples, where there are many more loss tokens per step. Watch the
first ~100 steps of 8.1, and drop to 2e-5 at the first spike. Each run took ~21–23 min (1,256–1,395 s), at 35 GB peak.

## Files

- **Data builders:** `make_long.py` (stand-in attempt; every instance dead-ended) and `make_long_format_only.py`.
- **Runners:** `run_long.sh` (with a memory watchdog: stops the run if free memory falls below 10 %),
  `run_long_all.sh`, `run_lr.sh`.
- **Results:** `long_results.json`, `lr_results.json`, and `adapters/*/run.json` + `metrics.jsonl`. The weights were
  deleted (8.5 GB).
- **Tests:** `tests/llm/test_train.py`, which covers the schedule, blocked attention and the optimizer round trip
  (all `gpu`).
