"""Resume unreported canonical cases in one CPU-bounded pytest process.

Use --final-delta once after the existing snapshot completes. Full-tree
collection preserves nested conftest fixtures before exact node-ID filtering.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

from run_fast_v54 import ENV, OUT, ROOT, start, finish


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="v54-fast-resume2")
    parser.add_argument("--final-delta", action="store_true")
    parser.add_argument("--rerun-failures", action="store_true")
    args = parser.parse_args()
    if args.final_delta:
        with (OUT / "v54-fast-final-collect.log").open("w") as output:
            subprocess.run([__import__("sys").executable, "-m", "pytest", "--collect-only", "-q",
                            "-p", "v54_fast_plugin"], cwd=ROOT,
                           env=dict(ENV, V54_SELECTION_PATH=str(OUT / "v54-fast-default-selection.json")),
                           stdout=output, stderr=subprocess.STDOUT, check=True)
    expected = set(json.loads((OUT / "v54-fast-default-selection.json").read_text()))
    subprocess.run([__import__("sys").executable, str(OUT / "summarize_fast_v54.py")],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    summary = json.loads((OUT / "fast-suite-summary.json").read_text())
    targets = set(summary["missing"])
    # Original explicit-file collection missed this nested geometry fixture.
    # Re-run its affected cases through the corrected full-tree collection.
    for row in summary["failures"]:
        if args.rerun_failures or "tests/generate/test_random_yard.py::test_rungs_roundtrip" in row["nodeid"]:
            targets.add(row["nodeid"])
    assert targets <= expected
    path = OUT / (args.name + "-targets.json")
    path.write_text(json.dumps(sorted(targets), indent=2) + "\n")
    if not targets:
        print("No unreported canonical cases.")
        return 0
    ENV["V54_NODE_SELECTION_PATH"] = str(path)
    pause = OUT / "v54-fast-pause.json"
    if pause.exists():
        record = json.loads(pause.read_text())
        record.update(workers_running=1, resumed_epoch=time.time())
        pause.write_text(json.dumps(record, indent=2) + "\n")
    process = start([], args.name)
    result = finish(process)
    subprocess.run([__import__("sys").executable, str(OUT / "summarize_fast_v54.py")], cwd=ROOT, check=True)
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
