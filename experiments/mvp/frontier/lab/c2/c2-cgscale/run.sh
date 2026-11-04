#!/bin/sh
# Run from the repository root. Only this experiment's directory is written.
set -eu
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
TASK_DIR=experiments/mvp/frontier/lab/c2/c2-cgscale
PYTHON=.venv/bin/python
export STOWAGE_VESSEL_CACHE_DIR="$PWD/$TASK_DIR/results/vessel-cache"

nice -n 5 "$PYTHON" "$TASK_DIR/verify_model.py"
nice -n 5 "$PYTHON" -m pytest "$TASK_DIR/test_method.py" "$TASK_DIR/test_benchmark.py" "$TASK_DIR/test_rmp.py" "$TASK_DIR/test_score_summary.py" -q -o "cache_dir=$TASK_DIR/results/pytest-cache" --basetemp "$TASK_DIR/results/pytest-temp"

# The baseline adapter changes only initialization; old pricing/master/dive remain
# unchanged. Compare LP and dive time, not adapted baseline total wall time.
nice -n 5 "$PYTHON" "$TASK_DIR/benchmark.py" --variant original --static-adapter "$TASK_DIR/pages.py" --lp-s 60 --dive-s 60 --out "$TASK_DIR/results/benchmark-original" &
ORIGINAL_PID=$!
nice -n 5 "$PYTHON" "$TASK_DIR/benchmark.py" --variant rmp --lp-s 60 --dive-s 60 --out "$TASK_DIR/results/benchmark-rmp" &
OPTIMIZED_PID=$!
wait "$ORIGINAL_PID"
wait "$OPTIMIZED_PID"

"$PYTHON" "$TASK_DIR/summarize_benchmark.py" "$TASK_DIR/results/benchmark-original" "$TASK_DIR/results/benchmark-rmp" --out "$TASK_DIR/results/benchmark-rmp-summary.json" --table-out "$TASK_DIR/results/benchmark-rmp-timing.md"
nice -n 5 "$PYTHON" experiments/mvp/frontier/lab/score.py --set medium12 --method "$TASK_DIR/rmp_method.py:propose" --finish "$TASK_DIR/rmp_method.py:finish" --budget-s 60 --finalize-s 120 --tries 1 --workers 1 --out "$TASK_DIR/results/score-medium12-rmp"
nice -n 5 "$PYTHON" "$TASK_DIR/audit_score.py" --folder "$TASK_DIR/results/score-medium12-rmp" --out "$TASK_DIR/results/score-rmp-audit.json"
