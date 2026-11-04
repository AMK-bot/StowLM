#!/bin/sh
# Detach the Python supervisor itself; an existing output directory resumes.
set -eu
cd "$(dirname "$0")/../../../.."
export UV_CACHE_DIR="$PWD/.uv-cache"
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export MKL_NUM_THREADS=1 STOWAGE_IN_PORT_WORKERS=1
if [ "$#" -gt 1 ]; then
    printf 'Usage: %s [binding-output-directory]\n' "$0" >&2
    exit 2
fi
uv run --offline --no-sync python - "${1:-}" <<'PY'
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

repository = Path.cwd()
binding = repository / 'experiments/mvp/pipeline/binding'
directory = (Path(sys.argv[1]) if sys.argv[1] else
             binding / 'measurements' / ('live-fixed-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))).resolve()
if not directory.is_relative_to(binding.resolve()):
    raise SystemExit('Campaign output must be under experiments/mvp/pipeline/binding/')
if not (binding / 'live_campaign.py').is_file():
    raise SystemExit('The frozen binding.live_campaign entry point is not present')
directory.mkdir(parents=True, exist_ok=True)
pidfile = directory / 'launcher.pid'
if pidfile.is_file():
    try:
        previous_pid = int(pidfile.read_text().strip())
        if previous_pid > 0:
            os.kill(previous_pid, 0)  # Existence probe for this recorded PID only.
            raise SystemExit(f'Recorded supervisor PID {previous_pid} is still present; refusing a duplicate launch')
    except ProcessLookupError:
        pass
    except (ValueError, OSError) as error:
        raise SystemExit(f'Cannot establish the prior supervisor state: {type(error).__name__}')
command = ['nohup', sys.executable, '-m', 'experiments.mvp.pipeline.binding.live_campaign',
           '--out', str(directory), '--workers', '3']
with (directory / 'nohup.log').open('ab', buffering=0) as stream:
    process = subprocess.Popen(command, cwd=repository, stdin=subprocess.DEVNULL,
        stdout=stream, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
event = dict(pid=process.pid, launcher_helper_pid=os.getpid(), started_utc_s=time.time(),
    started_utc=datetime.now(timezone.utc).isoformat(), detached_session=True,
    launcher='nohup-direct-python', workers=3, command=command, output=str(directory))
with (directory / 'launcher-history.jsonl').open('a') as ledger:
    ledger.write(json.dumps(event, sort_keys=True)+'\n')
    ledger.flush()
    os.fsync(ledger.fileno())
temporary = directory / f'.launcher.pid.{os.getpid()}.tmp'
temporary.write_text(str(process.pid)+'\n')
os.replace(temporary, pidfile)
print(f'Detached Python supervisor PID {process.pid}; checkpoints: {directory}', flush=True)
PY
