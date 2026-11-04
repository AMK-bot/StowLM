"""Rerun only the two fresh complete-sample gates after the check_samples.py metric-key fix (exit.securing_guard schema:
screens/evaluations/unavailable/fail_open). Attempt 1 is preserved as fresh-two-samples-attempt1-*. Same runtime and env."""
import json, os, resource, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUNTIME = "/private/tmp/stowlm-d132-exit-qualified-runtime-20261030"
env = {**os.environ, **{k: "1" for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
       "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "RAYON_NUM_THREADS", "STOWAGE_VESSEL_SINGLEFLIGHT",
       "PYTHONDONTWRITEBYTECODE", "UV_OFFLINE")}, "PYTHONHASHSEED": "0", "PYTHONPATH": str(HERE)}
d = HERE / "fresh-two-samples-process"
d.mkdir()
argv = [str(ROOT / ".venv/bin/python"), str(HERE / "check_samples.py"), RUNTIME, str(HERE / "fresh-two-samples")]
rec = {"started_utc": datetime.now(timezone.utc).isoformat(), "argv": argv, "cwd": RUNTIME, "gpu": False,
       "native_threads": 1, "owner": "fix-t8-sft rerun after harness key fix", "reaped": False}
t = time.perf_counter()
with open(d / "stdout.txt", "a") as out, open(d / "stderr.txt", "a") as err:
    p = subprocess.Popen(argv, cwd=RUNTIME, env=env, stdout=out, stderr=err)
    rec["owned_pid"] = p.pid
    code = p.wait(timeout=900)
ru = resource.getrusage(resource.RUSAGE_CHILDREN)
rec.update(reaped=True, exit_code=code, wall_s=round(time.perf_counter() - t, 2), cpu_s=ru.ru_utime + ru.ru_stime,
           peak_waited_child_rss_bytes=ru.ru_maxrss, source_drift=False)
(d / "process.json").write_text(json.dumps(rec, indent=1))
print("exit", code, rec["wall_s"])
sys.exit(code)
