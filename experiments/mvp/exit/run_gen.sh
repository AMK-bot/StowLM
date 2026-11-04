#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/../../.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export STOWAGE_IN_PORT_WORKERS=1
exec uv run python -m llm.exit.runner "$@"
