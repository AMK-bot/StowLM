#!/bin/zsh
# Tiny chain step 3: diagnostic QLoRA SFT on unchecked stand-ins; outputs are never shippable.
# rank 8 / scale 20 / last 16 layers / all linears / lr 1e-4 / batch 4 / seq <=1024 / 200 iters (~5 epochs of 161).
set -u
uv run python -m llm.stowlm.train --diagnostic true --data-dir experiments/tiny-chain/sft \
  --adapter-dir experiments/tiny-chain/adapters/quick-v2-diagnostic \
  --rank 8 --scale 20 --num-layers 16 --learning-rate 1e-4 --iters 200 --batch-size 4 --max-seq-length 1024 \
  --grad-checkpoint false --steps-per-report 10 --steps-per-eval 50 --steps-per-save 50 --val-batches -1 "$@"
