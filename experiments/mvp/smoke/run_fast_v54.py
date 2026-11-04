"""Run the unchanged default suite in CPU-bounded, resumable file shards.

No xdist dependency is required. Explicit multiprocessing tests run alone first;
the remaining files run in two independent one-thread pytest processes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "experiments/mvp/smoke"
ENV = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
           MKL_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1", STOWAGE_IN_PORT_WORKERS="1",
           PYTHONPATH=str(OUT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
POOL_FILES = {
    "tests/checker/test_in_port_prepass.py", "tests/checker/test_stability_batch.py",
    "tests/checker/test_in_port_evaluator.py", "tests/checker/test_full_in_port.py",
    "tests/checker/test_in_port_session.py", "tests/solver/test_ballast_deadline_inport.py",
    "tests/linear/test_warm.py", "tests/solver/test_ballast_inport.py",
}


def command(files, name):
    # Collect the complete directory tree before filtering files. Explicit file
    # ordering can import a bounds module before its parametrized conftest loads.
    return [sys.executable, "-m", "pytest", "-p", "v54_fast_plugin",
            "--tb=short", "--durations=20", f"--junitxml={OUT / (name + '.xml')}"]


def start(files, name):
    output = (OUT / (name + ".log")).open("w")
    env = dict(ENV, V54_SELECTION_PATH=str(OUT / (name + "-selection.json")),
               V54_FILE_LIST=json.dumps(files),
               V54_REPORT_PATH=str(OUT / (name + "-reports.jsonl")))
    reports = Path(env["V54_REPORT_PATH"])
    reports.write_text("")
    began = time.monotonic()
    child = subprocess.Popen(command(files, name), cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
    record = {"name": name, "pid": child.pid, "files": files, "command": command(files, name),
              "selection_environment": {key: env[key] for key in
                  ("V54_FILE_LIST", "V54_NODE_SELECTION_PATH", "V54_EXTRA_NODE_LIST", "V54_SELECTION_PATH", "V54_REPORT_PATH") if key in env},
              "native_threads": 1, "started": time.time()}
    (OUT / (name + "-command.json")).write_text(json.dumps(record, indent=2) + "\n")
    return child, output, began, record


def finish(running):
    child, output, began, record = running
    record.update(exit_code=child.wait(), elapsed_s=time.monotonic() - began)
    output.close()
    (OUT / (record["name"] + "-command.json")).write_text(json.dumps(record, indent=2) + "\n")
    return record


def main():
    counts = {}
    for line in (OUT / "v54-fast-collect.log").read_text().splitlines():
        if line.startswith("tests/") and ": " in line:
            path, count = line.rsplit(": ", 1)
            if count.isdecimal():
                counts[path] = int(count)
    assert counts, "Run default pytest --collect-only -q first"
    pool = sorted(set(counts) & POOL_FILES)
    shards, sizes = [[], []], [0, 0]
    for path, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])):
        if path in POOL_FILES:
            continue
        target = min(range(2), key=sizes.__getitem__)
        shards[target].append(path)
        sizes[target] += count
    manifest = {"collection_selected": sum(counts.values()), "pool_files": pool,
                "shard_files": shards, "shard_selected_counts": sizes,
                "approximation": "none: default marker selection unchanged; only execution partitioned"}
    (OUT / "v54-fast-partition.json").write_text(json.dumps(manifest, indent=2) + "\n")
    results = []
    if pool:
        results.append(finish(start(pool, "v54-fast-pools")))
    running = [start(files, f"v54-fast-shard{index + 1}") for index, files in enumerate(shards)]
    results.extend(finish(item) for item in running)
    (OUT / "v54-fast-commands.json").write_text(json.dumps(results, indent=2) + "\n")
    return int(any(record["exit_code"] for record in results))


if __name__ == "__main__":
    raise SystemExit(main())
