#!/bin/sh
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 MKL_NUM_THREADS=1
export PYTHONDONTWRITEBYTECODE=1 UV_NO_SYNC=1 UV_OFFLINE=1
export UV_CACHE_DIR="$PWD/experiments/cache-warm/w2-physwarm/uv-cache"
export PYTEST_ADDOPTS='-p no:cacheprovider'
uv run pytest tests/checker -q -k 'liquid or stability_tables or cache_warm'
test_status=$?
printf '%s\n' "$test_status" > experiments/cache-warm/w2-physwarm/pytest-detached.status
exit "$test_status"
