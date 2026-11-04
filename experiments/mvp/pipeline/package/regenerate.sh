#!/bin/sh
set -eu
unset CDPATH
stowlm_repo_root=$(cd "$(dirname "$0")/../../../.." && pwd)
cd "$stowlm_repo_root"
export UV_CACHE_DIR="$stowlm_repo_root/.uv-cache"
export PYTHONPATH="$stowlm_repo_root/src:$stowlm_repo_root/experiments/mvp/frontier:$stowlm_repo_root/experiments/mvp/frontier/lab"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
exec uv run --offline --no-sync python -m experiments.mvp.pipeline.package.regenerate "$@"
