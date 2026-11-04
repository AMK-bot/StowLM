#!/bin/sh
# One command: after training ends, build candidates, select one, score sealed v3 once, record x-ray traces.
# Detaches into a new session and needs no decision from anyone. Rerunning resumes from what exists.
set -u
cd "$(dirname "$0")/../../../.."
export UV_CACHE_DIR="$PWD/.uv-cache" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 RAYON_NUM_THREADS=1
dir=${READER_CHAIN_DIR:-experiments/mvp/reader/v3/results-final}
run=${READER_CHAIN_RUN:-uv run --offline --no-sync python -m}
if [ "${READER_CHAIN_FOREGROUND:-0}" != 1 ]; then
    chain_pid=$(uv run --offline --no-sync python -c '
import subprocess, sys
from pathlib import Path
directory = Path(sys.argv[1])
with (directory / "chain.log").open("ab", buffering=0) as stream:
    process = subprocess.Popen(["nohup", "env", "READER_CHAIN_FOREGROUND=1", "sh",
        "experiments/mvp/reader/v3/finish_chain.sh"],
        stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True)
print(process.pid)
' "$dir")
    printf '%s\n' "$chain_pid" > "$dir/chain.pid"
    printf 'Detached reader chain PID %s; log: %s/chain.log\n' "$chain_pid" "$dir"
    exit 0
fi
step() {
    printf '%s start %s\n' "$(date)" "$1"
    $run "experiments.mvp.reader.v3.$1"
    code=$?
    printf '{"step": "%s", "exit_code": %s, "at": "%s"}\n' "$1" "$code" "$(date)" >> "$dir/chain-status.jsonl"
    return $code
}
train_pid=$(cat "$dir/run.pid")
printf '%s chain PID %s waiting for training PID %s\n' "$(date)" "$$" "$train_pid"
while ps -p "$train_pid" -o command= | grep -q finish_run; do /bin/sleep 30; done
printf '%s training has ended\n' "$(date)"
step swa || exit 1
step select_adapter || exit 1
step score_selected  # a failure here must not cost the traces
sealed=$?
step record_traces
traces=$?
printf '%s chain finished: sealed exit %s, traces exit %s\n' "$(date)" "$sealed" "$traces"
