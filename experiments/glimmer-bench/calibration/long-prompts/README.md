# Conversion calibration at full-ship prompt lengths (0.1 extension, 29 Oct 2026)

**Setup:**
- The metric and the independent reference are the same as in `../calibration.json`: KL(PyTorch bf16 ‖ MLX variant) in nats/token, with the reference in transformers on MPS.
- **Threshold unchanged:** mean KL ≤ 0.1376.
- **Prompts** (prompt 3.0, switch on), each followed by an answer continuation of action tokens:

  | prompt | tokens | continuation |
  |---|---:|---|
  | timing-pilot full fixture | 3,715 + 1,023 | format-valid Place actions |
  | perf-1028 `measure-full` (the format review's full-ship source) | 6,261 + 1,024 | format-valid Place actions |
  | dense 800-box pilot | 11,965 + 2,048 | its archived format-only 800-action completion, re-encoded |

- **Scored positions:** the whole continuation plus the last 1,024 prompt positions, with the full prefix in context.
- **Inputs are identity-bound** (`identity.py`; `analyze.py` refuses a mismatch).
- **Memory:** both stacks use chunked prefill with a KV cache (2,048-token chunks), and keep logits only for scored positions. Chunked vs one forward pass on the 4.7k prompt: mean KL 0.0005, top-1 agreement 99.6 % (bf16 accumulation noise). The 14k context fits, with no need to shorten it.

| length (prompt + continuation) | variant | mean KL, all | prompt tail | continuation | max KL | positions > 0.1376 | top-1, all / continuation |
|---|---|---:|---:|---:|---:|---:|---:|
| 4.7k (3,715 + 1,023) | MLX 4-bit | **0.027** | 0.028 | 0.027 | 4.48 | 95/2047 (4.6 %) | 0.970 / 0.987 |
| 7.3k (6,261 + 1,024) | MLX 4-bit | **0.041** | 0.069 | 0.013 | 5.33 | 196/2048 (9.6 %) | 0.891 / 0.989 |
| 14.0k (11,965 + 2,048) | MLX 4-bit | **0.030** | 0.075 | 0.007 | 9.74 | 215/3072 (7.0 %) | 0.954 / 0.997 |
| all three | MLX bf16 (implementation floor) | ≤ 0.0015 | ≤ 0.0026 | ≤ 0.0005 | 0.30 | 0–2 | ≥ 0.975 |

**Result:** the prompt-averaged 4-bit mean KL is **0.0325, below the 0.1376 threshold**, and every length passes on its own.
- On the action tokens, KL is lowest and top-1 agreement is 98.7–99.7 %.
- The prompt tail sits at 0.03–0.075, the same band as the short-prompt calibration (q4 0.016–0.186).
- **Single positions:** 4.6–9.6 % exceed 0.1376, and the maximum is 9.7. These are isolated prompt-tail tokens; the threshold is defined on the mean.

**Compute:**

| run | wall | peak memory |
|---|---:|---|
| PyTorch | 133 s | 83.7 GB footprint |
| MLX bf16 | 13–32 s per prompt | 58.6 GB |
| MLX 4-bit | 12–32 s per prompt | 18.4 GB |

**Files:** `prep.py`, `pt_reference_long.py`, `mlx_long.py`, `analyze.py` → `long-calibration.json`. The logits stay in the scratchpad.

**Line for plan 0.1 / docs/evaluation.md:** "The 4-bit conversion holds at full-ship length. Against an independent PyTorch bf16 reference, at 4.7k, 7.3k and 14.0k tokens (prompt 3.0 + action continuation), the mean KL is 0.027/0.041/0.030 nats/token (threshold 0.1376), and action-token top-1 agreement is 98.7–99.7 %. The MLX implementation matches bf16 to ≤ 0.0015."

## Known-bad variants at ~7.3k tokens (w2-rev-calib gap 1, 29 Oct)

**Setup:** `separation.py` → `separation.json`. Same reference, same 2,048 scored positions as the 4-bit run on `measure_full_6k` (6,261 + 1,024), identity-bound.

| variant | mean KL, all | prompt tail | continuation | top-1, all |
|---|---:|---:|---:|---:|
| MLX bf16 (implementation floor) | 0.0015 | 0.0026 | 0.0004 | 0.975 |
| **shipped 4-bit** | **0.041** | 0.069 | 0.013 | 0.891 |
| 4-bit, blocks 20↔21 swapped (bad) | **0.084** | 0.141 | 0.027 | 0.858 |
| 3-bit (bad) | 0.198 | 0.349 | 0.046 | 0.852 |

**The ordering holds, but the fixed threshold fails:**
- The 4-bit model stays about **2.1× below** the nearest bad variant: the swap, in every region. The short-prompt calibration had 3.3×.
- **The 0.1376 threshold does not separate at this length.** The layer-swapped model scores 0.084 and would pass.
- **In the prompt tail alone**, 0.1376 does separate, but only just (swap 0.141).
- **The bad variants' KL falls with length.** Short prompts: swap 0.251, 3-bit 0.278. On the action continuation, the two bad models and q4 differ by only 0.013–0.046.
- **A length-specific cut would separate this one prompt:** the geometric mean of q4 and the swap is 0.058 for all positions. One prompt can't calibrate it, and the threshold is unchanged here.

**Corrected claim line:** "At ~7.3k tokens (one full-ship prompt plus 1,024 action tokens) the 4-bit model's mean KL to the bf16 reference is 0.041. That is about 2× below a layer-swapped 4-bit control (0.084) and 5× below 3-bit (0.198), so the ranking holds. But the 0.1376 acceptance threshold, calibrated on prompts of ≤ 573 tokens, would accept the swapped control at this length, so it is not a full-length acceptance test. A length-specific threshold needs more full-length prompts."
