"""Resumable 1200s baseline jobs, initially3CPU plus1 diagnostic slot."""
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(value, default=str) + "\n")
    temporary.replace(path)


def run(job):
    solver, selected = job
    index = selected["index"]
    directory = HERE / "runs" / solver / f"{index:03d}"
    if (directory / "done.json").exists():
        return json.loads((directory / "done.json").read_text())
    directory.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(HERE / "long_worker.py"), "--input", str(ROOT / selected["input"]),
               "--output", str(directory), "--solver", solver, "--budget", "1200", "--ballast-budget", "30", "--cap", "1320"]
    env = {**os.environ, **{name: "1" for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")},
           "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    started = time.monotonic()
    with (directory / "stdout.log").open("w") as out, (directory / "stderr.log").open("w") as err:
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=out, stderr=err)
        save(directory / "pid.json", dict(pid=child.pid, owner_pid=os.getpid(), command=command,
             started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
        try:
            code = child.wait(timeout=1330)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                code = child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                code = child.wait()
    path = directory / "result.json"
    row = json.loads(path.read_text()) if path.exists() else dict(error="no worker checkpoint")
    row.update(index=index, solver=solver, requested_spec=selected["spec"], returncode=code,
               process_wall_s=time.monotonic()-started)
    save(directory / "done.json", row)
    print(json.dumps(dict(index=index, solver=solver, completion=row.get("completion"), passed=row.get("clean_complete_pass"),
                          solve_s=row.get("solve_s"), wall_s=row["process_wall_s"], error=row.get("error"))), flush=True)
    return row


def main():
    selection = json.loads((HERE / "notes/selection.json").read_text())["selected"]
    jobs = [(solver, row) for row in selection for solver in ("scalable", "twostep")]
    save(HERE / "admission.json", dict(started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
         jobs=len(jobs), budget_s=1200, cap_s=1320, initial_long_workers=3, total_reserved_workers=4, ram_gb=12))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        active = {}
        while jobs or active:
            control = HERE / "control.json"
            slots = min(4, int(json.loads(control.read_text())["workers"])) if control.exists() else 3
            while jobs and len(active) < slots:
                job = jobs.pop(0)
                active[pool.submit(run, job)] = job
            ready, _ = concurrent.futures.wait(active, timeout=2, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in ready:
                job = active.pop(future)
                try:
                    future.result()
                except Exception as exc:
                    save(HERE / "dispatch-error.json", dict(job=job, error=repr(exc)))
                    raise
            save(HERE / "progress.json", dict(active=len(active), queued=len(jobs)))
    save(HERE / "dispatch-summary.json", dict(ended_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), jobs=12))


if __name__ == "__main__":
    main()
