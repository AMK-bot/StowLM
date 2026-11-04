"""Detached, resumable driver for D185 step 2: at most 3 map workers (6 CPU with V4 children), one Glimmer process.

    uv run --no-sync python experiments/mvp/strategy/proposer/collect/drive.py

Every step resumes from its own files. Spark and Glimmer collection run beside the arms; ``glimmer`` collection waits
for its section of the plan. The report runs once all four arms have a completion.json.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PLAN, PROPOSER, RESPONSES, ROOT

RUNS = PROPOSER / "runs"
SLOTS = 3
BASE = [".venv/bin/python", "experiments/mvp/strategy/proposer/run.py", "--k", "4"]
ENV = {**os.environ, "STOWLM_MLX_CACHE_LIMIT_GB": "2", "PYTHONUNBUFFERED": "1"}


def arm_command(arm: str) -> list[str]:
    command = [*BASE, "--arm", arm, "--output", str((RUNS / f"d185-{arm}").relative_to(ROOT))]
    if arm in ("spark", "glimmer"):
        command += ["--provider", "jsonl", "--proposals", str((RESPONSES / f"d185-{arm}.jsonl").relative_to(ROOT))]
    return command


def start(name: str, command: list[str]) -> subprocess.Popen:
    log = open(RUNS / f"{name}.log", "a")
    process = subprocess.Popen(command, cwd=ROOT, env=ENV, stdout=log, stderr=subprocess.STDOUT)
    with (RUNS / "d185-pids.txt").open("a") as stream:
        stream.write(f"{time.strftime('%FT%T%z')} {name} {process.pid}\n")
    return process


def main() -> int:
    RUNS.mkdir(exist_ok=True)
    collectors: dict[str, subprocess.Popen] = {}
    arms: dict[str, subprocess.Popen] = {}
    waiting = ["rule", "search", "spark", "glimmer"]
    while waiting or arms or collectors:
        plan = json.loads(PLAN.read_text())
        for arm in ("spark", "glimmer"):
            final = RESPONSES / f"d185-{arm}.jsonl"
            if arm in plan and not final.exists() and arm not in collectors:
                collectors[arm] = start(f"collect-{arm}", [".venv/bin/python",
                                        "experiments/mvp/strategy/proposer/collect/collect.py", arm])
        for name, process in list(collectors.items()):
            if process.poll() is not None:
                del collectors[name]
                if process.returncode:
                    print(f"collector {name} exited {process.returncode}", flush=True)
                    return 1
        for name, process in list(arms.items()):
            if process.poll() is not None:
                print(f"arm {name} exited {process.returncode}", flush=True)
                del arms[name]
        for arm in list(waiting):
            ready = arm not in ("spark", "glimmer") or (RESPONSES / f"d185-{arm}.jsonl").exists()
            if len(arms) < SLOTS and ready:
                waiting.remove(arm)
                if (RUNS / f"d185-{arm}" / "completion.json").exists():
                    continue
                arms[arm] = start(f"arm-{arm}", arm_command(arm))
        time.sleep(20)
    report = [".venv/bin/python", "experiments/mvp/strategy/proposer/report.py"]
    for arm in ("spark", "glimmer", "search", "rule"):
        report += [f"--{arm}", str((RUNS / f"d185-{arm}").relative_to(ROOT))]
    report += ["--output", str((RUNS / "d185-comparison.json").relative_to(ROOT))]
    return start("report", report).wait()


if __name__ == "__main__":
    raise SystemExit(main())
