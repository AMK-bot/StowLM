#!/bin/zsh
# 9.2 final StowLM showcase runs, one command (after the validation freeze and the single 9.1 test run). The 9.1 recipe exactly (D128; D128.5 equal finisher
# budget): greedy, sequential, mask + securing + look-ahead, <= 32 rewinds, V4 finisher scoped to finalize at
# FINALIZE_S (default 120 s, same as 9.1), MLX cache limit, NO repair (a repaired plan must never appear as StowLM's).
# Output: experiments/mvp/runs/stowlm-showcase/ in the layout mvp-ui's loader reads (manifest.json, config.json,
# instances/<id>/{result,trace.jsonl,outcome,report}.json), then a fresh full check of every shipped plan (verify.json).
# Dead ends / time limits stay recorded as such (the demo shows them honestly). Resumable (per-instance).
# Usage: zsh experiments/mvp/sft/eval92/run_92.sh <frozen_adapter_dir> [out_dir=experiments/mvp/runs/stowlm-showcase]
set -euo pipefail
cd "$(dirname "$0")/../../../.."
ADAPTER=$1; OUT=${2:-experiments/mvp/runs/stowlm-showcase}; TL=${TIME_LIMIT_S:-600}; FIN=${FINALIZE_S:-120}
[[ -f $ADAPTER/adapters.safetensors && -f $ADAPTER/adapters.safetensors.provenance.json ]] || { echo "adapter or its lineage sidecar missing: $ADAPTER"; exit 2; }
FILES=($(uv run python experiments/mvp/sft/eval92/showcase_files.py))
export STOWLM_MLX_CACHE_LIMIT_GB=${STOWLM_MLX_CACHE_LIMIT_GB:-2}
mkdir -p $OUT
uv run python experiments/eval-c1/run_c1.py --run-dir $OUT --adapter $ADAPTER --loose $FILES --n-samples 1 \
  --temperature 0 --mask-workers 0 --time-limit $TL --d79 --securing --lookahead --ballast-v4-finalize \
  --backtrack 32 --candidate-finalize-s $FIN --no-last-resort --no-repair > $OUT/run.log 2>&1
uv run python experiments/mvp/sft/eval92/verify92.py $OUT > $OUT/verify.log 2>&1 || { echo "a stored ok plan failed the fresh checker: see $OUT/verify.json"; exit 1; }
# Two-step rival: reuse showcase-v2-twostep when it has all three calls (mvp-ui reads it there).
for f in $FILES; do
  id=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['id'])" $f)
  [[ -f experiments/mvp/runs/showcase-v2-twostep/instances/$id/result.json ]] || echo "two-step rival missing for $id (showcase-v2-twostep)" | tee -a $OUT/verify.log
done
# Optional: StowLM under the D144 instruction (one call). Same recipe and single-finisher rule (D139) as above; it never
# fails 9.2 and isn't registered as a showcase run. mvp-ui's contract: experiments/mvp/ui/STOWLM-INSTRUCTION-RUN.md.
TIME_LIMIT_S=$TL FINALIZE_S=$FIN uv run python experiments/mvp/ui/run_stowlm_instruction.py --adapter $ADAPTER > experiments/mvp/runs/stowlm-instruction-dg.log 2>&1 || echo "optional instruction run: not shown (see experiments/mvp/runs/stowlm-instruction-dg/instruction-check.json)" | tee -a $OUT/verify.log
cat $OUT/verify.json
echo "9.2 done: $OUT"
