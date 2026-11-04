#!/bin/zsh
# Extra checks after the sweep: (1) cold-GPU recalibration after 3 min idle, to size the sustained-load slowdown;
# (2) batch 32 / 64 through BatchGenerator and the bare model. Then phase 2 (QLoRA + adapter conversion).
set -u
M4=models/glimmer-30b-4bit; O=experiments/glimmer-bench/long.jsonl
B() { uv run python experiments/glimmer-bench/bench_long.py "$@"; }
sleep 180
B $M4 experiments/glimmer-bench/calib_cold.jsonl 2048 16 300 none
for N in 32 64; do B $M4 $O 2048 $N 1000 none; done
[[ -s experiments/glimmer-bench/step64.json ]] || uv run python experiments/glimmer-bench/bench_step.py $M4 experiments/glimmer-bench/step64.json 64
echo EXTRA DONE
./experiments/glimmer-bench/run_phase2.sh
