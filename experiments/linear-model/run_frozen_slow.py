"""One recorded, single-core slow test run after parent admission (no auto-launch)."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import resource
import runpy
import signal
import subprocess
import sys
import time


def main():
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    ledger = Path("experiments/fleet/ledger.md")
    start = datetime.now(ZoneInfo("Asia/Bangkok")).isoformat(timespec="seconds")
    line = f"| linear-lp/kcs-slow8-frozen | 3.7 | 1 | 1 | no | 8 min | {start} |\n"
    shared_reservation = os.environ.get("STOW_LINEAR_SHARED_RESERVATION") == "1"
    if not shared_reservation:
        with ledger.open("a") as handle:
            handle.write(line)
    folder = Path("experiments/linear-model")
    status_path = folder / "kcs-slow-frozen-status.json"
    log_path = folder / "kcs-slow-frozen.txt"
    process = None
    source_before = None
    source_after = None
    began = time.monotonic()
    result = 1
    try:
        # Remove only the exact fixture's current key. Never clear a shared
        # namespace or caches belonging to the nine-case experiment.
        from stowage.linear.cache import model_cache_key, source_fingerprint, _DEFAULT_ROOT
        from stowage.linear.physics import _params_for
        namespace = runpy.run_path("tests/linear/test_model.py")
        context = namespace["context"].__wrapped__
        context.__globals__["build_linear_model"] = lambda instance: None
        instance, _ = context()
        _, params = _params_for(instance)
        key = model_cache_key(instance, None, 64, params)
        cache_folder = _DEFAULT_ROOT / "model"
        removed = []
        if os.environ.get("STOW_LINEAR_CLEAR_FIXTURE_CACHE") == "1":
            for path in [cache_folder / f"{key}.json", *cache_folder.glob(f"{key}.*.npz")]:
                if path.exists():
                    path.unlink()
                    removed.append(str(path))
        (folder / "kcs-fixture-cache-clean.json").write_text(json.dumps(
            {"fixture_key": key, "removed": removed, "phase": "admitted_frozen_run"}, indent=2))
        source_before = source_fingerprint()
        with log_path.open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-m", "slow",
                                        "tests/linear/test_model.py", "--tb=short"],
                                       stdout=log, stderr=subprocess.STDOUT)
            status_path.write_text(json.dumps({"pid": process.pid, "start": start,
                                              "status": "running"}, indent=2))
            print(f"own pytest PID={process.pid}; log={log_path}", flush=True)
            result = process.wait()
        source_after = source_fingerprint()
    except KeyboardInterrupt:
        result = 130
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
    finally:
        elapsed = time.monotonic() - began
        rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        peak_gb = rss / (1024**3 if sys.platform == "darwin" else 1024**2)
        status = {"pid": process.pid if process is not None else None,
                  "start": start, "status": "complete" if result != 130 else "interrupted",
                  "exit_code": result, "wall_seconds": elapsed, "peak_ram_gb": peak_gb,
                  "source_before": source_before, "source_after": source_after,
                  "shared_root_reservation": shared_reservation}
        status_path.write_text(json.dumps(status, indent=2))
        if not shared_reservation:
            ledger.write_text(ledger.read_text().replace(line, ""))
        with Path("experiments/fleet/history.md").open("a") as handle:
            handle.write(f"\n- linear-lp/kcs-slow8-frozen (3.7), {start}: estimate 1 core / 1 GB / no GPU / 8 min; actual {elapsed:.2f} s, peak child RSS {peak_gb:.3f} GB, exit {result}.\n")
        print(status_path.read_text(), flush=True)
        if log_path.exists():
            print(log_path.read_text(), flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
