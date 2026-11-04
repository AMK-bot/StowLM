"""Audit measured quality-campaign compute without counting summaries twice.

Pipeline totals include their phase times. Exact-window times are nested inside
improvement search, and exact search includes its own checker/ballast work; they
must not be added again. Reused instance audits describe earlier generation,
not fresh generation in the consuming job. Partial checkpoints give lower bounds
on consumed work, never guessed completion times or peak memory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def seconds(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def summarize_row(path, row, directory):
    phases = {}
    for phase in ("construct", "improve", "exact"):
        value = row.get(phase, {})
        if not isinstance(value, dict):
            continue
        phases[phase] = {"search_s": seconds(value.get("search_s")),
                         "validation_s": seconds(value.get("validation_s")),
                         "status": value.get("status")}
    phase_total = sum(v["search_s"] + v["validation_s"] for v in phases.values())
    audit = row.get("generation_audit", {})
    input_path = row.get("input_path")
    own_generation = bool(input_path and Path(input_path).resolve().is_relative_to(
        (directory / "instances").resolve()))
    generation = seconds(row.get("generation_s"))
    if own_generation:
        generation += seconds(audit.get("generation_s"))
    measured_total = row.get("total_pipeline_s")
    if measured_total is not None:
        observed = seconds(measured_total)
        basis = "completed pipeline total; phase/generation times included, not added"
    else:
        observed = phase_total + generation
        basis = "checkpoint phase times plus own generation; uncheckpointed work unavailable"
    changed = bool(row.get("source_changed_during_run"))
    incomplete = not row.get("finished", False)
    return {
        "path": str(path.relative_to(ROOT)), "finished": not incomplete,
        "source_changed": changed, "censored": changed or incomplete,
        "censor_reasons": (["source changed during run"] if changed else []) +
                          (["incomplete checkpoint; additional work unmeasured"] if incomplete else []),
        "phases": phases, "own_generation_s": generation,
        "reused_generation_audit_not_added_s": (seconds(audit.get("generation_s"))
                                                  if audit and not own_generation else 0.0),
        "observed_work_s": observed, "observed_work_basis": basis,
        "input_sha256": row.get("input_sha256"),
        "generation_status": row.get("generation_status"),
    }


def campaign(directory):
    manifest = read(directory / "manifest.json")
    identity = manifest.get("identity", {})
    rows = [summarize_row(path, read(path), directory)
            for path in sorted((directory / "results").glob("*.json"))]
    planned = len(manifest.get("selections", []))
    checkpoint_complete = len(rows) == planned and all(r["finished"] for r in rows)
    observed = sum(r["observed_work_s"] for r in rows)
    interrupted_note = ROOT / f"{directory.name}-censored.json"
    censored_note = read(interrupted_note) if interrupted_note.exists() else None
    changed = any(r["source_changed"] for r in rows)
    return {
        "job": directory.name, "kind": "cohort", "solver_version": identity.get("solver_version"),
        "configured_cores": identity.get("cores"), "peak_ram_gb": None, "gpu": False,
        "estimated_wall_s": None, "estimate_note": "No per-job admission estimate stored; configured caps are not predictions",
        "configured_budgets_s": identity.get("budgets_s"),
        "planned_inputs": planned, "checkpoint_rows": len(rows),
        "checkpoint_complete": checkpoint_complete, "source_changed": changed,
        "censored": not checkpoint_complete or changed or censored_note is not None,
        "interruption_note": censored_note,
        "observed_work_s": observed,
        "actual_wall_s": observed if checkpoint_complete else None,
        "wall_qualification": "Sum of sequential recorded pipelines; excludes interpreter/driver startup and exit overhead",
        "observed_core_seconds": observed * identity.get("cores", 1),
        "core_seconds_qualification": "reserved one-core work accounting, not measured CPU utilization",
        "rows": rows,
    }


def standalone(path, data):
    rows = data if isinstance(data, list) else [data]
    measured = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        total = next((row[key] for key in ("total_s", "wall_time_s", "seconds", "search_s")
                      if isinstance(row.get(key), (int, float))), None)
        if total is None:
            continue
        measured.append({"index": index, "rung": row.get("rung", row.get("selection", {}).get("rung")),
                         "observed_work_s": float(total), "search_s": row.get("search_s"),
                         "validation_and_other_s": (max(0.0, float(total)-row["search_s"])
                                                    if row.get("search_s") is not None else None),
                         "status": row.get("status"), "source_changed": bool(row.get("source_changed"))})
    if not measured:
        return None
    observed = sum(row["observed_work_s"] for row in measured)
    return {"job": path.name, "kind": "standalone pinned/exact/probe",
            "configured_cores": 1, "peak_ram_gb": None, "gpu": False,
            "estimated_wall_s": None, "configured_budget_s": data.get("budget_s") if isinstance(data, dict) else None,
            "estimate_note": "Per-job admission estimate unavailable",
            "observed_work_s": observed, "observed_core_seconds": observed,
            "actual_wall_s": observed, "censored": any(row["source_changed"] for row in measured),
            "rows": measured}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "quality-compute-report.json")
    parser.add_argument("--whole-pytest-status", choices=("running", "passed", "failed", "unknown"), default="running")
    parser.add_argument("--umbrella-finish-utc", help="Explicit leader-observed ISO timestamp; never inferred from file mtimes")
    args = parser.parse_args()
    if args.output.resolve() != (ROOT / "quality-compute-report.json").resolve():
        parser.error("output must be quality-compute-report.json in this experiment directory")
    jobs = [campaign(path) for path in sorted(ROOT.glob("quality*"))
            if path.is_dir() and (path / "manifest.json").exists()]
    ignored = []
    for path in sorted(ROOT.glob("quality*.json")):
        if path.resolve() == args.output.resolve():
            continue
        data = read(path)
        result = standalone(path, data)
        if result is None:
            ignored.append(str(path.relative_to(ROOT)))
        else:
            jobs.append(result)
    logs = []
    for path in sorted(ROOT.glob("pytest-quality*.log")):
        content = path.read_text(errors="replace")
        logs.append({"path": path.name, "wall_s": None,
                     "status": (f"{args.whole_pytest_status} (leader supplied)" if path.name == "pytest-quality-final-full.log" else
                                "failure summary recorded" if "FAILED " in content else
                                "completion marker recorded; exit code unavailable" if "[100%]" in content else
                                "status unavailable from log"),
                     "tail": content.splitlines()[-5:]})
    now = datetime.now(timezone.utc)
    finished = datetime.fromisoformat(args.umbrella_finish_utc) if args.umbrella_finish_utc else None
    start = datetime(2026, 10, 27, 21, 6, tzinfo=timezone.utc)
    if finished is not None and (finished.tzinfo is None or finished < start):
        parser.error("umbrella finish must be a timezone-aware timestamp after its start")
    report = {
        "schema": "scalable-quality-compute@1", "generated_utc": now.isoformat(),
        "umbrella": {"start_utc": "2026-10-27T21:06:00+00:00", "estimated_wall_s": 3600,
                     "reserved_cores": 4, "reserved_ram_gb": 4, "gpu": False,
                     "actual_finish_utc": finished.isoformat() if finished else None,
                     "actual_wall_s": (finished-start).total_seconds() if finished else None,
                     "status": "finished (leader supplied timestamp)" if finished else "ongoing at report generation; incomplete jobs remain censored",
                     "elapsed_at_report_s": (now-start).total_seconds(),
                     "overlap_disclosure": {"reported_logical_jobs": 5, "reported_duration_s_approx": 8,
                                            "action": "Owned pilot was stopped after overlap was found",
                                            "cpu_utilization": None,
                                            "qualification": "Leader-reported brief logical-job overlap; not concealed or asserted as measured active cores"}},
        "accounting": {"policy": "Each row pipeline or standalone top-level duration counted once; no summary-row or nested exact-window duplication",
                       "generation": "Own successful generation included in pipeline, own generation-only audit counted once; reused audits excluded",
                       "censored": "Incomplete checkpoints expose measured lower bounds; source-changing completed jobs retain compute but cannot qualify fixed-source quality",
                       "unavailable": "Peak RAM, process startup/exit overhead, missing per-job estimates, uncheckpointed interrupted work and pytest durations are not guessed",
                       "observed_job_seconds_sum": sum(job["observed_work_s"] for job in jobs),
                       "observed_reserved_core_seconds_sum": sum(job["observed_core_seconds"] for job in jobs),
                       "not_umbrella_wall": "Parallel cohort durations sum to work, not elapsed umbrella wall time"},
        "jobs": jobs, "ignored_aggregate_or_note_files": ignored, "pytest_logs": logs,
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    print(json.dumps({"jobs": len(jobs), "observed_job_seconds_sum": report["accounting"]["observed_job_seconds_sum"],
                      "censored_jobs": sum(job["censored"] for job in jobs), "output": str(args.output)}))


if __name__ == "__main__":
    main()
