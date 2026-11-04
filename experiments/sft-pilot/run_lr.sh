#!/bin/zsh
# lr/warmup pilot on the stub-tainted tiny stand-in set (experiments/tiny-chain/sft: 161 train / 5 val, ~567 tokens).
# Trainer 2.0 (fix-t8-sft): non-gold data needs --diagnostic true. Logged results were made with trainer 1.x.
# Same seed (data order + init) for every run; 150 iterations at batch 4 (~3.7 epochs); eval every 10.
set -u
run() { # NAME LR SCHEDULE WARMUP
  [[ -f experiments/sft-pilot/adapters/$1/run.json ]] && return
  uv run python -m llm.stowlm.train --data-dir experiments/tiny-chain/sft --adapter-dir experiments/sft-pilot/adapters/$1 \
    --resume false --diagnostic true --seed 0 --rank 8 --scale 20 --num-layers 16 --learning-rate $2 --lr-schedule $3 --warmup-frac $4 \
    --min-lr-frac 0.1 --iters 150 --batch-size 4 --max-seq-length 1024 --grad-checkpoint false \
    --steps-per-report 5 --steps-per-eval 10 --steps-per-save 50 --val-batches -1 > experiments/sft-pilot/$1.log 2>&1
}
run lr-2e-5-cos-w5 2e-5 cosine 0.05
run lr-5e-5-cos-w5 5e-5 cosine 0.05
run lr-1e-4-cos-w5 1e-4 cosine 0.05
run lr-1e-4-const 1e-4 constant 0.0
echo LR DONE
