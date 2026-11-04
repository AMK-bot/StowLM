#!/bin/zsh
# Pass-rate ablation on the 15 small VALIDATION maps (D110: never test; experiments/mvp/valsplit/ablation_small15.txt)
# to choose 9.1's decode setup. All rows: masked, D79, D95 securing layer, V4 finisher scoped to finalize, no repair,
# finisher-aware 90 s per candidate (so a poor complete plan cannot grind the finisher), own-plan pass vs two-step on
# the same maps (twostep_val.py). Resumable (run_c1 loose resume per map). GPU budget: the caller's watchdog.
#   a  greedy
#   c  greedy + backtracking (<= 32) + look-ahead layer (if green; else without, noted)
#   b  greedy + backtracking (<= 32), no look-ahead (only if time allows)
#   d  best-of-k (BEST_K, default 4) = greedy + (k-1) x T=0.7, c's decoding
#   e  d + 15 s LNS polish (CPU; labelled; never worse than d)
# Usage: zsh run_ablation.sh <adapter> <out_dir> [rows="a c d b e"] [time_limit_s=300] [lookahead=1] [gpu_budget_s=2700]
# The GPU budget is soft: a GPU row (a-d) starts only while elapsed < budget; row e is CPU.
set -uo pipefail
cd "$(dirname "$0")/../../../.."
ADAPTER=$1; OUT=$2; ROWS=(${=3:-a c d b e}); TL=${4:-300}; USE_LOOK=${5:-1}; BUDGET=${6:-2700}
mkdir -p $OUT
MAPS=($(cat experiments/mvp/valsplit/ablation_small15.txt))
BASE=(--adapter $ADAPTER --mask-workers 0 --d79 --no-repair --securing --ballast-v4-finalize --candidate-finalize-s 90)
LOOK=(); [[ $USE_LOOK == 1 ]] && uv run python -c "import llm.lookahead" 2>/dev/null && LOOK=(--lookahead)
[[ ${#LOOK} -eq 0 ]] && echo "look-ahead layer not used" > $OUT/LOOKAHEAD-OFF
run() { local row=$1; shift; uv run python experiments/eval-c1/run_c1.py --run-dir $OUT/$row "$@" > $OUT/$row.log 2>&1; echo "$row rc=$? $(date +%H:%M)"; }
for r in $ROWS; do
  if [[ $r != e ]] && (( SECONDS >= BUDGET )); then echo "$r skipped: GPU budget ${BUDGET}s used" | tee $OUT/$r.SKIPPED; continue; fi
  case $r in
    a) run a --loose $MAPS $BASE --n-samples 1 --temperature 0 --time-limit $TL ;;
    b) run b --loose $MAPS $BASE --n-samples 1 --temperature 0 --backtrack 32 --time-limit $TL ;;
    c) run c --loose $MAPS $BASE $LOOK --n-samples 1 --temperature 0 --backtrack 32 --time-limit $TL ;;
    d) run d --loose $MAPS $BASE $LOOK --n-samples ${BEST_K:-4} --greedy-first --temperature 0.7 --backtrack 32 --time-limit $((TL * 2)) ;;
    e) uv run python experiments/mvp/sft/ablation/polish_row.py $OUT/d $OUT/e > $OUT/e.log 2>&1; echo "e rc=$?" ;;
  esac
done
uv run python experiments/mvp/sft/ablation/summarize.py $OUT
