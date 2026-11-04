#!/bin/bash
# LCG-shift diagnostic on all of vessel L, then 600 s runs.
cd "$(dirname "$0")"
RUN="uv run --no-project --python 3.12 python run_benchmark.py"
C6="--score-feas 1 --init-select 1 --exact 3 --cap 500 --fail-repair 5"
$RUN --tag l_shift05 --time 60 --threads 1 --parallel 2 --instances 19-27 --bin alns_diag --lcg-shift 0.5 --extra "$C6 --lcg-shift 0.5" > logs/l_shift05.log 2>&1
$RUN --tag c600 --time 600 --threads 1 --parallel 2 --instances 1,13 --extra "$C6" > logs/c600.log 2>&1
