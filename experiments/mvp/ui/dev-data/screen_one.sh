#!/bin/sh
# usage: screen_one.sh INSTANCE OUTROOT PLANNER TIME [norepair]
n=$(echo "$1" | awk -F/ '{print $5"-"$6"-"$7}' | sed 's/.json//')
export PYTHONPATH=. OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
nice -n 10 uv run python experiments/mvp/ui/dev-data/make_run.py "$1" "$3" "$2/$n" "$4" $5 > "$2/$n.log" 2>&1
