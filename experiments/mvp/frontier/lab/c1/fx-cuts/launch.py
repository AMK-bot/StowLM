"""Launch the scorecard and record the exact owned PID and helper hashes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]


def main():
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                'MKL_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
        os.environ[key] = '1'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    command = [str(ROOT / '.venv/bin/python'),
               str(HERE.parents[1] / 'score.py'), *sys.argv[1:]]
    child = subprocess.Popen(command, cwd=ROOT)
    row = dict(pid=child.pid, launcher_pid=os.getpid(), command=command,
               started_utc=datetime.now(timezone.utc).isoformat(),
               helper_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(HERE.glob('*.py'))})
    with (HERE / 'owned_processes.jsonl').open('a') as stream:
        stream.write(json.dumps(row, sort_keys=True) + '\n')
    return child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
