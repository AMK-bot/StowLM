"""Summarize recorded elapsed times, admissions and per-process RSS."""
import datetime
import json
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    phases = {}
    intervals = []
    for phase, pattern in (("generation", "generation/outcomes/*.json"),
                           ("raw_solver", "runs/*/*/done.json"),
                           ("production_continuation", "production-continuation/*/*/done.json"),
                           ("cli_errors_no_solver_work", "harness-errors/*/*/done.json")):
        paths = list(HERE.glob(pattern))
        rows = [json.loads(p.read_text()) for p in paths]
        phases[phase] = dict(count=len(rows), recorded_worker_elapsed_s=sum(
            r.get("process_wall_s", r.get("elapsed_s", 0)) for r in rows),
            max_parent_rss_gib=max((r.get("peak_rss_gb", 0) for r in rows), default=0),
            max_child_rss_gib=max((r.get("child_peak_rss_gb", 0) for r in rows), default=0))
        for path, row in zip(paths, rows):
            pid = (HERE / "generation/logs" / path.stem / "pid.json") if phase == "generation" else path.parent / "pid.json"
            done = (HERE / "generation/logs" / path.stem / "done.json") if phase == "generation" else path
            if pid.exists() and done.exists():
                started = datetime.datetime.fromisoformat(json.loads(pid.read_text())["started_utc"]).timestamp()
                intervals.append((started, done.stat().st_mtime, phase))
    suites = list(ET.parse(HERE / "solver-verification.xml").getroot().iter("testsuite"))
    for suite in suites:
        started = datetime.datetime.fromisoformat(suite.attrib["timestamp"]).timestamp()
        intervals.append((started, started + float(suite.attrib["time"]), "default_tests"))
    events = sorted([(start, 1) for start, _, _ in intervals] + [(end, -1) for _, end, _ in intervals])
    active = maximum = 0
    for _, delta in events:
        active += delta
        maximum = max(maximum, active)
    starts = [datetime.datetime.fromisoformat(json.loads(p.read_text())["started_utc"]).timestamp()
              for p in (HERE / "generation/outcomes").glob("*.json")]
    end = max((end for _, end, _ in intervals), default=0)
    summary = dict(estimate=dict(cores=6, ram_gb=16, gpu=False, run_minutes=65, report_minutes=10),
                   actual_phases=phases, observed_recorded_job_overlap_max=maximum,
                   timing_start_utc=datetime.datetime.fromtimestamp(min(starts), datetime.timezone.utc).isoformat(),
                   timing_last_completed_utc=datetime.datetime.fromtimestamp(end, datetime.timezone.utc).isoformat(),
                   elapsed_to_last_completed_s=end-min(starts), gpu=False, cloud_cost=0,
                   aggregate_peak_ram="not instrumented; RSS fields are individual maxima, not simultaneous group usage",
                   cpu_time="not instrumented; elapsed totals include waiting for owned child workers and are not CPU-hours",
                   scope="Completed child jobs and default tests; initial single generation probe has no dispatcher PID record and was alone before dispatch.")
    (HERE / "compute-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
