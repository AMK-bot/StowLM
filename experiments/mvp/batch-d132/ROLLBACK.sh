#!/bin/sh
# Refuses unknown edits; restores only the owned transaction's exact backup.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../.." && pwd)
export PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
exec uv run --project "$ROOT" --offline --no-sync python "$HERE/assembly/batch_tool.py" rollback "$@"
