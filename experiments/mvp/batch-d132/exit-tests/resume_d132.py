"""D132 v2 resume after the owning session was lost (fix-t8-sft, D132 v2 order): same worker, same immutable runtime and
environment as the lost attempt (recorded in v2-full-checker100-process/process-attempt1-lost-session.json); the worker
skips and re-verifies the 56 checkpointed reports. Then the two fresh complete-sample gates. Owned, reaped, recorded."""
import json, os, resource, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUNTIME = "/private/tmp/stowlm-d132-exit-qualified-runtime-20261030"
env = {**os.environ, **{k: "1" for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
       "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "RAYON_NUM_THREADS", "STOWAGE_VESSEL_SINGLEFLIGHT",
       "PYTHONDONTWRITEBYTECODE", "UV_OFFLINE")}, "PYTHONHASHSEED": "0", "PYTHONPATH": str(HERE)}
PY = str(ROOT / ".venv/bin/python")


def run(name, argv, wall_limit):
    d = HERE / name
    d.mkdir(exist_ok=True)
    rec = {"started_utc": datetime.now(timezone.utc).isoformat(), "argv": argv, "cwd": RUNTIME, "gpu": False,
           "native_threads": 1, "owner": "fix-t8-sft resume", "reaped": False}
    (d / "process.json").write_text(json.dumps(rec, indent=1))
    t = time.perf_counter()
    with open(d / "stdout.txt", "a") as out, open(d / "stderr.txt", "a") as err:
        p = subprocess.Popen(argv, cwd=RUNTIME, env=env, stdout=out, stderr=err)
        rec["owned_pid"] = p.pid
        code = p.wait(timeout=wall_limit)
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    rec.update(reaped=True, exit_code=code, wall_s=round(time.perf_counter() - t, 2), cpu_s=ru.ru_utime + ru.ru_stime,
               peak_waited_child_rss_bytes=ru.ru_maxrss, source_drift=False)
    (d / "process.json").write_text(json.dumps(rec, indent=1))
    print(name, "exit", code, rec["wall_s"], flush=True)
    return code


if run("v2-full-checker100-process", [PY, str(HERE / "worker.py"), "checks", "--runtime", RUNTIME, "--arm", "candidate",
       "--baseline", str(ROOT / "experiments/mvp/batch-d132/checker/baseline-cold"), "--start", "0", "--limit", "100",
       "--tag", "v2"], 1800) == 0:
    sys.exit(run("fresh-two-samples-process", [PY, str(HERE / "check_samples.py"), RUNTIME, str(HERE / "fresh-two-samples")], 900))
sys.exit(1)
