#!/bin/sh
# Detached, checkpointed native study. Four workers; only development maps.
set -eu
cd "$(dirname "$0")/../../../.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export MKL_NUM_THREADS=1 STOWAGE_IN_PORT_WORKERS=1
study_dir=${1:-experiments/mvp/pipeline/sealed/measurements/native-development-$(date -u +%Y%m%dT%H%M%SZ)}
mkdir -p "$study_dir"
if [ "${SEALED_STUDY_FOREGROUND:-0}" != 1 ]; then
    uv run --offline --no-sync python -c '
import json, subprocess, sys, time
from pathlib import Path
directory=Path(sys.argv[1])
with (directory/"nohup.log").open("ab",buffering=0) as stream:
    child=subprocess.Popen(["nohup","env","SEALED_STUDY_FOREGROUND=1","sh",
        "experiments/mvp/pipeline/sealed/run_real_dry.sh",str(directory)],
        stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,
        start_new_session=True,close_fds=True)
with (directory/"launcher-history.jsonl").open("a") as ledger:
    ledger.write(json.dumps(dict(pid=child.pid,started=time.time(),detached_session=True))+"\n")
(directory/"launcher.pid").write_text(str(child.pid)+"\n")
print("Detached native study PID",child.pid,"checkpoints:",directory)
' "$study_dir"
    exit 0
fi
exec uv run --offline --no-sync python -m experiments.mvp.pipeline.sealed.real_dry_run --out-dir "$study_dir"
