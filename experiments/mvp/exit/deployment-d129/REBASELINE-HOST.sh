#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/../../../.."
export UV_CACHE_DIR="$PWD/.uv-cache"
export STOWLM_MLX_CACHE_LIMIT_GB=2
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1
# Explicit leader/Claude Metal host invocation only. Codex must not run this.
while [[ ! -f experiments/mvp/exit/deployment-d129/D131-CODE.READY ]]; do
  sleep 2
done
uv run python experiments/mvp/exit/rebaseline_on_host.py "$@"
