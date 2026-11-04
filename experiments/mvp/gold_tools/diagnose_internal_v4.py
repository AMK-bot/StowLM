"""Measure one failed D97 call without publishing or changing solver sources."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import time

import driver


def main():
    output = driver.LIVE_ROOT / "experiments/mvp/gold"
    ledger = driver.LIVE_ROOT / "experiments/fleet/ledger.md"
    owner = "mvp-gold/v4-diagnostic"
    epoch = json.loads((output / "runtime-epoch.json").read_text())
    instance_path = driver.LIVE_ROOT / "experiments/mvp/corpus/train/small/feeder_500/random_yard/seed-030100000.json"
    target = output / "diagnostics/internal-v4.json"
    runtime = output / "runtime" / f"{os.getpid()}.json"
    started = time.monotonic()
    def state(status, active=False):
        driver.atomic_json(runtime, dict(pid=os.getpid(), status=status,
            active_worker_pids=[os.getpid()] if active else [],
            updated_utc=datetime.now(timezone.utc).isoformat()))
    state("diagnostic_queued")
    while True:
        with driver.shared_admission_lock(output):
            detail = driver.select_admission(ledger, 1, True, owner,
                admission_mode="measured_cpu", active_gold_workers=driver.shared_active_workers(output),
                recent_start_workers=driver.recent_live_workers(output, lease_s=5))
            if detail["admitted"]:
                state("diagnostic", True)
                driver.update_ledger_reservation(ledger, owner, 1)
                break
        time.sleep(1)
    run_started = time.monotonic()
    report = dict(instance_path=str(instance_path), instance_sha256=driver.sha256(instance_path),
        runtime_epoch_sha256=epoch["runtime_epoch_sha256"], admission=detail,
        estimate=dict(cores=1, ram_gb=2, gpu=False, wall_s=240))
    try:
        driver.configure_runtime(epoch["runtime_root"], epoch["runtime_epoch_sha256"])
        from stowage.io import load_instance
        instance = load_instance(instance_path)
        report["environment_before"] = os.environ.get("STOWAGE_BALLAST_V4")
        result, metadata = driver.solve_gold_candidate(instance, instance.seed, lns_s=15.)
        report.update(status=result.status, message=result.message, metadata=metadata,
                      metrics=result.metrics, environment_after=os.environ.get("STOWAGE_BALLAST_V4"),
                      imported_modules=driver.assert_snapshot_imports())
    except Exception as exc:
        report.update(error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report.update(actual_run_s=time.monotonic()-run_started,
                      admission_wait_s=run_started-started,
                      peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        driver.atomic_json(target, report)
        driver.update_ledger_reservation(ledger, owner, 0)
        state("diagnostic_finished")
    print(json.dumps({k:report.get(k) for k in ("status", "message", "actual_run_s", "metadata")}))


if __name__ == "__main__":
    main()
