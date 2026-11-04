#!/bin/zsh
cd /Users/anda/Projects/meta-hackathon-stowage
B="--score-feas 1 --init-select 1 --verbose 1"
uv run --no-project --python 3.12 python experiments/mvp/frontier/public/larsen_pacino/run_benchmark.py --tag calG --time 60 --threads 1 --parallel 2 --instances 22,9,16,20 --extra "$B --exact 3 --cap 500 --fail-repair 5" > experiments/mvp/frontier/public/larsen_pacino/logs/calG.log 2>&1
uv run --no-project --python 3.12 python experiments/mvp/frontier/public/larsen_pacino/run_benchmark.py --tag calH --time 60 --threads 1 --parallel 2 --instances 22,9,16,20 --extra "$B --exact 3 --cap 500 --fail-repair 2" > experiments/mvp/frontier/public/larsen_pacino/logs/calH.log 2>&1
