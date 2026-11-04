#!/bin/zsh
# Tiny SFT PILOT (leader order 29 Oct 12:45; NOT 8.1): mvp_gold tiny dataset -> slim config, short run (<= 1.5 GPU-h)
# -> masked best-of-1 on the 15 frozen tiny test instances, no repair, D79, securing layer (D95), V4 finisher (D88).
# Usage: zsh experiments/mvp/sft/pilot_tiny.sh <epochs> <holdout_val>
set -euo pipefail
cd "$(dirname "$0")/../../.."
EPOCHS=${1:-3}; HOLD=${2:-15}
P=experiments/mvp/sft/pilot-tiny
VAL=()
[[ -d experiments/mvp/gold/val/tiny ]] && VAL=(--val experiments/mvp/gold/val/tiny)
if [[ ! -f $P/data/stats.json ]]; then
  uv run python experiments/mvp/sft/dataset/build.py --out $P/data --train experiments/mvp/gold/train/tiny $VAL \
    $([[ ${#VAL} -eq 0 ]] && echo --holdout-val $HOLD) > $P/build.log 2>&1
fi
CMD=$(uv run python experiments/mvp/sft/plan_run.py --data-dir $P/data --adapter-dir $P/adapter --epochs $EPOCHS | tail -1)
echo "$CMD" > $P/train.cmd
eval "$CMD --steps-per-eval 100 --val-batches -1" > $P/train.log 2>&1
T=experiments/mvp/corpus/test/tiny
uv run python experiments/eval-c1/run_c1.py --run-dir experiments/mvp/runs/sft-pilot-tiny --adapter $P/adapter \
  --loose $(ls $T/*/*/seed-*.json | grep -v audit) --n-samples 1 --temperature 1 --mask-workers 0 --time-limit 1200 \
  --d79 --no-repair --securing --ballast-v4-finalize > $P/eval.log 2>&1
echo done
