#!/bin/zsh
# PILOT 2 (leader 18:05 / D110; not 8.1): all tiny gold + the small gold available now -> slim config, <= 1.5 GPU-h
# (2 epochs if they fit, else 1), then the pass-rate ablation on the 15 small VALIDATION maps (val15; never test),
# soft GPU budget 45 min, then the completion markers mvp-exit's deployment waits for.
# Resumable: rerun the identical command (data and train command are frozen at first launch).
set -euo pipefail
cd "$(dirname "$0")/../../.."
P=experiments/mvp/sft/pilot2; G=experiments/mvp/gold
mkdir -p $P
if [[ ! -f $P/data/stats.json ]]; then
  uv run python experiments/mvp/sft/dataset/build.py --workers ${BUILD_WORKERS:-8} --out $P/data --train $G/train/tiny $G/train/small \
    --val $G/val/tiny $([[ -d $G/val/small ]] && echo $G/val/small) > $P/build.log 2>&1
fi
if [[ ! -f $P/train.cmd ]]; then
  uv run python experiments/mvp/sft/plan_run.py --data-dir $P/data --adapter-dir $P/adapter --epochs 2 --cap-h 1.5 \
    > $P/plan.log 2>&1 || uv run python experiments/mvp/sft/plan_run.py --data-dir $P/data --adapter-dir $P/adapter \
    --epochs 1 --cap-h 1.5 > $P/plan.log
  echo "$(tail -1 $P/plan.log) --steps-per-eval 200" > $P/train.cmd
fi
[[ -f $P/adapter/adapters.safetensors && -f $P/TRAIN.DONE ]] || { eval "$(cat $P/train.cmd)" >> $P/train.log 2>&1 && touch $P/TRAIN.DONE; }
[[ ${STOP_AFTER_TRAIN:-0} == 1 ]] && { echo "trained; stopping before the ablation"; exit 0; }
zsh experiments/mvp/sft/ablation/run_ablation.sh $P/adapter $P/val15 "a c d b e" 300 ${LOOKAHEAD:-1} 2700 > $P/ablation.log 2>&1
cp $P/val15/ablation.json $P/DONE.json
mkdir -p experiments/mvp/runs/sft-pilot2-small  # handshake path read by experiments/mvp/exit/deploy_when_ready.py
python3 -c "
import json; d=json.load(open('$P/val15/ablation.json'))
json.dump({'marker':'fix-t8-sft pilot 2 + val15 ablation complete; GPU released','adapter':'$P/adapter',
           'ablation':'$P/val15/ABLATION.md','rows':d}, open('experiments/mvp/runs/sft-pilot2-small/polish_summary.json','w'), indent=1)"
echo "pilot 2 done"
