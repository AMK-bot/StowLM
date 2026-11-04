#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/../../../.."
export UV_CACHE_DIR="$PWD/.uv-cache"
export STOWLM_MLX_CACHE_LIMIT_GB=2
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1
# Explicit leader/Claude Metal host only. Wait for both verified training rows
# and the measured new-checker baseline; no pending row can start training.
while ! uv run python -c 'import json,pathlib,sys; p=pathlib.Path("experiments/mvp/exit/deployment-d131/salvage-prepared.json"); sys.exit(0 if p.exists() and json.loads(p.read_text()).get("status")=="ready" else 1)'; do
  sleep 5
done
exec zsh experiments/mvp/exit/recover_on_host.sh --out experiments/mvp/exit/deployment-d131 --recover-prepared experiments/mvp/exit/deployment-d131/salvage-prepared.json
