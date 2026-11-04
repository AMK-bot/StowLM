# GRPO venue (plan 0.8): 27 Oct 2026, stow-gpu

**Recommendation: GRPO on Modal CUDA.** Use 2× H100: one runs vLLM 0.31.0 for rollouts with our feasibility-mask
`LogitsProcessor`, the other runs TRL 1.16.0 `GRPOTrainer` with PEFT LoRA on a bf16 base, subclassed to compute
log-probs under the mask. SFT and eval stay on the Mac in MLX, and the adapter moves between them with
`convert_adapter.py` (verified below). This depends on the **$2.3 smoke test** (`modal_smoke.md`), which needs the
user's approval. If the smoke test fails, the fallback is a mini GRPO in MLX (numbers below) or cut-order 2 (SFT + mask only).

**Evidence status: partial.** Q1 imports and Q1b weights download passed on Modal CPU. Q2 vLLM GPU loading/mask
checks and Q3 CUDA LoRA training remain **unrun**. The current Q3 log-prob replay compares base models with the
adapter disabled; it does **not** establish adapter-on sampler/trainer agreement through task 6.4's weight
synchronization path. Both that check and a successful CUDA training step are required before the venue is verified.

Files in this folder:
- `cuda-stack.md`: library versions and Glimmer support, with file:line evidence.
- `modal_smoke.py` / `.md` / `.lock.txt`: smoke test; Q1/Q1b CPU passed, Q2/Q3 GPU unrun.
- `convert_adapter.py`: MLX↔PEFT adapter conversion.
- `mlx_logits.py`, `pt_logits.py`, `compare_logits.py`, `convert_bf16.py`: the conversion check.
- `compare_logits.json`: results.
- `.venv-pt/`: isolated torch 2.14.1 / transformers 5.21.0 / peft 0.21.2 env for the Mac check.

## 1. Does the CUDA stack support Glimmer? Yes, but the mask is custom code everywhere (details: `cuda-stack.md`)

| Library | Version | Glimmer | Gap for us |
|---|---|---|---|
| transformers | 5.21.0 (Glimmer since 5.15.0) | yes, `MuseGlimmerForConditionalGeneration` only (no text-only CausalLM) | none |
| PEFT | 0.21.2 | works, no default target map | use the anchored regex, or the vision tower gets adapters |
| TRL | 1.16.0 | yes: fused head applies `output_multiplier` + softcap (`trainer/utils.py` L1576-1580) | no logits-processor hook; training log-probs are full-vocab (`grpo_trainer.py` L1419-1508) → override |
| vLLM | 0.32.0 (Glimmer since 0.28.0; TRL pins ≤0.31.0) | yes, LoRA declared | V1 logits processors are engine-level classes (`--logits-processors`) |
| Unsloth | 2026.10.9 | yes, incl. GRPO | patches the same TRL internals we must override, so avoid it |

Pinned stack: torch 2.13.0, transformers 5.17.0, vllm 0.31.0, trl 1.16.0, peft 0.21.2. It resolves offline to 209
packages (`modal_smoke.lock.txt`). Installing and importing it on Modal CPU passed (smoke Q1, recorded in §6).
CUDA driver support, GPU loading and training are unverified.

## 2. Adapter conversion MLX 4-bit ↔ PEFT: correct; 4-bit base adds a small shift (measured on the Mac)

**Test setup:**
- **Toy adapter:** `models/toy-adapter-mlx`. MLX QLoRA, rank 8, scale 20, last 16 layers, all 8 linears including the
  attention output gate. 40 iterations at lr 2e-4 on `data/glimmer-bench`, so it is deliberately strong: it changes
  the model's next-token distribution by KL 13–22 nats.
- **Conversions:** MLX → PEFT gives `models/toy-adapter-peft`, 256 tensors, alpha 160. Converting back gives
  `models/toy-adapter-mlx-roundtrip`.
- **Prompts:** 5 prompts as fixed token IDs: 2 in-distribution plans, English, chat template, and a 3,000-token plan
  that is longer than the 2,048-token sliding window. Logits were compared at the last 64 positions.
- **Measure:** the "delta" is logits with the adapter minus logits without it.

**Results:**
- **Round trip:** bit-identical (max |Δ| = 0.0 for both weights and logits).
- **MLX bf16 vs PyTorch bf16 + PEFT** (`convert_bf16.py`, same base precision, so this tests the conversion itself):
  - Delta cosine is 0.995–0.9995, with relative error 3–10%.
  - KL between adapted models is ≤0.0034 nats/token on the plan and English prompts, and 0.046 on the chat prompt.
  - The base models alone agree to KL ≤0.002, including the 3,000-token prompt, so both stacks implement the
    sliding-window and NoPE layers the same way.
  - **The conversion is correct.**
- **MLX 4-bit vs PyTorch bf16 + PEFT** (what moving SFT to GRPO actually does):

| Prompt | Delta cosine | Delta rel. err | KL base bf16‖q4 | KL adapted bf16‖q4 | Top-1 agree (adapted / base) |
|---|---|---|---|---|---|
| plan (in-dist) | 0.988 | 0.16 | 0.007 | 0.030 | 0.88 / 0.97 |
| plan 2 | 0.984 | 0.19 | 0.032 | 0.042 | 0.80 / 0.92 |
| English | 0.947 | 0.32 | 0.008 | 0.027 | 0.81 / 1.00 |
| chat | 0.949 | 0.31 | 0.244 | 0.309 | 0.77 / 0.77 |
| plan, 3,000 tok | 0.969 | 0.25 | 0.002 | 0.057 | 0.73 / 0.97 |

KL is asymmetric: all cross-stack numbers above and in the stored JSON are **KL(PyTorch bf16 ‖ MLX)**, with
MLX precision given by the `mlx` tag. The bf16 comparison uses the same direction. The JSON now names these
`kl_base_bf16_to_mlx` / `kl_adapted_bf16_to_mlx` and includes `kl_direction`; historical `*_q4_vs_bf16` keys remain
compatibility aliases of the same values. This corrects the original reversed table labels, without rerunning a
model or inventing the reverse-direction KL: stored `.npy` logits are absent from this folder.

**What this means:**
- Most of the cross-stack error is 4-bit quantisation, and an adapter amplifies it: KL rises from ~0.002–0.03
  to ~0.03–0.06 nats/token.
- The toy adapter is far stronger than a real SFT adapter should be, so treat these as upper-end numbers. UNVERIFIED
  for the real 8.1 adapter.
- **Consequences:**
  - (a) GRPO's sampler and trainer must use the same base precision (both bf16 on Modal).
  - (b) Re-measure this table and the checker pass rate with the real SFT adapter before GRPO starts (6.4).
  - (c) The adapter GRPO produces is trained against bf16 but deployed on MLX 4-bit, so eval 9.1 must score the
    q4 deployment, not the bf16 one. The checker gate keeps this safe either way.

## 3. Mac-GPU hours vs Modal cost for GRPO

GRPO (Group Relative Policy Optimisation, [Shao et al. 2024, DeepSeekMath §4](https://arxiv.org/abs/2402.03300))
works like this. For each prompt it samples a group of G plans and scores each with the reward (checker gate +
negative weighted moves). Each plan's advantage is its reward minus the group mean, divided by the group std. It
then takes a clipped policy-gradient step on the LoRA weights. So every step costs G × prompts rollouts of
generation, plus one forward/backward pass over all of them.

The reference step below is 4 prompts × G=16, which gives 64 rollouts of a 2k-token prompt plus 6k output tokens:
384k generated tokens and 512k training tokens per step. KL-to-reference is off (β=0), so there is no reference
forward pass.

| | Mac, MLX (measured, sustained) | Modal, 2× H100 (ESTIMATED, unmeasured) |
|---|---|---|
| Rollouts | b64, ~190 tok/s with mask → **~34 min** | vLLM ~1,000–2,000 tok/s (Python mask ~0.5 ms/seq-step adds ~30 ms/step at b64) → **3–6 min** |
| Policy update | QLoRA 8k seq: 16 layers ~180 tok/s → **~47 min**; all layers ~87 tok/s → ~98 min | LoRA bf16 + grad ckpt ~1,500–2,500 tok/s → **3.5–6 min** |
| Per step | **~1.4 h** (16 layers) – 2.2 h (all) | **~7–12 min** wall × 2 GPUs ≈ **$0.9–1.6/step** (+~10% CPU/mem) |
| 50 steps | **~70–110 Mac-GPU hours** | **~$45–80**: fits the ~$80 GRPO budget |
| Mini GRPO (2 prompts × G=8, 3k out) | ~25 min/step → 30 steps ≈ **12.5 h** (one overnight) | ~$0.2/step |

- The Mac has about 190 GPU-hours left before the deadline. SFT 8.1 (≥12 h) and eval 9.1 (~4 h per full run) also
  need it, so a real-sized GRPO run doesn't fit locally. **Modal is ~10× faster per step, and $80 buys ~50–90
  reference steps** (~35–55 at 10k-token plans).
- The Modal throughputs are estimates from bandwidth/FLOP arithmetic (H100 3.35 TB/s, ~35–50% MFU) and have **not
  been measured**. The smoke test's Q2/Q3 measure them, at 16×2k decode and one 12k LoRA step.
- The H100 rate comes from `docs/compute.md`: $3.9492/h.
- The older compute table's ~200–300 steps for ~$40 uses shorter examples and is superseded for this reference
  workload. At 4 prompts × G=16 × (2k prompt + 6k output), 200 steps would cost ~$180–320 before CPU/memory,
  not ~$40. These remain estimates until CUDA measurements exist.

## 4. Proposed decision text (for the leader to put in docs/decisions.md)

> ## D15 — 2026-10-27 — GRPO runs on Modal (vLLM + TRL, bf16 LoRA); SFT and eval stay in MLX
> GRPO for StowLM runs on Modal on 2× H100. One GPU runs vLLM 0.31.0 rollouts with our feasibility mask as a vLLM V1
> `LogitsProcessor`. The other runs TRL 1.16.0 `GRPOTrainer` with PEFT 0.21.2 LoRA on a bf16 base, subclassed so that
> `_get_per_token_logps_and_entropies` computes log-probs under the same mask (D9): log-softmax over the allowed
> tokens only. The stack is pinned (torch 2.13.0, transformers 5.17.0) and installed together with the checker and
> mask versions. SFT (8.1) and eval (9.1) stay on the Mac in MLX 4-bit. Adapters cross with
> `experiments/grpo-venue/convert_adapter.py`: `A = a.T`, `B = b.T`, `lora_alpha = scale·r`, and an anchored
> `target_modules` regex so the vision tower is never adapted.
> **Why:**
> - All four CUDA libraries support Glimmer.
> - A GRPO step at our plan lengths (64 rollouts × 6k tokens) takes ~1.4–2.2 h on the Mac (measured) against
>   ~7–12 min on Modal (estimated), and the Mac GPU is needed for SFT and eval.
> - The conversion is exact (bit-identical round trip; bf16↔bf16 delta cosine ≥0.995). The 4-bit base adds
>   ~0.03–0.06 nats/token of KL with a strong toy adapter.
>
> Unsloth is not used, because it patches the TRL internals we override.
> **Conditions:**
> - Run the $2.3 smoke test (`experiments/grpo-venue/modal_smoke.py`) first, with user approval.
> - Re-measure the q4↔bf16 shift on the real SFT adapter.
> - Score the final adapter in the MLX 4-bit deployment.
> - Keep the spend cap and checkpoint every step (GPU functions are preemptible).
>
> **Revisit if:**
> - The smoke test fails on stack import, vLLM processor application or masked log-prob agreement. Then fall back
>   to a mini GRPO in MLX (~25 min/step at 32 rollouts × 3k tokens; mlx-lm already returns masked log-probs) or to
>   cut-order 2.
> - Plans shrink to ≤2k output tokens. That makes MLX GRPO 3–5× cheaper and possibly enough.

## 5. Notes for 6.4 (GRPO infrastructure)

- **Feasibility engine (4.4) cost:** keep its per-step cost to ≤0.5 ms per sequence. In vLLM the processor runs on
  the host for the whole batch every step, and at b64 Python overhead costs about as much as the forward pass itself.
  Precompute the allowed-token sets per grammar state.
- **Importance-sampling ratio:** the vLLM sampler's log-probs (`processed_logprobs`) are taken after the mask, so
  the trainer must use the same mask, or the ratio is biased. Positions with only one legal token have log-prob 0:
  exclude them from the loss.
- **Prompt cache:** in MLX, prefill each group's shared prompt once and copy the cache. Prefill is only ~420 tok/s
  under sustained load.

## 6. Modal CPU stage run: 27 Oct 2026, stow-modal (Q1 + Q1b CPU PASS; Q2/Q3 GPU unrun)

Command (user-approved CPU-only preliminary spend):
`uv run --with modal modal run experiments/grpo-venue/modal_smoke.py --only-cpu --cap 1`.
Logs, results and billing are in `runs/` (`cpu-smoke-2026-10-27.*`, `attempt1-crashloop.log`).

- **Attempt 1 failed on a bug:** module import in the container crash-looped (13 restarts, then stopped by hand). The
  container copy is `/root/modal_smoke.py`, so `HERE.parents[1]` raised `IndexError`. Fix: `LOCAL_ADAPTER` is `None`
  inside the container, and the adapter upload is guarded (2 lines in `modal_smoke.py`). No GPU stage could start.
- **Q1 import, PASS (32.9 s wall, cold start included):**
  - Versions: torch 2.13.0, transformers 5.17.0, vllm 0.31.0, trl 1.16.0, peft 0.21.2, accelerate 1.15.0,
    flashinfer 0.7.0.post1, triton 3.7.1, nvidia-cuda-runtime 13.0.96, xgrammar 0.2.7.
  - `MuseGlimmerForConditionalGeneration` is present in transformers. Both Glimmer archs are in the vLLM registry.
  - The V1 `LogitsProcessor` signatures match `cuda-stack.md`. The TRL `_get_per_token_logps_and_entropies`
    signature is in `runs/cpu-smoke-2026-10-27.results.json`.
  - CPU only, so the CUDA 13 driver question is still open for Q2.
- **Q1b weights, PASS:**
  - Volume `stowlm-glimmer-weights`, path `/Muse-Glimmer-30B@cec94fcf`, 59,581,797,464 B (55.5 GiB).
  - Downloaded from the HF Hub inside Modal (not uploaded from the Mac) in 209.9 s, at 284 MB/s unauthenticated.
  - Spot-checks: sha256 matches the local HF cache for all 7 json/jinja/tokenizer files. Both safetensors shards
    match byte-for-byte in size (49,950,112,952 and 9,603,322,320 B).
  - The shards were not re-hashed on the Volume, which would need another container run. hf_xet verifies chunks
    during download. Local blob sha256: `8eef6153…2694` and `b58cc214…4381`.
  - Toy adapter (62 MB) uploaded to Volume `stowlm-adapters:/toy-adapter-peft`.
- **Image build:** the heavy image (209 pkgs) built in 34 s. It is cached now, so later runs reuse it.
- **Cost, measured by `modal billing report --for today --show-resources`:**
  - Attempt 1: $0.0052.
  - Attempt 2: CPU $0.0273 + memory $0.0141 + egress $0.0335 = $0.075. The egress is mostly my `volume get`
    spot-checks and is covered by the egress allowance.
  - Workspace this month: metered $0.09, credits −$0.06, egress allowance −$0.03, **billed $0.00**.
  - The script's own client-side estimate was $0.026 against $0.041 metered compute. Modal bills container idle and
    scale-down time too, so treat the script's GPU estimates as ~1.5× low.
- **Volume storage:** 55.5 GiB × $0.09/GiB-month ≈ **$0.17/day (~$5/month)**, not yet shown in billing. Delete it
  with `modal volume delete stowlm-glimmer-weights` if GPU stages slip by more than a few days.
- **GPU stages (Q2 vLLM + Q3 HF/LoRA), not run.** Run them once the dashboard shows the credit:
  `uv run --with modal modal run experiments/grpo-venue/modal_smoke.py --cap <available $, ≥3; recommend 5>`.
  - Q1 re-runs (~$0.003), and Q1b is skipped by its `.complete` marker.
  - Typical cost is ~$2.2 by the script's estimate, ~$3 adjusted. Worst case is ~$5.6.

### Required follow-up for task 6.4 (not covered by this smoke)

Use a nonzero text LoRA adapter in the actual trainer, synchronize it via the production
`src/llm/rl/modal_grpo.py` merge/export/worker-load path, and compare the same masked token log-probs before any
optimizer update on both trainer and rollout sides. Repeat after an optimizer update and synchronization, and
require a nonzero adapter effect so a silently missing adapter fails. Check the real SFT adapter separately (D26).
The script records `adapter_on_sampler_trainer_agreement_verified: false` even if its base replay passes.
Adding an alternate smoke-only adapter loader would not validate the production synchronization path.
