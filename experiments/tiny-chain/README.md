# Tiny chain, end to end (27 Oct 2026, stow-gpu)

**Historical diagnostic evidence only.** These artifacts predate splits v2,
prompt 2.1, mask 1.2 and the bound D50 gold-record contract. The 6.1 repair has
published zero current clean gold records. Use `experiments/sft-data/build.py`
for the current admission pipeline; this directory cannot certify task 7.1.

The later `synthetic-mask-fixtures/` subdirectory contains four deterministic
1/2/3-box diagnostics regenerated with prompt 2.1 and mask 1.2, distinct from
the historical pilot below. They replay 4/4 through the real decoder/hull-free
engine, retain diagnostic taint, and have zero production verified examples.
Reproduce with `experiments/sft-data/verify_fixture.py`; see its evidence JSON.

Plan rule "ship tiny first": generate → solve → serialise → SFT → masked eval, once, on the v2 **tiny** rung
(6 dry 20' boxes; `experiments/instances-v2/`, splits from the manifest). Everything ran. **Everything is
stub-tainted**, because the chain only gets past step 1 with a stub (below). Nothing here is gold, shippable or
evaluable. The code it exercised is real 7.1/8.1 code: `src/llm/stowlm/{sft_data,train,planner}.py`.

## What broke, and where

| # | Where | What | Effect |
|---|---|---|---|
| 1 | 3.7 linear model ← 4.4 engine (`stowage/linear/physics.py:345`, `feasibility/_engine.py:157-165`) | Building the engine on a v2 tiny instance runs the validity-band sweep. It takes ~170 s and fails: "accepted only 0 of 64 required conditions in 3200 attempts". C2 is then unavailable, so the engine fails closed with a **dead end at depth 0**. | Every engine-based planner fails: exact A* (231 s, 0 nodes, 30 s limit ignored), scalable (268 s), two-step (275 s). **No gold plan at all** on the real path. |
| 2 | 4.2 ballast / 3.1 stability (`solver/ballast.py`, `checker/stability*`) | With C2 switched off, a complete tiny plan (6 boxes on an almost empty feeder) fails IS Code A 2.3 weather ("outside applicability / no steady equilibrium"), the B 2.3 containership second check, and for feeder_1700 the draught/trim limits. `finish_ballast` returns `not_found` after 120–140 s on all three vessels. | **0 checker-passing plans.** 0/4 in the survey and 0/12 stand-in plans checked. The light-ship departure condition can't be ballasted, or the finisher can't find it. Owners: 3.1/4.2, not this order. |
| 3 | 4.6 relaxed oracle (`solve_exact(check_physics=False)`) | It times out at 30 s on most tiny instances that need ≥1 relocation (Dijkstra over full boxslot, ~12k states). `default_relocations_only=True` also times out. | Replaced by the scalable solver over the C2-off engine. That takes 0.2–4.5 s per instance and is optimal on every solved instance (objective = 3.6 lower bound, gap 0). |
| 4 | 4.4 engine C1 | On 75/241 tiny train+val instances: "every candidate breaks a placement rule or a completion bound (capacity)". 3 of the 9 val instances are dead at depth 0 for every planner. | 166/241 stand-in plans. Either the generator ships instances the engine calls infeasible, or C1 is too strict. This needs a G1 comparison by the 4.4/1.x owners. |
| 5 | mlx-lm 0.32.2 `tuner/trainer.default_loss` | Its loss mask is `steps <= length`, which trains **one padding token (id 0) after every sequence**. | Fixed locally by `train.completion_loss` (`steps < length`). The test proves both behaviours. Worth an upstream issue. |
| 6 | Resume in `train.py` (first version) | Resuming with fresh Adam moments sent the loss from 0.45 to 2.5 in 5 steps. | Optimizer state is now checkpointed and restored exactly (unit test: identical next update). A resumed run still spiked once, but so did an uninterrupted run at iteration 30. See "SFT". |

The stub, used for every step below, overrides `feasibility.make_engine` through `stowage.frozen.stub_override` with
the real `IncrementalEngine` and `extra={"global_screen": 0}`, the engine's own diagnostic switch. It turns off C2
(the global stability/strength screen). The L1/L2 local checks and C1 capacity stay on, and the full checker still
judges every plan. Provenance carries `feasibility.make_engine` and `tiny_chain.gold_standin`. `require_clean`,
`save_plan` and `write_dataset` all refuse these artefacts outside a `tiny-chain` folder.

## 1. Gold plans (`gold.py`)

- **strict mode** (exact A* → `finalize` → `check`, keep only passes): **0 plans** (breaks 1–2).
- **standin mode** (scalable over the C2-off engine; each action replayed through `legal_actions()`): 166/241 =
  161 train + 5 val.
  - Optimal on all 166 (gap 0 to the lower bound).
  - Weighted extra lifts: 0 on 105, 1 on 32, 5 on 29.
  - `finalize` + `check` on all 5 val plans and 7 train plans: 0/12 passed, ballast `not_found`. (Of the 12 train
    plans sampled for checking, 5 had dead-ended, so they produced no plan to check.)
- Plans are stored as structured `MovePlan`s with `actions`, in `gold/standin/<split>/`. `index.jsonl` holds one row
  per instance.

## 2. SFT data (`build_sft.py` → `sft/`; code `llm.stowlm.sft_data`)

- **Format:** 4.5 v2: `encode_prompt` (native chat framing, assistant turn fixed `to=user`) followed by
  `ActionCodec.encode_plan`, all through the shared token-ID builder (D9). Loss is on the completion only
  (`prompt_len` offset).
- **Lengths:** prompt 501–550 tokens (p50 523); completion 43 tokens (6 actions × 7 + end-of-plan); total ≤ 593.
  **7.17 tokens per action** (includes the end token).
- **Pilot-only replay:** **166/166 examples replay through the historical decoder backed by a C2-off stub**, with
  no masked token, in 5.2 s total. This is **zero production-verified gold examples** and does not meet 7.1's criterion.

## 3. Quick SFT (`run_sft.sh`; code `llm.stowlm.train`)

Current runs explicitly pass `--diagnostic true`. All datasets, adapters and
evaluation outputs from this chain are **diagnostic only, never shippable**.
Trainer 2.0 refuses legacy checkpoint resume: the script uses the fresh
`adapters/quick-v2-diagnostic/` directory. The measurements below describe the
historical run, not a retraining under the current contract.

| | |
|---|---|
| Base | `models/glimmer-30b-4bit` (MLX affine 4-bit) |
| LoRA | rank 8, scale 20, dropout 0, last 16 layers, all linears (16.1M trainable) |
| Optimiser | Adam, lr 1e-4 constant, batch 4, 200 iterations (~5 epochs of 161), seq ≤ 1,024, no grad checkpoint |
| Loss | val 2.87 → 0.69 (it 100) → 0.62 (it 150) → **0.45 (it 200)**; train 1.04 (it 10) → 0.51 (it 200), spike to 3.39 at it 30 |
| Speed | 33 → 20 trained tok/s. mlx-lm counts loss tokens only, so that is ≈ 450 → 270 processed tok/s. The drop came from GPU throttling plus load average 30–50 from other jobs. |
| Peak / wall | 35.1 GB / 27.6 min |
| Checkpoints | `adapters/quick/0000{050,100,150,200}_adapters.safetensors`. That run predates optimizer checkpoints. |

**Loop, eval loss, checkpointing and resume work.** Resume restores the adapter exactly: val loss at the resume
point was 0.455 against 0.454 before. It now also restores the optimizer state exactly.

Two short runs show lr 1e-4 without warmup is unstable here:
- A 10+10 resumed run spiked to val 2.14.
- A continuous 20-iteration control didn't spike (val 0.70).
- The main run spiked at iteration 30.

mlx-lm's batch shuffle isn't seeded or restored on resume, so the two short runs saw different data orders. **For
8.1, use lr 2e-5 to 5e-5 with warmup.** That is a recommendation; I didn't measure it.

## 4. Masked eval, tiny val (`eval.py` → `eval/`; code `llm.stowlm.planner`)

Greedy decoding, the 5.1 mask over the C2-off engine, batch 1. Then `finalize` and `check`, run on CPU workers.

| Planner | Complete | Dead end | **Checker pass** | Extra weighted lifts vs stand-in gold (5 val with gold) | Time per plan |
|---|---|---|---|---|---|
| `stowlm` (quick adapter) | 5/9 | 4/9 | **0/9** (ballast `not_found` on all 5) | +0, +0, +2, +0, +4 (total +6) | 3.8 s (gen ~14 tok/s) |
| `stowlm.base` (no adapter) | 5/9 | 4/9 | **0/9** | +0, +0, +4, +2, +3 (total +9) | 3.2 s (~17 tok/s) |

- **Dead ends:** 3 of the 4 are depth-0 engine dead ends, so those instances are unsolvable under the engine. The
  fourth, `tiny.84`, dead-ends after 5 actions under both planners. The scalable solver had no plan for it either.
- **Mask:** 100% of generated plans are mask-legal by construction. Pass rate is 0 because of break 2, not because
  of the model.
- **Moves:** comparing 5 vs 5 plans is anecdotal. The adapter has not learned to beat the base yet; it was trained
  on 161 near-identical 6-box examples.
- **Prefill:** the prompt prefill is ~1–2 s of each plan's time.

## Compute (estimate → actual)

| Job | Estimate | Actual |
|---|---|---|
| Ballast survey | 4 cores, 5 min | 3 min |
| Gold stand-in | 4 cores, 10–15 min | ~16 min |
| Quick SFT | GPU, 30 GB, ~17 min | 35.1 GB, 27.6 min |
| Eval | GPU + 4 cores, 10 min | 1.6 min |
| Resume tests + GPU pytest | 5 min | ~6 min |

The SFT waited ~25 min for stow-mask's GPU job (`gpu-measure`, estimated 25 min, actually ~47 min).

## Reproduce

```
uv run python experiments/tiny-chain/gold.py --mode standin --splits train val --workers 4 --check-sample 12
uv run python experiments/tiny-chain/build_sft.py
./experiments/tiny-chain/run_sft.sh --diagnostic true   # diagnostic only; GPU: ledger line first
uv run python experiments/tiny-chain/eval.py --adapter experiments/tiny-chain/adapters/quick-v2-diagnostic --workers 4
uv run pytest -q tests/llm/test_sft_data.py tests/llm/test_train.py tests/llm/test_stowlm_planner.py   # -m gpu for the smoke
```
