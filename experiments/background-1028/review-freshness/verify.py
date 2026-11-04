"""One-thread default regression run, with source and resource bookkeeping."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sources():
    return {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / "src/stowage").rglob("*.py"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--estimate-s", type=float, default=720.)
    parser.add_argument("--markers", default=None)
    args = parser.parse_args()
    record = dict(started_utc=datetime.now(timezone.utc).isoformat(),
                  wrapper_pid=os.getpid(), estimate_s=args.estimate_s,
                  resources=dict(cores=1, RAM_GB=3, GPU=False, BLAS_threads=1),
                  source_start=sources())
    target = HERE / f"{args.label}-compute.json"
    started = time.perf_counter()
    command = [sys.executable, "-m", "pytest", "-x", *args.paths,
               f"--junitxml={HERE / (args.label + '.xml')}"]
    if args.markers is not None:
        command.extend(["-m", args.markers])
    record["command"] = command
    with (HERE / f"{args.label}.log").open("w") as stream:
        worker = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
        record["pytest_pid"] = worker.pid
        target.write_text(json.dumps(record, indent=2) + "\n")
        code = worker.wait()
    after = sources()
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    before = record["source_start"]
    record.update(exit_code=code, elapsed_s=time.perf_counter() - started,
                  source_end=after,
                  source_changes=[name for name in sorted(before.keys() | after.keys())
                                  if before.get(name) != after.get(name)],
                  peak_child_RSS_MiB=usage.ru_maxrss / (1024**2 if sys.platform == "darwin" else 1024))
    target.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({key: value for key, value in record.items()
                      if key not in {"source_start", "source_end"}}, sort_keys=True), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
