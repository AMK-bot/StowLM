#!/bin/sh
# Claude GPU executive only: detached supervisor waits for CPU and explicit GPU admission.
set -eu
if [ "$#" -ne 2 ] || [ "$1" != "--gpu-lease" ]; then
  echo 'Usage: experiments/mvp/pipeline/intake_lora/launch.sh --gpu-lease /absolute/path/to/claude-lease.json' >&2
  exit 2
fi
case "$2" in
  /*) lease="$2" ;;
  *) lease="$PWD/$2" ;;
esac
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)
cd "$repo"
export UV_CACHE_DIR="$repo/.uv-cache"
export PYTHONPATH="src:experiments/mvp/frontier:experiments/mvp/frontier/lab${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1 TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 RAYON_NUM_THREADS=1
out=experiments/mvp/pipeline/intake_lora
# noclobber is a local launch claim; existing logs and recorded PIDs are never overwritten.
set -C
{ : > "$out/launch.claim"; } 2>/dev/null || { echo 'Existing intake launch claim; preserve prior run.' >&2; exit 2; }
set +C
nohup uv run --offline --no-sync python -m experiments.mvp.pipeline.intake_lora.gpu_run \
  --train-only --gpu-lease "$lease" > "$out/gpu-run.log" 2>&1 < /dev/null &
owned_pid=$!
printf '%s\n' "$owned_pid" > "$out/gpu-run.pid"
printf 'Detached intake supervisor PID %s; log %s/gpu-run.log\n' "$owned_pid" "$out"
