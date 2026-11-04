"""Fresh, unchanged D49 edge designs; three independent ship workers."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

from ordinary_campaign import run as accounted_run, abort_check

RUN = Path(__file__).resolve().parent
OWNER = RUN.parent.parent
SHIPS = ("feeder_500", "feeder_1000", "feeder_1700")
LOGS = RUN / "edge-logs"
LOGS.mkdir(exist_ok=True)


def run(ship, version, phase, extra=()):
    if version in (1, 2):
        # Every checkpoint pins its corresponding ordinary transfer record.
        # Phase scheduling must wait for that record, independently per ship.
        began = time.monotonic()
        while not (RUN / "ordinary" / ship / f"{phase}-light.json").exists():
            if time.monotonic()-began > 2400:
                raise TimeoutError(f"ordinary {phase} transfer missing for {ship}")
            abort_check()
            time.sleep(3)
    name = f"{ship}-v{version}-{phase}"
    prior_path = LOGS / (name + ".execution.json")
    if prior_path.exists():
        prior = json.loads(prior_path.read_text())
        if prior.get("exit_code") == 0:
            print(json.dumps(dict(reused_command=name, pid=prior["pid"])), flush=True)
            return
        if "exit_code" not in prior:
            raise ValueError(f"refuse duplicate active edge job: {name}")
        for suffix in (".execution.json", ".log"):
            path = LOGS / (name + suffix)
            archive = LOGS / (name + ".initial" + suffix)
            if archive.exists():
                raise ValueError(f"preserve multiple failed edge attempts: {archive}")
            path.rename(archive)
    script = OWNER / ("edge_grid.py" if version == 1 else f"edge_grid_v{version}.py")
    command = [sys.executable, str(script), "--ship", ship, "--phase", phase,
        "--prepared", str(RUN / "ordinary"), "--output", str(RUN / f"edges-v{version}"),
        *map(str, extra)]
    # Advertise scheduling before launching: prefetch publication must not
    # race a canonical producer which has already begun waiting for capacity.
    prior_path.write_text(json.dumps(dict(command=command, queued=True),indent=2)+"\n")
    # Shared accounting wraps the original producer with profile_run.py and
    # enforces the same dynamic numerical capacity as the ordinary workers.
    accounted_run("edge-"+name, [script, "--ship", ship, "--phase", phase,
        "--prepared", RUN/"ordinary", "--output", RUN/f"edges-v{version}", *extra])
    observed=json.loads((RUN.parent/"compute-logs"/("freshwater-edge-"+name+".execution.json")).read_text())
    record=dict(command=command,pid=observed["pid"],exit_code=0,seconds=observed["seconds"])
    prior_path.write_text(json.dumps(record,indent=2)+"\n")


def ship_worker(ship):
    # Each experiment captures live numerical inputs independently. Never reuse
    # the two-state timing pilot or historical raw calibration.
    began = time.monotonic()
    while not (RUN / "ordinary" / ship / "train-light.json").exists():
        if time.monotonic()-began > 2400:
            raise TimeoutError(f"ordinary training not available for {ship}")
        abort_check()
        time.sleep(3)
    for version in (1, 2):
        for phase in ("train", "holdout"):
            run(ship, version, phase)
    # One resumable lattice shard per ship evaluates the identical 800 requests;
    # only the scheduling partition differs from the previous eight-shard run.
    run(ship, 3, "lattice", ("--shard-count", 1))
    run(ship, 3, "finalize", ("--shard-count", 1,
        "--prior-training-root", RUN / "edges-v2"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ship", choices=SHIPS)
    args = parser.parse_args()
    selected = (args.ship,) if args.ship else SHIPS
    label = args.ship or "all"
    started = time.monotonic()
    (RUN / f"edges-controller-{label}.pid").write_text(str(os.getpid()) + "\n")
    with ThreadPoolExecutor(max_workers=len(selected)) as pool:
        futures = [pool.submit(ship_worker, ship) for ship in selected]
        for future in as_completed(futures):
            future.result()
    record = dict(passed=True, seconds=time.monotonic()-started, workers=len(selected),
        peak_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        controller_pid=os.getpid())
    (RUN / f"edges-execution-{label}.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record), flush=True)
