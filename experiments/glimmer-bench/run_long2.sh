#!/bin/zsh
# Trimmed remainder of run_long.sh (the full grid would have taken ~3 h more): 8k prompts to 5k outputs only,
# mask overhead at b16, 3-bit at b16, then the bare-model step benchmark. Resumable like run_long.sh.
set -u
M4=models/glimmer-30b-4bit; M3=models/glimmer-30b-3bit; O=experiments/glimmer-bench/long.jsonl
B() { uv run python experiments/glimmer-bench/bench_long.py "$@"; }
while kill -0 ${WAIT_PID:-0} 2>/dev/null; do sleep 5; done
for N in 1 16; do B $M4 $O 8192 $N 5000 none; done
for MODE in none lazy sync; do B $M4 $O 2048 16 2000 $MODE; done
B $M3 $O 2048 16 2000 none
[[ -s experiments/glimmer-bench/step.json ]] || uv run python experiments/glimmer-bench/bench_step.py $M4 experiments/glimmer-bench/step.json
echo SWEEP DONE
