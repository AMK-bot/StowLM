"""Detach one gold driver and record only the PID this launcher created."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="tiny")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    output = root / "experiments/mvp/gold"
    output.mkdir(parents=True, exist_ok=True)
    record = output / f"{args.name}.process.json"
    if record.exists():
        previous = json.loads(record.read_text())
        try:
            os.kill(previous["pid"], 0)
        except ProcessLookupError:
            pass
        else:
            raise SystemExit(f"Recorded PID {previous['pid']} may still be live; inspect it before relaunching")
    extra = args.args[1:] if args.args[:1] == ["--"] else args.args
    command = [sys.executable, str(Path(__file__).with_name("driver.py")), *extra]
    environment = os.environ.copy()
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        environment[key] = "1"
    with (output / f"{args.name}.log").open("ab", buffering=0) as log:
        process = subprocess.Popen(command, cwd=root, env=environment,
                                   stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    data = dict(pid=process.pid, command=command,
                started_utc=datetime.now(timezone.utc).isoformat())
    record.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data))


if __name__ == "__main__":
    main()
