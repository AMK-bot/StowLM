#!/bin/sh
# One command, one GPU job at a time. GPU order: readings, x-ray retrace, hero candidates, offline timing,
# intake untrained baseline, intake sealed score, base Glimmer. The 12 plans run on CPU alongside.
# Then it watches for p110's hero wordings. Detached, no decision needed; a rerun resumes.
set -u
cd "$(dirname "$0")/../../../.."
export UV_CACHE_DIR="$PWD/.uv-cache" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 RAYON_NUM_THREADS=1 STOWAGE_IN_PORT_WORKERS=1
dir=${READER_FINAL_DIR:-experiments/mvp/pipeline/reader_final}
run=${READER_FINAL_RUN:-uv run --offline --no-sync python -m}
wait_pid=${READER_FINAL_WAIT_PID:-$(cat experiments/mvp/pipeline/intake_lora/gpu-run.pid)}
poll_s=${READER_FINAL_POLL_S:-120}
intake=$PWD/experiments/mvp/pipeline/intake_lora
if [ "${READER_FINAL_FOREGROUND:-0}" != 1 ]; then
    chain_pid=$(uv run --offline --no-sync python -c '
import subprocess, sys
from pathlib import Path
directory = Path(sys.argv[1])
with (directory / "chain.log").open("ab", buffering=0) as stream:
    process = subprocess.Popen(["nohup", "env", "READER_FINAL_FOREGROUND=1", "sh",
        "experiments/mvp/pipeline/reader_final/chain.sh"],
        stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True)
print(process.pid)
' "$dir")
    printf '%s\n' "$chain_pid" > "$dir/chain.pid"
    printf 'Detached reader_final chain PID %s; log: %s/chain.log\n' "$chain_pid" "$dir"
    exit 0
fi
note() {
    printf '{"step": "%s", "exit_code": %s, "at": "%s"}\n' "$1" "$2" "$(date)" >> "$dir/chain-status.jsonl"
}
step() {
    printf '%s start %s\n' "$(date)" "$1"
    $run "experiments.mvp.pipeline.reader_final.$1"
    code=$?
    note "$1" "$code"
    return $code
}
intake_scoring() {
    step intake_gate || return 1
    lease="$intake/claude-gpu-lease.json"
    printf '%s start intake untrained validation baseline\n' "$(date)"
    uv run --offline --no-sync python -m experiments.mvp.pipeline.intake_lora.evaluate --backend glimmer \
        --gpu-lease "$lease" --output "$intake/results/valid-untrained.json"
    note intake_untrained $?
    printf '%s start intake sealed score, once\n' "$(date)"
    (
        set -a
        . /Users/anda/Projects/.env
        set +a
        export SPARK_CREDIT_READY=1
        uv run --offline --no-sync python -m experiments.mvp.pipeline.intake_lora.score_final --execute-once --gpu-lease "$lease"
    )
    note intake_sealed $?
}
printf '%s chain PID %s waiting for intake PID %s\n' "$(date)" "$$" "$wait_pid"
while ps -p "$wait_pid" -o command= | grep -q intake_lora; do /bin/sleep 30; done
step read_all || exit 1
step report
step plan_cases &
planning=$!
step retrace
step hero_candidates
step offline_timing
[ "${READER_FINAL_SKIP_INTAKE:-0}" = 1 ] || intake_scoring
step base_arm
wait "$planning"
step report
tries=0
until step hero_candidates && [ "$poll_s" != 0 ] || [ "$poll_s" = 0 ] || [ "$tries" -ge 360 ]; do
    tries=$((tries + 1))
    /bin/sleep "$poll_s"
done
printf '%s reader_final chain finished\n' "$(date)"
