#!/bin/bash
# Second half of the sweeps: waits for the literal sweep, then the LCG diagnostic, then 600 s runs.
cd "$(dirname "$0")"
until [ "$(ls results/l60 2>/dev/null | wc -l)" -ge 27 ]; do sleep 5; done
R=data/raw/larsen-pacino
CAL="--time 60 --threads 1 --clock cpu --seed 1 --score-feas 1 --init-select 1 --exact 3 --cap 500 --fail-repair 5"
mkdir -p results/diag
run() { # inst file shift
  bin/alns_diag --vessel $R/vessel_data/vessel_L.txt --instance $R/container_instances/Vessel_L/$2.txt --plan results/diag/inst$1_shift$3.plan $CAL --lcg-shift $3 > results/diag/inst$1_shift$3.json 2>&1
}
run 20 VLLow2 0.5 & run 20 VLLow2 1.0 & wait
run 25 VLHigh1 1.0 & run 21 VLLow3 1.0 & wait
RUN="uv run --no-project --python 3.12 python run_benchmark.py"
C6="--score-feas 1 --init-select 1 --exact 3 --cap 500 --fail-repair 5"
$RUN --tag c600 --time 600 --threads 1 --parallel 2 --instances 1,13,25,7,19,16 --extra "$C6" > logs/c600.log 2>&1
