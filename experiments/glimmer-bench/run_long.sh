#!/bin/zsh
# Resumable sweep for bench_long.py (each config is skipped if already in long.jsonl).
set -u
M4=models/glimmer-30b-4bit; M3=models/glimmer-30b-3bit; O=experiments/glimmer-bench/long.jsonl
B() { uv run python experiments/glimmer-bench/bench_long.py "$@"; }
for P in 2048 8192; do for N in 1 8 16; do B $M4 $O $P $N 10000 none; done; done
for MODE in lazy sync; do B $M4 $O 2048 16 2000 $MODE; done
B $M4 $O 2048 16 2000 none
for N in 1 16; do B $M3 $O 2048 $N 5000 none; done
echo SWEEP DONE
