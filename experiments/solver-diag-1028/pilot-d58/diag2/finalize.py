"""Summarize completed observations; never starts a planner."""
import datetime as dt
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    cases = json.loads((HERE / "notes/selection.json").read_text())["selected"]
    records = []
    starts, ends = [], []
    table = ["| Ship | Solver | Complete | Checker / shippable | Actual solve s, median / max |",
             "|---|---|---:|---:|---:|"]
    for case in cases:
        for solver in ("scalable", "twostep"):
            directory = HERE / "runs" / solver / f"{case['index']:03d}"
            result_path = directory / "done.json"
            result = json.loads(result_path.read_text())
            result["ship"] = case["spec"]["vessel"]
            records.append(result)
            starts.append(dt.datetime.fromisoformat(json.loads((directory / "pid.json").read_text())["started_utc"]))
            ends.append(dt.datetime.fromtimestamp(result_path.stat().st_mtime, dt.timezone.utc))
    groups = []
    for ship in sorted({r["ship"] for r in records}, key=lambda s: int(s.split("_")[-1])):
        for solver in ("scalable", "twostep"):
            rows = [r for r in records if r["ship"] == ship and r["solver"] == solver]
            times = [r["solve_s"] for r in rows]
            group = dict(ship=ship, solver=solver, n=len(rows),
                         complete=sum(r["completion"] == "complete" for r in rows),
                         checker=sum(bool(r.get("checker_passed")) for r in rows),
                         shippable=sum(bool(r.get("shippable")) for r in rows),
                         median_s=statistics.median(times), max_s=max(times))
            groups.append(group)
            table.append(f"| {ship} | {solver} | {group['complete']}/{group['n']} | {group['checker']}/{group['n']} / {group['shippable']}/{group['n']} | {group['median_s']:.2f} / {group['max_s']:.2f} |")
    compute = dict(
        estimate="4 cores / 12 GB / no GPU, approximately 63 minutes compute",
        long_trials=len(records), long_worker_wall_sum_s=sum(r["wall_s"] for r in records),
        long_dispatch_span_s=(max(ends)-min(starts)).total_seconds(),
        long_start_utc=min(starts).isoformat(), long_end_utc=max(ends).isoformat(),
        largest_worker_peak_rss_gib=max(r["peak_rss_gb"] + r.get("child_peak_rss_gb", 0) for r in records),
        maximum_admitted_numeric_workers=4, aggregate_peak_ram_measured=False,
        profile_all_attempts_worker_wall_s=177.436, stalls_worker_wall_sum_s=62.513,
        note="Worker wall sum is occupied worker time, not measured CPU time. Aggregate RAM was not sampled; per-worker RSS was recorded. No GPU or cloud use.")
    (HERE / "aggregate-table.md").write_text("\n".join(table) + "\n")
    (HERE / "aggregate.json").write_text(json.dumps(groups, indent=2) + "\n")
    (HERE / "compute-summary.json").write_text(json.dumps(compute, indent=2) + "\n")
    mass = json.loads((HERE / "mass-contract.json").read_text()) if (HERE / "mass-contract.json").exists() else {"cases": []}
    contradictions = {r["index"] for r in mass["cases"] if r["checker_model_mass_contradiction"]}
    compact = [dict(index=r["index"], ship=r["ship"], solver=r["solver"],
                    loaded=r.get("cargo_loaded"), total=r["n_loads"], rules=r.get("rules", []),
                    states=r.get("states", {}), ballast=r.get("ballast_status"),
                    mask_failure=(r.get("mask_replay") or {}).get("failure_kind"),
                    source_drift=r.get("source_drift"),
                    attribution="instance: pinned checker-model mass contradiction" if r["index"] in contradictions else "solver first-candidate timeout; global instance feasibility unknown") for r in records]
    (HERE / "failure-detail.json").write_text(json.dumps(compact, indent=2) + "\n")
    print(json.dumps(compute))


if __name__ == "__main__":
    main()
