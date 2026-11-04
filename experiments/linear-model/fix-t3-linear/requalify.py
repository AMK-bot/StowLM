"""Resumable fresh measurements using the owner's unchanged qualification tools.

The historical population design is preserved: no past holdout is relabelled a
pass and no physical-input hash is rewritten. Each current-epoch observation
is freshly measured. One to eight single-native-thread subprocesses run at once.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
OWNER = HERE.parent / "epoch_requalification"
RUN_NAME = os.environ.get("STOWAGE_LINEAR_QUAL_RUN", "epoch")
if RUN_NAME not in ("epoch", "epoch-current", "epoch-final", "epoch-fingerprint", "epoch-claude"):
    raise ValueError("unsupported scoped run name")
OUT = HERE / RUN_NAME
LOGS = HERE / "runs" / RUN_NAME
LOGS.mkdir(parents=True, exist_ok=True)
WORKERS = int(os.environ.get("STOWAGE_LINEAR_QUAL_WORKERS", "4"))
if not 1 <= WORKERS <= 8:
    raise ValueError("qualification workers must be between1 and8")
SHIPS = ("feeder_500", "feeder_1000", "feeder_1700")


def run(name, args):
    start = time.monotonic()
    sampler_source=(OWNER/'targeted.py').read_bytes()
    sampler_digest=hashlib.sha256(sampler_source).hexdigest()
    sampler_archive=OWNER/f'targeted-sampler-{sampler_digest[:12]}.py'
    if sampler_archive.exists():
        if sampler_archive.read_bytes()!=sampler_source:
            raise ValueError('targeted source archive conflicts with its digest')
    else:
        sampler_archive.write_bytes(sampler_source)
    with (LOGS / (name + ".log")).open("w") as log:
        proc = subprocess.Popen([sys.executable, *map(str, args)], stdout=log, stderr=subprocess.STDOUT)
        (LOGS / (name + ".pid")).write_text(str(proc.pid) + "\n")
        code = proc.wait()
    record = dict(name=name, pid=proc.pid, exit_code=code, seconds=time.monotonic()-start)
    (LOGS / (name + ".execution.json")).write_text(json.dumps(record, indent=2)+"\n")
    print(json.dumps(record), flush=True)
    if code:
        raise RuntimeError(f"{name} failed; see its log")


def worker(ship, name):
    spec = importlib.util.spec_from_file_location("owner_driver", OWNER / "driver.py")
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    prepared = driver.prepare(ship, OUT)
    old = json.loads((OWNER / "production" / ship / name).read_text())
    # physical_case has a canonical phase-level name. Separate shard directories
    # preserve independent seeds before copying the original fresh record into
    # the promoter's explicitly named training/enrichment slot.
    attempt = os.environ.get("STOWAGE_LINEAR_QUAL_ATTEMPT", "initial")
    if attempt not in ("initial", "retry-budget120"):
        raise ValueError("unsupported qualification attempt")
    shard_root = RUN_NAME if attempt == "initial" else RUN_NAME+"-"+attempt
    shard = HERE / "shards-explicit-reference-policy" / shard_root / ship / name.removesuffix(".json")
    record = driver.physical_case(ship, old["level"], old["phase"], shard, prepared,
        samples=old["samples"], seed=old["seed"], public=True)
    driver.write(OUT / ship / name, record)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker(sys.argv[2], sys.argv[3])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "repair-500":
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(run,"repair-feeder_500-"+path.stem,
                [Path(__file__),"worker","feeder_500",path.name])
                for path in sorted((OWNER/"production/feeder_500").glob('*.json'))
                if path.name.startswith(('train-','holdout-','enrichment'))]
            for future in as_completed(jobs): future.result()
        return
    start = time.monotonic()
    # Admission is recorded in compute.md before this controller starts.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        raw = [pool.submit(run, "raw-"+ship, [OWNER/"driver.py", "--ship", ship,
            "--output", OUT, "--phase", "raw"]) for ship in SHIPS]
        for future in as_completed(raw):
            future.result()
        jobs = []
        for ship in SHIPS:
            for path in sorted((OWNER / "production" / ship).glob("*.json")):
                if path.name.startswith(("train-", "holdout-", "enrichment")):
                    # Only successful workers publish into OUT. Promotion still
                    # independently validates every numerical/source binding.
                    if (OUT/ship/path.name).exists():
                        continue
                    jobs.append(pool.submit(run, ship+"-"+path.stem,
                        [Path(__file__), "worker", ship, path.name]))
            if not (OUT/ship/"boundary.json").exists():
                jobs.append(pool.submit(run, "boundary-"+ship, [OWNER/"boundary.py",
                    "--ship", ship, "--output", OUT, "--samples", "64", "--holdout-samples", "64"]))
        for kind, seed, samples, objective, pairs in (
            ("min",3821,64,"min",[]), ("grid",3822,128,"grid",[]),
            ("pairs-3823",3823,256,"grid",["DB-M","HEEL","WING-A"]),
            ("pairs-3824",3824,256,"grid",["DB-M","HEEL","WING-A"])):
            shard = HERE / "targeted" / RUN_NAME / kind
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
                # Do not spend the remaining queue on a rejected epoch. Started
                # subprocesses finish normally and retain their own checkpoints.
                for pending in jobs: pending.cancel()
                raise
    for kind in ("min", "grid", "pairs-3823", "pairs-3824"):
        source = HERE/"targeted"/RUN_NAME/kind/"feeder_1000"/"train-light.json"
        shutil.copyfile(source, OUT/"feeder_1000"/("targeted-"+kind+"-light.json"))
    (LOGS/"campaign.execution.json").write_text(json.dumps(dict(
        passed=True,seconds=time.monotonic()-start,workers=WORKERS),indent=2)+"\n")


if __name__ == "__main__":
    main()
