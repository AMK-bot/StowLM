"""Record a one-thread default regression run without changing the source tree."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--estimate-s", type=float, default=600.)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("witness_measure_sources", HERE / "measure.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    source_start = module.sources()[0]
    started = time.perf_counter()
    command = [sys.executable, "-m", "pytest", *args.paths,
               f"--junitxml={HERE / (args.label+'.xml')}"]
    log = HERE / (args.label+".log")
    record = dict(wrapper_pid=os.getpid(), command=command,
                  started_utc=datetime.now(timezone.utc).isoformat(),
                  estimate_s=args.estimate_s, source_start=source_start,
                  resources={"cores":1, "RAM_GB":3, "GPU":False},
                  environment={key:os.environ.get(key) for key in (
                      "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")})
    output = HERE / (args.label+"-compute.json")
    with log.open("w") as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
        record["pytest_pid"] = process.pid
        output.write_text(json.dumps(record, indent=2)+"\n")
        code = process.wait()
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    record.update(exit_code=code, elapsed_s=time.perf_counter()-started,
                  source_end=module.sources()[0],
                  peak_child_RSS_MiB=usage.ru_maxrss/(1024**2 if sys.platform=="darwin" else 1024))
    output.write_text(json.dumps(record, indent=2)+"\n")
    print(json.dumps(record, sort_keys=True), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
