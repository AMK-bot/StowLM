#!/bin/bash
# Final sweeps. Resumable: rerun this file and finished instances are skipped.
# Start with: nohup nice -n 10 ./run_all.sh > logs/run_all.log 2>&1 &
cd "$(dirname "$0")"
RUN="uv run --no-project --python 3.12 python run_benchmark.py"
CAL="--score-feas 1 --init-select 1 --exact 3 --cap 500 --fail-repair 5"
LIT="--cap 4000 --fail-repair 5"
$RUN --tag c60 --time 60 --threads 1 --parallel 2 --extra "$CAL" > logs/c60.log 2>&1
$RUN --tag l60 --time 60 --threads 1 --parallel 2 --extra "$LIT" > logs/l60.log 2>&1
$RUN --tag c600 --time 600 --threads 1 --parallel 2 --instances 1,13,25,7,19,16,4,10,22 --extra "$CAL" > logs/c600.log 2>&1
