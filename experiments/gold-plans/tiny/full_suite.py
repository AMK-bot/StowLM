"""Run the authorized full suite with one native thread and a 20-minute cap."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import subprocess
import time


def main():
    folder = Path(__file__).resolve().parent
    root = folder.parents[2]
    env = dict(os.environ)
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                 "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[name] = "1"
    env["UV_CACHE_DIR"] = str(root / ".uv-cache")
    command = ["uv", "run", "pytest", "-q"]
    started = time.monotonic()
    record = dict(command=command, cwd=str(root), wrapper_pid=os.getpid(),
                  started_utc=datetime.now(timezone.utc).isoformat(),
                  timeout_s=1200, native_threads=1, estimated_wall_s=900,
                  log=str(folder / "full-suite.log"))
    with (folder / "full-suite.log").open("w") as log:
        process = subprocess.Popen(command, cwd=root, env=env, stdout=log,
                                   stderr=subprocess.STDOUT)
        record["owned_child_pid"] = process.pid
        (folder / "full-suite-running.json").write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps(record), flush=True)
        try:
            code = process.wait(timeout=1200)
            record["timed_out"] = False
        except subprocess.TimeoutExpired:
            record["timed_out"] = True
            # Only this recorded Popen child is signalled; no process discovery.
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            code = 124
    record.update(exit_code=code, wall_time_s=time.monotonic() - started,
                  finished_utc=datetime.now(timezone.utc).isoformat(),
                  child_peak_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    temporary = folder / "full-suite.result.json.partial"
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(folder / "full-suite.result.json")
    (folder / "full-suite-running.json").unlink(missing_ok=True)
    print(json.dumps(record), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
