"""Account for measured jobs; subjobs overlap and must not be summed as wall."""
from datetime import datetime
import json
from pathlib import Path

RUN = Path(__file__).resolve().parent


def read(relative):
    return json.loads((RUN/relative).read_text())


def utc(value):
    return datetime.fromisoformat(value).timestamp()


def main():
    admission = read("admission.json")
    finish = read("finish-execution.json")
    results = read("results.json")
    end = utc(finish["finished_utc"])
    campaign_end = max(utc(value["qualification_end_utc"]) for value in results.values())
    evidence_ready = None
    for line in (RUN/"finish-campaign.log").read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if "evidence_ready_utc" in event:
            evidence_ready = utc(event["evidence_ready_utc"])
    if evidence_ready is None:
        raise ValueError("qualification-ready time missing")
    initial_raw = [json.loads(path.read_text()) for path in
        (RUN/"ordinary-logs-time-denied").glob("*.execution.json")]
    raw_wall = max(record["seconds"] for record in initial_raw)
    ordinary_wall = read("ordinary-logs/campaign.execution.json")["seconds"]
    jobs = {
        "campaign": dict(cores_cap=9, ram_gb=24, estimate_initial_s=1800,
            estimate_revised_s=2100, actual_s=campaign_end-utc(admission["start_utc"])),
        "ordinary_raw_and_resume": dict(cores_cap=6, ram_gb=16, estimate_s=1200,
            fresh_raw_s=raw_wall, resumed_controller_s=ordinary_wall,
            actual_execution_s=raw_wall+ordinary_wall,
            note="Excludes wrapper-repair downtime; initial time-l wrappers returned sandbox error after valid raw output."),
        "diagnostics": dict(cores_cap=1, ram_gb=2, estimate_s=90,
            actual_s=read("diagnostics-summary.json")["actual_child_wall_s"]),
        "lattice_prefetch": dict(cores_cap=3, ram_gb=6, estimate_s=240,
            actual_s=read("lattice-prefetch-logs/campaign.execution.json")["seconds"],
            published_prefixes=3, states_per_prefix=256),
        "replay_prefetch": dict(cores_cap=3, ram_gb=6, estimate_s=180,
            actual_s=read("replay-prefetch-logs/campaign.execution.json")["seconds"],
            published_prefixes=0, unused_states=144,
            note="Canonical replay started before publication window; every attempt safely skipped."),
        "final_checks": dict(cores_cap=7, ram_gb=16,
            estimates_s=[600,900,1500],actual_s=end-evidence_ready),
    }
    for name in ("candidate", "candidate-gates", "promotion", "cache-warm", "public-validate",
                 "aliases", "production-smoke", "holdout-replay", "linear-feasibility-tests"):
        record = read(f"finish-logs/{name}.execution.json")
        jobs[name] = {key:record[key] for key in
            ("exit_code", "elapsed_s", "peak_child_rss_mib", "start", "child_pid")}
    jobs["linear-feasibility-tests"].update(estimate_s=480,cores_cap=1,ram_gb=4)
    jobs["cache-warm"].update(estimate_s=180,cores_cap=3,ram_gb=4)
    peak_bytes = []
    metadata = []
    for path in RUN.rglob("*.execution.json"):
        record = json.loads(path.read_text())
        metadata.append(str(path.relative_to(RUN)))
        for key,value in record.items():
            if isinstance(value,(int,float)) and "rss" in key:
                if key.endswith("_bytes"):
                    peak_bytes.append(value)
                elif key.endswith("_mib"):
                    peak_bytes.append(value*1024**2)
    for path in RUN.glob("edges-execution-*.json"):
        peak_bytes.append(json.loads(path.read_text())["peak_child_rss_bytes"])
    summary = dict(passed=finish["passed"],gpu=False,paid_compute=False,
        start_utc=admission["start_utc"],end_utc=finish["finished_utc"],
        total_reserved_window_s=end-utc(admission["start_utc"]),jobs=jobs,
        peak_observed_child_rss_bytes=max(peak_bytes),aggregate_peak_ram_measured=False,
        execution_metadata=metadata,
        note="Overlapping subjob walls are not additive; live reservation reduced9CPU/24GB ->7CPU/16GB ->2CPU/6GB.")
    (RUN/"compute-summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(json.dumps(dict(campaign_s=jobs["campaign"]["actual_s"],
        final_checks_s=jobs["final_checks"]["actual_s"],
        total_s=summary["total_reserved_window_s"],peak_child_rss_gib=max(peak_bytes)/1024**3)))


if __name__ == "__main__":
    main()
