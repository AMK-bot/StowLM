#!/bin/sh
# One command starts/resumes an admission-controlled, checkpointed detached study.
set -eu
cd "$(dirname "$0")/../../../.."
export UV_CACHE_DIR="$PWD/.uv-cache"
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export MKL_NUM_THREADS=1 STOWAGE_IN_PORT_WORKERS=1
study_dir=${1:-experiments/mvp/pipeline/runner/measurements/profile-study-$(date -u +%Y%m%dT%H%M%SZ)}
mkdir -p "$study_dir"
if [ "${RUNNER_PROFILE_FOREGROUND:-0}" != 1 ]; then
    study_pid=$(uv run --offline --no-sync python -c '
import json, os, subprocess, sys, time
from pathlib import Path
directory = Path(sys.argv[1])
with (directory / "nohup.log").open("ab", buffering=0) as stream:
    process = subprocess.Popen(["nohup", "env", "RUNNER_PROFILE_FOREGROUND=1", "sh",
        "experiments/mvp/pipeline/runner/run_profile_study.sh", str(directory)],
        stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True)
with (directory / "launcher-history.jsonl").open("a") as ledger:
    ledger.write(json.dumps(dict(pid=process.pid, started_utc_s=time.time(),
        detached_session=True, launcher="nohup")) + "\n")
print(process.pid)
' "$study_dir")
    printf '%s\n' "$study_pid" > "$study_dir/launcher.pid"
    printf 'Detached profile study PID %s; checkpoints: %s\n' "$study_pid" "$study_dir"
    exit 0
fi
printf 'Profile study launcher PID %s; warm numerical caches, no stored cargo plans\n' "$$"
uv run --offline --no-sync python -m experiments.mvp.pipeline.runner.cache_seed --out "$study_dir/cache-seeding.json"
uv run --offline --no-sync python -m experiments.mvp.pipeline.runner.profile_study --out "$study_dir" --timeout 7200
uv run --offline --no-sync python -m experiments.mvp.pipeline.runner.profile_finalize --study "$study_dir"
