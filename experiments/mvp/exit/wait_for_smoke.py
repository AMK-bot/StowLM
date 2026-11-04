"""CPU-idle handoff watcher: run exactly one admitted post-pilot GPU smoke."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'experiments/mvp/exit/post-pilot-smoke'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT/'.watcher.lock').open('a+') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        return watch()


def watch():
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[key] = '1'
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'watcher.pid').write_text(str(os.getpid()) + '\n')
    started = time.monotonic()
    parent = ROOT / 'experiments/mvp/sft/pilot2/adapter'
    marker = ROOT / 'experiments/mvp/runs/sft-pilot2-small/polish_summary.json'
    previous = OUT/'watcher-result.json'
    while time.monotonic()-started < 8*3600:
        if marker.exists() and (parent / 'adapters.safetensors.provenance.json').exists():
            from llm.exit.smoke_once import attempt_path
            from llm.exit.runner import source_pins
            attempt = attempt_path(parent,source_pins())
            if attempt.exists():
                previous.write_text(json.dumps(dict(status='already_attempted',attempt_key=attempt.stem)))
                return 0
            command = [sys.executable, '-m', 'llm.exit.runner', '--parent', str(parent),
                       '--gpu-smoke', '--out', str(OUT)]
            with (OUT / 'smoke.log').open('a') as stream:
                process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
                (OUT / 'smoke.pid').write_text(str(process.pid) + '\n')
                code = process.wait()
            # Only this exact adapter/code claim proves an attempt. A prior
            # compute.json must not suppress a new key after admission refusal.
            if attempt.exists():
                (OUT / 'watcher-result.json').write_text(json.dumps(dict(
                    completed_utc=datetime.now(timezone.utc).isoformat(), returncode=code,
                    attempt_key=attempt.stem,
                    smoke_evidence=str(OUT / 'gpu-smoke.json')), indent=2))
                return code
        time.sleep(30)
    (OUT / 'watcher-result.json').write_text(json.dumps(dict(status='expired_without_gpu_handoff')))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
