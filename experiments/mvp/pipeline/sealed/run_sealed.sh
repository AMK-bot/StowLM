#!/bin/sh
# Leader runs outside the sandbox, after2.1. A missing manifest spends no claim.
set -eu
cd "$(dirname "$0")/../../../.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export MKL_NUM_THREADS=1 STOWAGE_IN_PORT_WORKERS=1
launch_module=experiments.mvp.pipeline.sealed.launch
if [ "${1:-}" = '--preflight' ]; then
    exec uv run --offline --no-sync python -m "$launch_module" --preflight
fi
manifest=${1:-experiments/mvp/pipeline/freeze/manifest-pipeline.json}
test -f "$manifest" || { printf 'Task2.1 manifest absent; no sealed claim consumed\n' >&2; exit 2; }
printf 'Running this release command attests: other fleet work stopped; GPU free for the live reader.\n'
# Preserve exit2 at the shell boundary rather than returning a detached PID
# for a known drift. The detached controller verifies again before release.
uv run --offline --no-sync python -m "$launch_module" --manifest "$manifest" --check-only
launch_dir=experiments/mvp/pipeline/sealed/measurements/release-launcher
mkdir -p "$launch_dir"
uv run --offline --no-sync python -c '
import json,subprocess,sys,time
from pathlib import Path
directory=Path(sys.argv[1])
with (directory/"nohup.log").open("ab",buffering=0) as stream:
    child=subprocess.Popen([sys.executable,"-m",sys.argv[3],
        "--manifest",sys.argv[2],"--attest-idle-fleet"],stdin=subprocess.DEVNULL,stdout=stream,
        stderr=subprocess.STDOUT,start_new_session=True,close_fds=True)
with (directory/"launcher-history.jsonl").open("a") as ledger:
    ledger.write(json.dumps(dict(pid=child.pid,started=time.time(),detached_session=True))+"\n")
print("Sealed launcher PID",child.pid,"(manifest/source/calibration/admission gates still apply)")
' "$launch_dir" "$manifest" "$launch_module"
