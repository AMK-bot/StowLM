# Prompt 3.0 sequence-length qualification (fix-t8-sft, 29 Oct 2026) — synthetic fixture, never evidence

**Example:** one synthetic 18,006-token example (11,965 prompt + 6,041 completion), the measured prompt-3.0 length of the dense 800-box pilot (`experiments/review/fixes/w2/w2-l4-format-review.md`).

**Run:** 3 QLoRA steps with the D31 config (last 16 layers, rank 8, `--attn-block 1024`, grad checkpointing). The new default `max_seq_length` is 18,432.

| step | s/step | processed tok/s | peak memory |
|---:|---:|---:|---:|
| 1 | 73.4 | 245 | 53.5 GB |
| 2 | 82.1 | 219 | 53.7 GB |
| 3 | 87.5 | 206 | 53.7 GB |

It fits: 53.7 GB peak, against D31's 53 GB at 17.7k. Throughput is about 205–245 tok/s; it fell across the 3 steps, probably thermal (unverified).

**D31 SFT estimate, updated for prompt 3.0** (prompts are 13–19 % longer):
- The same gold set costs about 15 % more tokens: 10–15M becomes **11.5–17.3M processed tokens**.
- At the measured 205–258 tok/s (~258 on ~600-token tiny examples, 205–245 at 18k), that is **≈ 12.4–23.4 GPU-h**, plus about 4 % for validation: **≈ 13–24 GPU-h** (was 14–21).
- The upper end assumes a mix weighted to long examples. A short-example-heavy curriculum sits near the bottom.
