#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/../../.."
export UV_CACHE_DIR="$PWD/.uv-cache"
export STOWLM_MLX_CACHE_LIMIT_GB=2
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1
# Leader/Claude host session only; Codex must not launch this Metal workload.
uv run python experiments/mvp/exit/recover_on_host.py "$@"
