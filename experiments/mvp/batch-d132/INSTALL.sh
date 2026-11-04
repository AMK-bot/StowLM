#!/bin/sh
# Run only when the leader opens the D132 generation-boundary window.
set -eu
# argparse accepts unique long-option abbreviations. Block those too, so the
# private Python fault-test switch cannot bypass production post-install tests.
for arg in "$@"; do
    case "$arg" in
        --s|--sk|--ski|--skip*)
            echo "INSTALL.sh refuses the private --skip-fast-tests fault-test option." >&2
            exit 2
            ;;
    esac
done
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../.." && pwd)
export PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
exec uv run --project "$ROOT" --offline --no-sync python "$HERE/assembly/batch_tool.py" install "$@"
