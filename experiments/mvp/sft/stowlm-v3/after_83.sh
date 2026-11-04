#!/bin/zsh
# D152: when run_83.sh has validated 8.3, run 8.2 on tiny48 if logic-v4-decode's run didn't finish (it needs the GPU,
# exclusive to 8.3 until now), then write VALIDATION.md and the docs/evaluation.md section, and release the ledger row.
set -uo pipefail
cd "$(dirname "$0")/../../../.."
OUT=experiments/mvp/sft/stowlm-v3
F=experiments/mvp/sft/final-validation
export UV_CACHE_DIR="$PWD/.uv-cache"
RUN=$(cat $OUT/run_83.pid)
while kill -0 $RUN 2>/dev/null; do /bin/sleep 30; done
[[ -f $OUT/VALIDATED ]] || { echo "run_83 ended without VALIDATED"; exit 1; }
if [[ ! -f $F/8.2/tiny48/rows.json ]]; then
  uv run python $F/run_adapter.py experiments/mvp/sft/stowlm-v2/adapter 8.2 experiments/mvp/valsplit/tiny_val48.txt tiny48 >> $OUT/validation.log 2>&1
fi
uv run python $OUT/validation_report.py || exit 1
sed -i '' '/^| stowlm-83\/sft-and-validation |/d' experiments/fleet/ledger.md
print -r -- "
- stowlm-83/sft-and-validation (fix-t8-sft, 30 Oct): 8.3 SFT + D152 validation, exclusive GPU; estimate 16000 s, finished $(date '+%Y-%m-%d %H:%M %Z')." >> experiments/fleet/history.md
date > $OUT/REPORTED
