#!/bin/zsh
# 8.1 / 8.2 SFT StowLM, one command (v4; D117, D137). Resumable: rerun the identical command.
#   1. 7.1 build (once; skipped when $OUT/data/stats.json exists, so a resume never changes the data identity):
#      all tiny + small MVP train gold, mask replay with D79 + D95 securing, <= 10,240 tokens, 8 workers + row cache;
#      val = the tiny val gold + 24 small train-gold maps held out (deterministic by path hash; small has no val gold).
#   2. plan_run.py sizing with the 5.9 slim config (rank 8, 16 layers): 2 epochs if <= 8 GPU-h, else 1.
#   3. llm.stowlm.train: checkpoints every 100 micro-steps (adapter + Adam state + marker), val loss every 100.
#   4. Completion marker $OUT/DONE.json.
# Usage: zsh experiments/mvp/sft/run_81.sh [out_dir]
set -euo pipefail
cd "$(dirname "$0")/../../.."
OUT=${1:-experiments/mvp/sft/stowlm-v1}
G=experiments/mvp/gold
mkdir -p $OUT
if [[ ! -f $OUT/data/stats.json ]]; then
  VAL=(); for r in tiny small; do [[ -d $G/val/$r ]] && VAL+=($G/val/$r); done
  uv run python experiments/mvp/sft/dataset/build.py --workers ${BUILD_WORKERS:-8} --out $OUT/data \
    --train $G/train/tiny $G/train/small --val $VAL --holdout-val ${HOLDOUT_VAL:-24} --holdout-from $G/train/small \
    > $OUT/build.log 2>&1
fi
if [[ ! -f $OUT/train.cmd ]]; then
  if ! uv run python experiments/mvp/sft/plan_run.py --data-dir $OUT/data --adapter-dir $OUT/adapter --epochs 2 \
       > $OUT/plan.log 2>&1; then
    uv run python experiments/mvp/sft/plan_run.py --data-dir $OUT/data --adapter-dir $OUT/adapter --epochs 1 > $OUT/plan.log
  fi
  echo "$(tail -1 $OUT/plan.log) --steps-per-eval ${STEPS_PER_EVAL:-100} --val-batches -1" > $OUT/train.cmd  # frozen at first launch
fi
eval "$(cat $OUT/train.cmd)" >> $OUT/train.log 2>&1
python3 -c "
import json; r=json.load(open('$OUT/adapter/run.json'))
json.dump({'marker': '8.1 SFT complete', 'adapter': '$OUT/adapter', 'wall_s': r.get('wall_s'),
           'val_loss_last': r.get('val_loss_last'), 'iterations_run': r.get('iterations_run')}, open('$OUT/DONE.json', 'w'), indent=1)"
echo "8.1 done: $OUT/adapter"
