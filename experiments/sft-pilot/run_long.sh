#!/bin/zsh
# Long-sequence QLoRA feasibility (8.1 code, llm.stowlm.train): batch 1, grad checkpointing on, 3 steps per config.
# Trainer 2.0 (fix-t8-sft): non-gold data needs --diagnostic true. Logged results were made with trainer 1.x.
# args: TAG LAYERS [ATTN_BLOCK]   (data experiments/sft-pilot/data/TAG; adapter .../adapters/long-TAG-lLAYERS[-bBLOCK])
# A watchdog stops the run if system free memory drops below 10% (stay <= ~100 GB of 128).
set -u
TAG=$1; L=$2; B=${3:-0}
NAME=long-$TAG-l$L$([[ $B != 0 ]] && echo -b$B)
uv run python -m llm.stowlm.train --data-dir experiments/sft-pilot/data/$TAG \
  --adapter-dir experiments/sft-pilot/adapters/$NAME --resume false --diagnostic true --attn-block $B \
  --rank 8 --scale 20 --num-layers $L --learning-rate 1e-5 --iters 3 --batch-size 1 --max-seq-length 18432 \
  --grad-checkpoint true --steps-per-report 1 --steps-per-eval 100 --steps-per-save 3 --val-batches 1 &
PID=$!
while kill -0 $PID 2>/dev/null; do
  FREE=$(memory_pressure | awk '/free percentage/ {gsub("%","",$5); print $5}')
  if [[ -n $FREE && $FREE -lt 10 ]]; then echo "WATCHDOG: free memory ${FREE}% - stopping $PID"; kill -TERM $PID; fi
  sleep 5
done
wait $PID
