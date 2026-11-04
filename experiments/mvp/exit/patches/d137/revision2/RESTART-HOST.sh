#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/../../../../../.."
export UV_CACHE_DIR="$PWD/.uv-cache"
export STOWLM_MLX_CACHE_LIMIT_GB=2
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1
unset STOWAGE_BALLAST_V4
# Explicit Claude/Metal host invocation only. This script never installs patches.
# D142 F6: audit 8.1 and 8.2 (sequentially, about 25 GPU-min), record the choice, then queue the loop from that parent.
R=experiments/mvp/exit/patches/d137/revision2/restart
if [[ "${1:-}" == "--check-only" ]]; then
  uv run python $R/parent_audit.py --check-only || echo "not ready: see the missing list above"
  [[ -f experiments/mvp/exit/deployment-d137/parent-choice.json ]] && exec uv run python $R/restart_on_host.py "$@"
  echo "parent-choice.json not written yet: run without --check-only after install"; exit 0
fi
uv run python $R/parent_audit.py
exec uv run python $R/restart_on_host.py "$@"
