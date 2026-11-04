#!/bin/zsh
# Phase 2 GPU jobs (after run_long.sh): QLoRA throughput at seq 8k/12k, then the toy adapter + MLX logits for the
# adapter-conversion check (experiments/grpo-venue/). Each step skips if its output exists.
set -u
: ${SCRATCH:?set SCRATCH to a temp dir holding long-data/ (make_long_data.py)}
M4=models/glimmer-30b-4bit; D=$SCRATCH/long-data; L=experiments/glimmer-bench/qlora_logs; mkdir -p $L
q() { # name seq layers extra...
  [[ -s $L/$1.log ]] && grep -q "PEAK_GB" $L/$1.log && return
  uv run python experiments/glimmer-bench/qlora_peak.py --model $M4 --train --data $D --batch-size 1 --iters 4 --steps-per-report 1 \
    --steps-per-eval 1000 --val-batches 1 --max-seq-length $2 --num-layers $3 --learning-rate 1e-5 \
    --adapter-path $SCRATCH/qlora-bench-$1 "${@:4}" > $L/$1.log 2>&1
}
# q seq8k_l16 8192 16  # OOMs without --grad-checkpoint (Metal out of memory at 128 GB); kept to document that
q seq8k_l16_gc 8192 16 --grad-checkpoint
q seq8k_l52_gc 8192 52 --grad-checkpoint
q seq12k_l16_gc 12288 16 --grad-checkpoint
q seq12k_l52_gc 12288 52 --grad-checkpoint
# toy adapter (rank 8, scale 20, last 16 layers, all 8 linears incl. the attention output gate), strong lr so the
# adapter's effect on logits is large enough to compare
[[ -f models/toy-adapter-mlx/adapters.safetensors ]] || uv run python -m mlx_lm lora --model $M4 --train \
  --data data/glimmer-bench --batch-size 1 --iters 40 --steps-per-report 10 --steps-per-eval 1000 --val-batches 1 \
  --max-seq-length 1024 --learning-rate 2e-4 --adapter-path models/toy-adapter-mlx > $L/toy_adapter.log 2>&1
uv run python experiments/grpo-venue/convert_adapter.py mlx2peft models/toy-adapter-mlx models/toy-adapter-peft
uv run python experiments/grpo-venue/convert_adapter.py peft2mlx models/toy-adapter-peft models/toy-adapter-mlx-roundtrip
C=$SCRATCH/conv; mkdir -p $C
uv run python experiments/grpo-venue/mlx_logits.py $M4 none $C mlx_q4_base
uv run python experiments/grpo-venue/mlx_logits.py $M4 models/toy-adapter-mlx $C mlx_q4_adapter
uv run python experiments/grpo-venue/mlx_logits.py $M4 models/toy-adapter-mlx-roundtrip $C mlx_q4_roundtrip
echo PHASE2 DONE
