#!/bin/sh
# One command: once finish_chain.sh has exited, run the mask study. Detached; a rerun resumes from the row store.
# A separate script because a running sh must not have its script edited under it.
set -u
cd "$(dirname "$0")/../../../.."
export UV_CACHE_DIR="$PWD/.uv-cache" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 RAYON_NUM_THREADS=1
dir=${READER_CHAIN_DIR:-experiments/mvp/reader/v3/results-final}
run=${READER_CHAIN_RUN:-uv run --offline --no-sync python -m}
if [ "${READER_CHAIN_FOREGROUND:-0}" != 1 ]; then
    mask_pid=$(uv run --offline --no-sync python -c '
import subprocess, sys
from pathlib import Path
directory = Path(sys.argv[1])
with (directory / "mask-chain.log").open("ab", buffering=0) as stream:
    process = subprocess.Popen(["nohup", "env", "READER_CHAIN_FOREGROUND=1", "sh",
        "experiments/mvp/reader/v3/mask_chain.sh"],
        stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True)
print(process.pid)
' "$dir")
    printf '%s\n' "$mask_pid" > "$dir/mask-chain.pid"
    printf 'Detached mask study PID %s; log: %s/mask-chain.log\n' "$mask_pid" "$dir"
    exit 0
fi
chain_pid=$(cat "$dir/chain.pid")
printf '%s mask chain PID %s waiting for chain PID %s\n' "$(date)" "$$" "$chain_pid"
while ps -p "$chain_pid" -o command= | grep -q finish_chain; do /bin/sleep 30; done
$run experiments.mvp.reader.v3.mask_study
code=$?
printf '{"step": "mask_study", "exit_code": %s, "at": "%s"}\n' "$code" "$(date)" >> "$dir/chain-status.jsonl"
printf '%s mask chain finished: exit %s\n' "$(date)" "$code"
