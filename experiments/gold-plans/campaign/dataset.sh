#!/usr/bin/env bash
# Offline CPU-only 7.1 publication; a matching completed build resumes by hash.
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_root/.uv-cache}"
exec uv run --offline --python 3.12 python experiments/sft-data/build.py --resume --require-epoch "$@"
