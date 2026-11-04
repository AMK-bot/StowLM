# Conversion tolerance calibration (task 0.1, fix-t8-sft, 28 Oct 2026 19:33 EDT)

This closes the deferred 0.1 row in `experiments/review/fixes/fix-t0-exp.md`: "acceptance tolerance must be calibrated
against a trusted independent conversion".

**The reference** is Meta's pinned checkpoint (`cec94fcf`) run in **PyTorch bf16 through transformers on MPS**. It is
independent of mlx-lm: different model code, different weight loading, no MLX sanitizer.

**What it is compared against.** Six fixed-ID prompts, logits at the last 64 positions:
- the four `validate_conversion.py` prompts;
- two in-domain stowlm-prompt@2.2 prompts (573 and 563 tokens).

**The measure** is KL(reference ‖ variant) in nats per token. It counts the information lost when the variant's
next-token distribution stands in for the reference's (Cover & Thomas, *Elements of Information Theory*, ch. 2).

| variant | what it is | mean KL over the 6 prompts | per-prompt KL | top-1 agreement |
|---|---|---:|---|---|
| `mlx_bf16` | the same checkpoint in MLX bf16 | **0.0051** | 0.0005–0.019 | 0.95–1.00 |
| `mlx_q4_fresh` | the source quantised now (affine, group 64, 4 bit) | 0.0754 | = q4 | = q4 |
| `mlx_q4` | shipped `models/glimmer-30b-4bit` | **0.0754** | 0.016–0.186 | 0.76–1.00 |
| `mlx_q3` | `models/glimmer-30b-3bit` (coarser) | 0.278 | 0.107–0.517 | 0.59–1.00 |
| `mlx_q4_swap` | **negative control:** q4 with decoder blocks 20↔21 swapped | 0.251 | 0.153–0.359 | 0.53–0.93 |

**Findings:**
- **The MLX implementation matches the independent one.** The bf16-vs-bf16 gap is ≤ 0.019 nats/token, so mlx-lm's Glimmer code and sanitizer reproduce transformers'.
- **The shipped 4-bit model and a fresh quantisation of the source agree on the 6 prompts.** Their logits differ from a fresh `quantize_model(affine, g64, 4)` of the source by **0.0** (max |Δ| = 0) at every measured position. This compares predictions on 6 prompts, not on-disk tensors, so it is strong but not complete evidence that the file is that procedure's output.
- **Calibrated acceptance tolerance.** Each threshold sits at the geometric mean between the q4 value and the nearest disqualified value (q3 or the swap):
  - **Primary: mean KL over the 6 prompts ≤ 0.138 nats/token.** q4 is at 0.075; the nearest disqualified value is 0.251, a 3.3× margin.
  - **Secondary: per-prompt thresholds** 0.042 / 0.069 / 0.075 / 0.138 / 0.258 / 0.151 (prompt order as in `prompts.json`). Margins are 1.9–6.6×.
  - **Implementation check (bf16 vs bf16): ≤ 0.05.** The observed maximum is 0.019. This bound is a labelled judgement, not derived from the bands.
- **No single per-prompt cut-off separates the bands.** Long in-domain prompts sit higher for every variant: q4 reaches 0.186 on the 573-token prompt, while q3 is only 0.107 on the 6-token prompt. That's why the rule is prompt-averaged or per-prompt.

**What the numbers support:**
- The thresholds sit between q4 and the disqualified variants, so the shipped q4 passes by construction. The test separates "q4-like" from q3 or broken, and the fidelity evidence is the measured values themselves.
- State it as measured, not as "faithful": against an independent PyTorch bf16 reference, the 4-bit model's next-token distributions differ by a mean KL of 0.075 nats/token (top-1 agreement 76–100 %). That holds on 6 prompts up to 573 tokens; the MLX implementation itself matches bf16 to ≤ 0.02.
- Prompts of 2k–17k tokens are untested, and KL rises with prompt length.

**Identity binding (code review w2-rev-c1, 28 Oct):**
- The collectors now write `<tag>__<prompt>.identity.json` beside every array: the prompt-ID digest, the array's SHA-256 and shape, the model's identity (pinned revision or content hash), settings and software versions.
- `calibrate.py` refuses missing, mismatched, mixed, partial or non-finite inputs, writes strict JSON (no NaN) and includes the collection identities in its result.
- **Re-collected with the bound scripts (28 Oct, ~1 GPU-min).** `calibration.json` is now identity-bound: all six collections carry identity records, and it includes the collection identities. Every number reproduced exactly: q4 0.0754, q3 0.2785, swap 0.2511, threshold 0.1376, floor 0.019, q4 = fresh quantisation. The unbound first run is kept as `calibration-unbound-20261028.json`. The prompt is now stowlm-prompt@3.0, with the same token IDs.

**Caveats:**
- Six prompts and two disqualifying controls are a small sample.
- A re-conversion that fails only narrowly should be checked against the per-prompt table, not decided on the average alone.
- The thresholds are for affine 4-bit, group 64, and this source revision. Any change to the quantisation or the base revision needs recalibration.

**Files:**
- `prep_prompts.py`, which writes the token IDs;
- `pt_reference.py`, run in `experiments/grpo-venue/.venv-pt`;
- `mlx_variants.py`;
- `calibrate.py`, which writes `calibration.json`.

The logits (~1 GB) stay in the scratchpad and are regenerable.

**Compute:** PyTorch 46.5 s at a 61.3 GB peak; MLX 23.8 s at a 57.0 GB peak. That is one GPU job, about 1.5 min in total, against an estimate of 20 min and 85 GB.

Run order:

```
uv run python prep_prompts.py OUT
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ../../grpo-venue/.venv-pt/bin/python pt_reference.py OUT mps
HF_HUB_OFFLINE=1 uv run python mlx_variants.py OUT
uv run python calibrate.py OUT calibration.json
```
