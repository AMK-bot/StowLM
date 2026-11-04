#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../../../.."
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export UV_CACHE_DIR="$PWD/.uv-cache"
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
set -a
source /Users/anda/Projects/.env
set +a
export SPARK_CREDIT_READY=1
exec uv run --offline --no-sync python -m experiments.mvp.pipeline.intake.score_sealed
