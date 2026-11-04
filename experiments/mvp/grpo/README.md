# 10.1 GRPO prep: falsifier 4 and the launch path (29 Oct, w2-modal)

**Result: falsifier 4 passes, and the GRPO loop runs end to end.** Job spend: **$2.54 metered**. Project Modal total
is ≈ $7.1 including Volume storage.

## Falsifier 4: does the SFT policy give GRPO a signal? (D26 item 4: ≥ 30 % mixed groups)

**Why it matters:** GRPO's advantage is (reward − group mean) / group std. A group whose rewards are all equal gives
no gradient (Shao et al. 2024, DeepSeekMath §4).

**Setup:**
- **Policy:** the tiny SFT **pilot** adapter (`experiments/mvp/sft/pilot-tiny/adapter`, not 8.1). Converted to PEFT
  as `pilot-tiny-peft/`; the round trip is bit-identical.
- **Serving:** merged into the bf16 base on CPU with `merged_weights_from_checkpoint` (fp32, rounded once) and loaded
  into vLLM through the production `apply_exported_weights`. 15.5 GB, 20–27 s merge, 4–7 s load.
- **Rollouts:** 1× H100. 8 tiny **train** instances (one per vessel × generator, plus 2 from feeder_800) × G=8 at T=1.
  Production worker and mask, MVP setting (`modal_grpo.MVP_SPEC`: D79 C2 off + D95 securing layer, as `run_c1.py`
  uses).
- **Reward:** the full production reward on every rollout (`score_f4.py`: V4 finisher, D88, then the full checker).

**Results:**
- **Mixed groups: 8/8 = 100 %**, against the 30 % bar.
- 64/64 plans checker-passed, none dropped.
- Moves within a group span e.g. 6–20 (per-group lists are in `runs/f4-scored-*.json`). The pilot gets legality but
  varies widely on move quality, which is exactly the signal GRPO needs.

**Step timings (4 prompts × G8):**

| Phase | Time |
|---|---|
| Rollouts | 15–19 s (mask host share 13–17 %) |
| Scoring on the M5 | 18.3 s/plan mean (max 31 s), 550–620 CPU-s per step |
| Sync | 17–19 s |
| Update (smoke, 16 rollouts) | 8 s |

**APPROXIMATION:** the pilot adapter was trained on the MLX 4-bit base and is served on bf16 here. bf16 is GRPO's
real setting, not the MLX evaluation setting.

## GRPO loop smoke (`run_grpo.py`, 2× H100, pilot adapter, 1 step × 2 prompts × G8)

What ran, in order:
1. Base-only agreement floor: mean 0.0032, p99 0.048, max 0.114. It passes the D26 gate.
2. Out-of-place sync.
3. Batched masked rollouts.
4. Production rewards on the Mac: 16/16 passed, mean 9.0 moves, 2/2 groups mixed.
5. Agreement gate: passed.
6. Masked-log-prob update: grad norm 3.38, optimizer stepped.
7. Checkpoint to the `stowlm-grpo-checkpoints` Volume.

Timings: load 212 s, sync 19.4 s, rollouts 10.6 s, Mac scoring 101 s (4 workers under fleet load), update 8 s.

## Found and fixed on the way (all in `src/llm/rl/`, with tests)

**vLLM rejects the instance in `extra_args`.** vLLM requires every integer there to fit int64. Instances carry
256-bit provenance hashes, so the mask spec now carries the instance as a JSON string.

**The container's finisher failed every plan as `unknown:required_calculation`.** Root cause: `stowage.params`
locates the repo by `pyproject.toml`, which wasn't in the image, so vessel parameters couldn't be read. The image now
also ships `pyproject.toml` and `data/raw/kcs`.

**Modal CPU is far too slow for scoring.** With that fixed, one plan's V4 finisher takes **590 s on Modal CPU against
38 s on the M5**, with the same reward.
- So rewards are scored **on the Mac**. The GPU side is a warm Modal class (`GRPO.rollouts` / `GRPO.update`), and the
  local entrypoint scores between the two calls.
- The cause of the gap is unexplored; it was not cold caches.
- `llm.rl.score_batch` plus a numpy-2.5.4 venv stay in the image as a fallback: the vLLM lock pins numpy 2.3.5, under
  which the finisher also fails locally.

**Production now matches the MVP evaluation's settings:** mask (`MVP_SPEC`, `mvp_engine`) and reward
(`mvp_reward_config`: V4 finisher scoped to finalize). `run_gpu_smoke` uses them too.

**Training-image pins** (`src/llm/rl/training_image.json`, recorded 29 Oct):

| Component | Pinned version |
|---|---|
| schema | 1.9 |
| checker | 2.2 |
| feasibility | 2.5 |
| mask | stowlm-mask@1.2 |
| linear_model | 3.5 |
| ballast | 4.0 |
| move_engine | 2.0 |
| action_space | 1.0.1 |
| rule_catalogue | sha256:a34a24ed… |
| rl | stowlm-grpo@2.0 |

The `_scope` field records the setting the pins hold for: v4 MVP, C2 off (D79), securing D95, finisher V4 (D88),
rule freeze off.

## 10.1: exact start command (once 8.1 lands)

```sh
uv run python experiments/grpo-venue/convert_adapter.py mlx2peft <8.1 MLX adapter dir> experiments/mvp/grpo/sft-8.1-peft
# add a line for the Mac scoring workers to experiments/fleet/ledger.md (8 cores, ~10 GB)
OPENBLAS_NUM_THREADS=1 uv run --with modal modal run experiments/mvp/grpo/run_grpo.py::main --run-id grpo-101 \
  --adapter experiments/mvp/grpo/sft-8.1-peft --steps 25 --prompts 4 --group 8 --cap 20 --score-workers 8 --n-instances 64
```

**Estimate, unmeasured at 4 × 8 with SFT 8.1:**
- **Per step:** sync 19 s + rollouts ~20 s + Mac scoring 32 × ~20 s / 8 workers ≈ 80 s + update ~16 s ≈ **2.3 min**,
  about **$0.46/step** at ~$11.9/h for 2× H100.
- **Start-up:** load ≈ 3.5 min ≈ $0.7.
- **Totals:** 25 steps ≈ $12.5 and ≈ 1 h. $20 buys ≈ 35–40 steps.
- **With only 4 Mac workers** (the fleet is busy): ~3.6 min/step, ≈ $0.7/step.
- **Resume:** re-run the same command. Completed steps load from the Volume, and the per-run ledger keeps the cap
  cumulative.
