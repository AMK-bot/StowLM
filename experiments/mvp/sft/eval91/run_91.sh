#!/bin/zsh
# 9.1 evaluation, one command (v4; D128 recipe; D128.5 equal finisher budgets). Resumable (per-instance resume).
# Runs ONCE on test v2.1 with the adapter frozen on validation (D110: the loop's final_recipe on audit30 + reserve25);
# its numbers are reported, never used to pick or change the adapter, the recipe or the finisher budget.
# Main row "StowLM own plan": the cargo sequence is decoded by the fine-tuned model under the feasibility mask
# (securing + look-ahead layers, <= 32 bounded rewinds), greedy, sequential, no sampling; ballast by the deterministic
# V4 finisher (scoped to finalize); full checker. 5.7 repair is allowed but reported separately ("repaired pass").
# Two-step runs in the same pipeline with the SAME finisher budget (FINALIZE_S, default 120 s, = the v4.4 references').
# Finisher time limits are their own outcome, apart from planner dead ends and checker failures.
#   stowlm/   : D128 recipe (pass@1)
#   twostep/  : two-step, same finisher budget
#   sampled/  : optional (SAMPLES=k>0): k x T=0.7 for a best-of-(k+1) row
#   combine91 : SUMMARY.md / summary.json;  polish91: "StowLM + 15 s solver polish (hybrid)", separately labelled
# Usage: zsh experiments/mvp/sft/eval91/run_91.sh <frozen_adapter_dir> <out_dir> [time_limit_s=600]
set -euo pipefail
cd "$(dirname "$0")/../../../.."
ADAPTER=$1; OUT=$2; TL=${3:-600}; FIN=${FINALIZE_S:-120}; SAMPLES=${SAMPLES:-0}
FILES=($(uv run python experiments/mvp/sft/eval91/test_files.py ${=RUNGS:-tiny small}))
mkdir -p $OUT
print -l $FILES > $OUT/inputs.txt
export STOWLM_MLX_CACHE_LIMIT_GB=${STOWLM_MLX_CACHE_LIMIT_GB:-2}
COMMON=(--adapter $ADAPTER --loose $FILES --mask-workers 0 --time-limit $TL --d79 --securing --lookahead
        --ballast-v4-finalize --backtrack 32 --candidate-finalize-s $FIN --no-last-resort)
uv run python experiments/eval-c1/run_c1.py --run-dir $OUT/stowlm --n-samples 1 --temperature 0 $COMMON > $OUT/stowlm.log 2>&1
uv run python experiments/mvp/sft/ablation/twostep_val.py $OUT/inputs.txt $OUT/twostep $FIN > $OUT/twostep.log 2>&1
if (( SAMPLES > 0 )); then
  uv run python experiments/eval-c1/run_c1.py --run-dir $OUT/sampled --n-samples $SAMPLES --temperature 0.7 $COMMON \
    > $OUT/sampled.log 2>&1
fi
uv run python experiments/mvp/sft/eval91/combine91.py $OUT
uv run python experiments/mvp/sft/eval91/polish91.py $OUT
# Refresh SUMMARY with the separate hybrid rows after StowLM polish completes.
# The two-step hybrid is admitted only by its completed equality-guarded pre-run.
uv run python experiments/mvp/sft/eval91/combine91.py $OUT
echo "9.1 done: $OUT/SUMMARY.md"
