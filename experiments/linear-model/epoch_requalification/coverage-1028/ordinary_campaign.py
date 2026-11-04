"""Unchanged population design at explicit freshwater density; shared dynamic cap."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time

RUN = Path(__file__).resolve().parent
OWNER = RUN.parent
OUT = RUN / "ordinary"
LOGS = RUN / "ordinary-logs"
SHIPS = ("feeder_500", "feeder_1000", "feeder_1700")
WORKERS = 6
THREADS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
           "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS")



def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def acquire_slot(name):
    """All numerical controllers share this capacity; default two CPUs."""
    while True:
        with (RUN/"compute.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            registry=RUN/"compute-active.json"
            active=json.loads(registry.read_text()) if registry.exists() else []
            active=[item for item in active if _alive(item["holder_pid"])]
            capacity_path=RUN/"compute-capacity.json"
            capacity=json.loads(capacity_path.read_text())["workers"] if capacity_path.exists() else 2
            if not 1<=capacity<=9: raise ValueError("shared CPU capacity must be1..9")
            if len(active)<capacity:
                active.append(dict(holder_pid=os.getpid(),name=name))
                registry.write_text(json.dumps(active,indent=2)+"\n")
                return
            registry.write_text(json.dumps(active,indent=2)+"\n")
        time.sleep(.5)


def release_slot():
    with (RUN/"compute.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        registry=RUN/"compute-active.json"
        active=json.loads(registry.read_text()) if registry.exists() else []
        registry.write_text(json.dumps([item for item in active if item["holder_pid"]!=os.getpid()],indent=2)+"\n")


def run(name, args):
    LOGS.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    command = ["/usr/bin/nice", "-n", "10", sys.executable,
               str(Path(__file__)), "execute", name, *map(str, args)]
    env = os.environ.copy()
    env.update({key: "1" for key in THREADS})
    with (LOGS / (name + ".log")).open("w") as log:
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        (LOGS / (name + ".pid")).write_text(str(proc.pid) + "\n")
        (LOGS / (name + ".command.json")).write_text(json.dumps(command) + "\n")
        code = proc.wait()
    record = dict(name=name, pid=proc.pid, exit_code=code,
                  seconds=time.monotonic()-start, command=command,
                  native_threads=1)
    observed = json.loads((LOGS / (name + ".child.execution.json")).read_text())
    record.update(child_pid=observed["child_pid"], max_rss_bytes=observed["max_rss_bytes"])
    (LOGS / (name + ".execution.json")).write_text(json.dumps(record, indent=2)+"\n")
    print(json.dumps(record), flush=True)
    if code:
        raise RuntimeError(f"{name} failed; see its log")


def worker(ship, name):
    from profile_run import load_driver
    driver = load_driver()
    prepared = driver.prepare(ship, OUT)
    old = json.loads((OWNER / "production" / ship / name).read_text())
    shard = RUN / "ordinary-shards" / ship / name.removesuffix(".json")
    record = driver.physical_case(ship, old["level"], old["phase"], shard,
        prepared, samples=old["samples"], seed=old["seed"], public=True)
    driver.write(OUT / ship / name, record)


def equivalence_worker(ship):
    import importlib.util
    from profile_run import load_driver
    driver = load_driver()
    spec = importlib.util.spec_from_file_location("freshwater_promote", OWNER/"promote.py")
    promoter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(promoter)
    raw = json.loads((OUT/ship/"raw.json").read_text())
    record = promoter.verify_equivalence(ship, OUT/ship, raw)
    driver.write(OUT/ship/"equivalence.json", record)


def main():
    if len(sys.argv)>1 and sys.argv[1]=="--set-capacity":
        capacity=int(sys.argv[2])
        if not 1<=capacity<=9: raise ValueError("capacity must be1..9")
        temporary=RUN/"compute-capacity.json.tmp"
        temporary.write_text(json.dumps({"workers":capacity})+"\n")
        temporary.replace(RUN/"compute-capacity.json")
        print(json.dumps({"capacity":capacity}),flush=True)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "execute":
        name = sys.argv[2]
        start = time.monotonic()
        original_args = sys.argv[3:]
        original_script = Path(original_args[0]).resolve()
        command = [sys.executable, *([str(RUN/"profile_run.py")] if original_script.parent == OWNER else []), *original_args]
        acquire_slot(name)
        proc = subprocess.Popen(command)
        (LOGS / (name + ".child.pid")).write_text(str(proc.pid)+"\n")
        record = dict(command=command, wrapper_pid=os.getpid(), child_pid=proc.pid)
        path = LOGS / (name + ".child.execution.json")
        path.write_text(json.dumps(record, indent=2)+"\n")
        status = proc.wait()
        release_slot()
        record.update(exit_code=status, seconds=time.monotonic()-start,
                      max_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
        path.write_text(json.dumps(record, indent=2)+"\n")
        sys.exit(status)
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker(sys.argv[2], sys.argv[3])
        return
    if len(sys.argv)>1 and sys.argv[1]=="equivalence-worker":
        equivalence_worker(sys.argv[2])
        return
    if sys.argv[1:]==["--phase", "equivalence"]:
        for ship in SHIPS:
            run("equivalence-"+ship, [Path(__file__), "equivalence-worker", ship])
        return
    start = time.monotonic()
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / "controller.pid").write_text(str(os.getpid())+"\n")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        raw = [pool.submit(run, "raw-"+ship, [OWNER/"driver.py", "--ship", ship,
            "--output", OUT, "--phase", "raw", "--calibration-samples", "192"])
            for ship in SHIPS]
        for future in as_completed(raw):
            future.result()
        (LOGS / "raw-complete.json").write_text(json.dumps({"seconds": time.monotonic()-start})+"\n")
        print("ALL RAW COMPLETE", flush=True)
        jobs = []
        # Train-light first makes unchanged edge workers runnable promptly.
        populations = []
        for ship in SHIPS:
            for path in sorted((OWNER / "production" / ship).glob("*.json")):
                if path.name.startswith(("train-", "holdout-", "enrichment")):
                    populations.append((ship, path))
        populations.sort(key=lambda row: (row[1].name != "train-light.json", row[0], row[1].name))
        for ship, path in populations:
            jobs.append(pool.submit(run, ship+"-"+path.stem,
                [Path(__file__), "worker", ship, path.name]))
        for ship in SHIPS:
            jobs.append(pool.submit(run, "boundary-"+ship, [OWNER/"boundary.py",
                "--ship", ship, "--output", OUT, "--samples", "64", "--holdout-samples", "64"]))
        for kind, seed, samples, objective, pairs in (
            ("min",3821,64,"min",[]), ("grid",3822,128,"grid",[]),
            ("pairs-3823",3823,256,"grid",["DB-M","HEEL","WING-A"]),
            ("pairs-3824",3824,256,"grid",["DB-M","HEEL","WING-A"])):
            shard = RUN / "targeted" / kind
            args = [OWNER/"targeted.py", "--ship", "feeder_1000", "--level", "light",
                "--output", shard, "--prepared", OUT, "--samples", samples,
                "--seed", seed, "--mass-objective", objective, "--budget-seconds", "1200"]
            if pairs:
                args += ["--tank-pairs", *pairs]
            jobs.append(pool.submit(run, "targeted-"+kind, args))
        for future in as_completed(jobs):
            try:
                future.result()
            except BaseException:
                for pending in jobs:
                    pending.cancel()
                raise
    for kind in ("min", "grid", "pairs-3823", "pairs-3824"):
        source = RUN/"targeted"/kind/"feeder_1000"/"train-light.json"
        shutil.copyfile(source, OUT/"feeder_1000"/("targeted-"+kind+"-light.json"))
    # Sequential numerical equivalence jobs share the same global capacity.
    for ship in SHIPS:
        run("equivalence-"+ship, [Path(__file__), "equivalence-worker", ship])
    (LOGS/"campaign.execution.json").write_text(json.dumps(dict(
        passed=True,seconds=time.monotonic()-start,workers=WORKERS),indent=2)+"\n")


if __name__ == "__main__":
    main()
