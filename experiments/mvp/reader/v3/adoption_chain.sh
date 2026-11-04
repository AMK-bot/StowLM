#!/bin/sh
# One command, in order, one GPU job at a time: mask study, D263 adoption, adopted x-ray, then intake training.
# Detaches into a new session; needs no decision from anyone. A failed step is logged and the next still runs.
set -u
cd "$(dirname "$0")/../../../.."
export UV_CACHE_DIR="$PWD/.uv-cache" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 RAYON_NUM_THREADS=1
dir=${READER_CHAIN_DIR:-experiments/mvp/reader/v3/results-final}
run=${READER_CHAIN_RUN:-uv run --offline --no-sync python -m}
intake=${READER_CHAIN_INTAKE:-experiments/mvp/pipeline/intake_lora}
if [ "${READER_CHAIN_FOREGROUND:-0}" != 1 ]; then
    chain_pid=$(uv run --offline --no-sync python -c '
import subprocess, sys
from pathlib import Path
directory = Path(sys.argv[1])
with (directory / "adoption-chain.log").open("ab", buffering=0) as stream:
    process = subprocess.Popen(["nohup", "env", "READER_CHAIN_FOREGROUND=1", "sh",
        "experiments/mvp/reader/v3/adoption_chain.sh"],
        stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True)
print(process.pid)
' "$dir")
    printf '%s\n' "$chain_pid" > "$dir/adoption-chain.pid"
    printf 'Detached adoption chain PID %s; log: %s/adoption-chain.log\n' "$chain_pid" "$dir"
    exit 0
fi
step() {
    label=$1
    shift
    printf '%s start %s\n' "$(date)" "$label"
    "$@"
    code=$?
    printf '{"step": "%s", "exit_code": %s, "at": "%s"}\n' "$label" "$code" "$(date)" >> "$dir/chain-status.jsonl"
}
printf '%s adoption chain PID %s\n' "$(date)" "$$"
step mask_study $run experiments.mvp.reader.v3.mask_study
step adopt_decide $run experiments.mvp.reader.v3.adopt decide
step adopt_xray $run experiments.mvp.reader.v3.adopt xray
lease=$PWD/$intake/claude-gpu-lease.json
step intake_lease $run experiments.mvp.reader.v3.intake_lease "$lease"
step intake_launch sh "$intake/launch.sh" --gpu-lease "$lease"
printf '%s adoption chain finished; intake supervisor PID in %s/gpu-run.pid\n' "$(date)" "$intake"
