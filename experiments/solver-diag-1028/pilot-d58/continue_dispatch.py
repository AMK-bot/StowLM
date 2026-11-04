"""One reserved CPU slot for repair-enabled continuations of raw non-passes."""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from dispatch import HERE, ROOT, THREADS, save

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--minutes-after-raw", type=float, default=0,
                    help="Optional single-worker fallback catch-up after the original raw deadline")
args = parser.parse_args()
deadline = datetime.datetime.fromisoformat(json.loads((HERE / "dispatch-admission.json").read_text())["deadline_utc"]).timestamp() + args.minutes_after_raw * 60
attempted = set()
while time.time() < deadline - 2:
    pending = []
    for path in (HERE / "runs").glob("*/*/done.json"):
        if not (path.parent / "result.json").exists():
            continue
        row = json.loads(path.read_text())
        key = (row["solver"], row["index"])
        target = HERE / "production-continuation" / key[0] / f"{key[1]:03d}"
        if row.get("clean_complete_pass") or key in attempted or (target / "done.json").exists():
            continue
        pending.append((key, path.parent, target))
    if not pending:
        if (HERE / "dispatch-summary.json").exists() and (HERE / "harness-retry-summary.json").exists():
            break
        time.sleep(5)
        continue
    key, raw, target = sorted(pending)[0]
    attempted.add(key)
    target.mkdir(parents=True, exist_ok=True)
    cap = max(.1, min(150, deadline - time.time()))
    command = [sys.executable, str(HERE / "production_continue.py"), "--raw-run", str(raw),
               "--budget", "30", "--cap", str(cap)]
    env = {**os.environ, **{name: "1" for name in THREADS}, "PYTHONDONTWRITEBYTECODE": "1"}
    started = time.monotonic()
    error = None
    with (target / "stdout.log").open("w") as out, (target / "stderr.log").open("w") as err:
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=out, stderr=err)
        save(target / "pid.json", dict(pid=child.pid, owner_pid=os.getpid(), command=command,
                                       started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), cap_s=cap))
        try:
            code = child.wait(timeout=cap + 5)
        except subprocess.TimeoutExpired:
            error = "owned continuation exceeded cap"
            child.terminate()
            try:
                code = child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                code = child.wait()
    path = target / "result.json"
    result = json.loads(path.read_text()) if path.exists() else dict(error=error or "no continuation checkpoint")
    result.update(solver=key[0], index=key[1], returncode=code, process_wall_s=time.monotonic() - started)
    if error:
        result["dispatch_error"] = error
    save(target / "done.json", result)
    print(json.dumps(dict(solver=key[0], index=key[1], passed=result.get("clean_complete_pass"),
                          status=result.get("final_status"), error=result.get("error"), wall_s=result["process_wall_s"])), flush=True)
save(HERE / "continuation-dispatch-summary.json", dict(attempted=len(attempted), ended_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
