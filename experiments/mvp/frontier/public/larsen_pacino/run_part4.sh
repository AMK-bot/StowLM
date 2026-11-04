#!/bin/bash
# After the first pair of 600 s runs: a second pair.
cd "$(dirname "$0")"
until [ "$(ls results/c600 2>/dev/null | wc -l)" -ge 2 ]; do sleep 5; done
RUN="uv run --no-project --python 3.12 python run_benchmark.py"
C6="--score-feas 1 --init-select 1 --exact 3 --cap 500 --fail-repair 5"
$RUN --tag c600 --time 600 --threads 1 --parallel 2 --instances 7,16 --extra "$C6" > logs/c600b.log 2>&1
