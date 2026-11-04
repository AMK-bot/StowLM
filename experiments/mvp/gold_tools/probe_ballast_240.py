"""D100: sequential 240-second ballast measurements on seven failed cases.

APPROXIMATION: old failed attempts did not persist their unfinished plans;
reconstruct candidates under the same pinned runtime, seed and solver settings.
This measures these instances, not the byte-identical lost candidate plans.
Experimental results stay outside published gold until independently admitted.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import time

import driver


def main():
    output = driver.LIVE_ROOT / "experiments/mvp/gold"
    destination = output / "D100-ballast-240"
    destination.mkdir(exist_ok=True)
    ledger = driver.LIVE_ROOT / "experiments/fleet/ledger.md"
    owner = "mvp-gold/v4-diagnostic"
    epoch = json.loads((output / "runtime-epoch.json").read_text())
    cases = [x for x in json.loads((output / "D98-RERUN.json").read_text())["rows"]
             if x["drop_reason"] == "ballast"]
    assert len(cases) == 7
    runtime = output / "runtime" / f"{os.getpid()}.json"
    started = time.monotonic()
    def state(status, active=False):
        driver.atomic_json(runtime, dict(pid=os.getpid(), status=status,
            active_worker_pids=[os.getpid()] if active else [],
            updated_utc=datetime.now(timezone.utc).isoformat()))
    state("diagnostic_queued")
    while True:
        with driver.shared_admission_lock(output):
            admission = driver.select_admission(ledger, 1, True, owner,
                admission_mode="measured_cpu", active_gold_workers=driver.shared_active_workers(output),
                recent_start_workers=driver.recent_live_workers(output, lease_s=5))
            if admission["admitted"]:
                state("diagnostic", True)
                driver.update_ledger_reservation(ledger, owner, 1)
                break
        time.sleep(1)
    run_started = time.monotonic()
    report = dict(estimate=dict(cores=1, ram_gb=2, gpu=False,
        wall_s_upper=7*(120+240+120+180), basis="measured prior constructor120 + ballast120 timeout; new ballast cap240; checker120/mask180 upper bounds"),
        runtime_epoch_sha256=epoch["runtime_epoch_sha256"], admission=admission,
        admission_wait_s=run_started-started, requested=7, completed=0, passed=0,
        approximation="Candidates reconstructed because failed original plans were not retained.", cases=[])
    try:
        driver.configure_runtime(epoch["runtime_root"], epoch["runtime_epoch_sha256"])
        from stowage.io import load_instance
        from stowage.feasibility import FeasibilityConfig
        from stowage.solver.scalable import ScalableConfig, ScalableSolver
        from stowage.planner import finalize
        from stowage.checker import check
        for target in cases:
            case_path = destination / target["case"]
            case_path.mkdir(parents=True, exist_ok=True)
            result_path = case_path / "measurement.json"
            if result_path.exists() and json.loads(result_path.read_text()).get("completed"):
                row = json.loads(result_path.read_text())
            else:
                row = dict(case=target["case"], completed=False, passed=False,
                    input_sha256=target.get("input_sha256"), started_utc=datetime.now(timezone.utc).isoformat())
                driver.atomic_json(result_path, row)
                try:
                    source_record = json.loads((output / target["case"] / "result.json").read_text())
                    input_path = Path(source_record["input_path"])
                    row["input_sha256"] = driver.sha256(input_path)
                    assert row["input_sha256"] == source_record["input_sha256"]
                    with driver.scoped_ballast_v4(False):
                        instance = load_instance(input_path)
                        phase = time.monotonic()
                        options = dict(json.loads((output / "small-scalable.json").read_text()),
                            securing_screen=True, heavy_first_ordering=True)
                        with driver.deadline(125):
                            candidate = ScalableSolver(ScalableConfig(
                                feasibility_config=FeasibilityConfig(extra={"global_screen": 0}), **options
                            )).plan(instance, seed=instance.seed, time_limit_s=120)
                        row.update(constructor_s=time.monotonic()-phase, constructor_status=candidate.status,
                            constructor_metrics=candidate.metrics)
                        if candidate.status != "ok" or candidate.plan is None:
                            raise RuntimeError(f"Candidate reconstruction failed: {candidate.status}: {candidate.message}")
                        (case_path / "candidate.json").write_text(candidate.model_dump_json(indent=2))
                        driver.atomic_json(result_path, row)
                        phase = time.monotonic()
                        with driver.deadline(245), driver.scoped_ballast_v4(True):
                            finished = finalize(instance, candidate, time_limit_s=240)
                        row.update(ballast_s=time.monotonic()-phase, ballast_status=finished.status,
                            ballast_message=finished.message, ballast_metrics=finished.metrics,
                            ballast_found=finished.status == "ok" and finished.plan is not None and finished.plan.ballast is not None)
                        driver.atomic_json(result_path, row)
                        if not row["ballast_found"]:
                            raise RuntimeError(f"240-second ballast: {finished.status}: {finished.message}")
                        (case_path / "finished.json").write_text(finished.model_dump_json(indent=2))
                        phase = time.monotonic()
                        with driver.deadline(120):
                            checker = check(instance, finished.plan)
                        row.update(checker_s=time.monotonic()-phase, checker_passed=checker.passed)
                        (case_path / "checker.json").write_text(checker.model_dump_json(indent=2))
                        if not checker.passed:
                            raise RuntimeError("Full checker rejected 240-second result")
                        replay = {}
                        phase = time.monotonic()
                        with driver.deadline(180):
                            driver.replay(instance, finished.plan, replay, securing=True)
                        row.update(mask_s=time.monotonic()-phase, mask=replay, passed=bool(replay.get("passed")))
                        row["imported_modules"] = driver.assert_snapshot_imports()
                except Exception as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                row.update(completed=True, finished_utc=datetime.now(timezone.utc).isoformat())
                driver.atomic_json(result_path, row)
            report["cases"].append(row)
            report.update(completed=len(report["cases"]), passed=sum(x["passed"] for x in report["cases"]),
                ballast_found=sum(x.get("ballast_found", False) for x in report["cases"]),
                run_s=time.monotonic()-run_started)
            driver.atomic_json(destination / "REPORT.json", report)
    finally:
        report.update(run_s=time.monotonic()-run_started,
            peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            finished_utc=datetime.now(timezone.utc).isoformat())
        driver.atomic_json(destination / "REPORT.json", report)
        driver.update_ledger_reservation(ledger, owner, 0)
        state("diagnostic_finished")


if __name__ == "__main__":
    main()
