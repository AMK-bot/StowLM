#!/bin/zsh
# Scale probe chain, one process at a time: validation calibration (tiny, small), full ship, then medium maps 3 and 4.
# Full ship: 5,400 s, the solver's budget; --max-tokens 60000 because 20,000 tokens is only 2,857 actions (3.5 per box),
# below the 3.8-4.6 per box seen on medium. Everything else is the 9.1 recipe, no repair.
set -u
cd "$(dirname "$0")/../../../.."
S=experiments/mvp/strategy/scale; R=$S/runs
export STOWLM_MLX_CACHE_LIMIT_GB=2
COMMON=(--adapter experiments/mvp/sft/stowlm-v2/adapter --n-samples 1 --temperature 0 --mask-workers 0 --d79 --securing
        --lookahead --ballast-v4-finalize --backtrack 32 --candidate-finalize-s 120 --no-last-resort --no-repair)
maps() { uv run python -c "import json,sys;print(' '.join(json.load(open('$1'))$2))"; }
uv run python $S/run_scale.py --run-dir $R/calib --loose ${=$(maps $S/calib_maps.json '')} --time-limit 600 $COMMON > $R/calib.log 2>&1
echo calib done $(date)
uv run python $S/run_scale.py --run-dir $R/full --loose ${=$(maps $S/maps.json '[4:]')} --time-limit 5400 --max-tokens 60000 $COMMON > $R/full.log 2>&1
echo full done $(date)
uv run python $S/run_scale.py --run-dir $R/medium1800 --loose ${=$(maps $S/maps.json '[2:4]')} --time-limit 1800 $COMMON > $R/medium1800.log 2>&1
echo medium done $(date)
