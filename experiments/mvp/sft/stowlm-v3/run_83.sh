#!/bin/zsh
# D151 8.3 SFT then D152 validation, one command, on the host (Metal). Same recipe as 8.2: its train.cmd with only the
# dirs and iters changed (2 epochs over the final rows), from the base model. Resumable: rerun the identical command.
# Refuses to start without READY-83.json. Validation: experiments/mvp/sft/final-validation/run_adapter.py on audit30,
# reserve25 and tiny val48, the script logic-v4-decode ran for 8.2, so the rows pair map by map.
set -euo pipefail
cd "$(dirname "$0")/../../../.."
OUT=experiments/mvp/sft/stowlm-v3
export UV_CACHE_DIR="$PWD/.uv-cache" STOWLM_MLX_CACHE_LIMIT_GB=8
unset STOWAGE_BALLAST_V4
[[ -f experiments/mvp/gold/twostep-teacher/READY-83.json && -f $OUT/data/manifest.json ]] || { echo "dataset not ready"; exit 1; }
if [[ ! -f $OUT/train.cmd ]]; then
  ROWS=$(wc -l < $OUT/data/train.jsonl | tr -d ' ')
  ITERS=$(( (ROWS * 2) / 4 * 4 ))
  sed -e 's#stowlm-v2/#stowlm-v3/#g' -e "s#--iters 2504#--iters $ITERS#" experiments/mvp/sft/stowlm-v2/train.cmd > $OUT/train.cmd
fi
if [[ ! -f $OUT/DONE.json ]]; then
  eval "$(cat $OUT/train.cmd)" >> $OUT/train.log 2>&1
  python3 -c "
import json; r=json.load(open('$OUT/adapter/run.json'))
json.dump({'marker': '8.3 SFT complete', 'adapter': '$OUT/adapter', 'wall_s': r.get('wall_s'),
           'val_loss_last': r.get('val_loss_last'), 'iterations_run': r.get('iterations_run')}, open('$OUT/DONE.json', 'w'), indent=1)"
fi
V=experiments/mvp/valsplit
export STOWLM_MLX_CACHE_LIMIT_GB=2
for pair in audit_small30.txt:audit30 reserve_small25.txt:reserve25 tiny_val48.txt:tiny48; do
  uv run python experiments/mvp/sft/final-validation/run_adapter.py $OUT/adapter 8.3 $V/${pair%%:*} ${pair##*:} \
    >> $OUT/validation.log 2>&1
done
date > $OUT/VALIDATED
