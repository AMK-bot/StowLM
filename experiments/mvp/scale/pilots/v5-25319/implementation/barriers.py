"""Read-only TRAIN sweep census; no planner/checker imports or map evaluation."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def certificate(record):
    """Validate stored evidence only: never substitute a fresh check or repair."""
    if not record.get("passed"):
        return None
    assert record["checker_attempted"] and record["checker_passed"]
    assert record["finalize_status"] == "ok" and not record["layout_violations"]
    report = ROOT / record["report_path"]
    finished = ROOT / record["finished_path"]
    assert sha(report) == record["report_sha256"]
    assert sha(finished) == record["finished_sha256"]
    checked, plan = read(report), read(finished)
    assert checked["passed"] and not checked.get("violations")
    assert plan["status"] == "ok" and plan.get("plan") is not None
    moves = checked["count"]["weighted_nonproductive"]
    assert record["moves"] == moves
    return moves


def arm_outcome(arm):
    candidates = [arm.get(name, {}) for name in ("raw", "compact")]
    passes = [(certificate(c), c) for c in candidates if c.get("passed")]
    metrics = arm.get("metrics", {})
    if passes:
        moves, selected = min(passes, key=lambda x: x[0])
        stage = "pass"
    else:
        moves, selected = None, candidates[0]
        if not arm.get("solver_complete"):
            stage = "terminal" if metrics.get("preplan_status") == "ok" and metrics.get("terminal_status") not in (None, "ok", "not_started") else "carrier"
        elif all(c.get("finalize_status") != "ok" for c in candidates):
            stage = "finisher"
        elif any(c.get("layout_violations") for c in candidates):
            stage = "layout"
        else:
            stage = "checker"
    return dict(stage=stage, moves=moves, variant=selected.get("variant"),
                solver_status=arm.get("status"), message=arm.get("message"),
                preplan_status=metrics.get("preplan_status"),
                terminal_status=metrics.get("terminal_status"),
                constructor_depth=metrics.get("constructor_metrics", {}).get("deepest_depth"),
                constructor_backtracks=metrics.get("constructor_metrics", {}).get("backtracks"),
                timeout_candidate_count=sum("timeout:worker" in str(c.get("message")) for c in candidates),
                stage_seconds=dict(solver=arm.get("eval_seconds", 0),
                                   carrier=metrics.get("preplan_time_s", 0),
                                   greedy=metrics.get("preplan_greedy_primary_time_s", 0),
                                   constructor=metrics.get("constructor_time_s", 0),
                                   terminal=metrics.get("terminal_time_s", 0),
                                   compaction=sum(c.get("compaction_seconds", 0) for c in candidates),
                                   finisher=sum(c.get("finisher_seconds", 0) for c in candidates),
                                   checker=sum(c.get("checker_seconds", 0) for c in candidates)))


def manifest_row(job):
    return {key: job[key] for key in ("map_id", "input", "sha256", "ship", "split", "seed", "n_loads", "family")}


def build(run_path):
    run = read(run_path / "run.json")
    assert run["split"] == "train"
    jobs = run["jobs"]
    assert all(j["split"] == "train" and "/train/" in j["input"] for j in jobs)
    rows, artifacts = [], {}
    for job in jobs:
        path = run_path / "maps" / job["map_id"] / "summary.json"
        row = {key: job[key] for key in ("map_id", "ship", "fill_target", "mass_level", "seed", "input", "n_loads")}
        row["input_sha256"] = job["input_sha256"]
        row["realised_cell_fill"] = job["realised_cell_fill"]
        row["resolved"] = path.is_file() and read(path).get("complete") is True
        row["arms"] = {}
        if row["resolved"]:
            summary = read(path)
            assert summary["job"]["input_sha256"] == job["input_sha256"]
            artifacts[str(path.relative_to(ROOT))] = sha(path)
            row["arms"] = {name: arm_outcome(arm) for name, arm in summary["arms"].items()}
            row["passed"] = any(a["stage"] == "pass" for a in row["arms"].values())
            row["moves"] = min((a["moves"] for a in row["arms"].values() if a["moves"] is not None), default=None)
        rows.append(row)
    resolved = [r for r in rows if r["resolved"]]
    failed = [r for r in resolved if not r["passed"]]
    arms = [a for r in resolved for a in r["arms"].values()]
    stages = Counter(a["stage"] for a in arms)
    ranking = []
    for stage in ("carrier", "finisher", "terminal", "checker", "layout"):
        ranking.append(dict(barrier=stage, failing_arms=stages[stage],
                            failed_maps_with_barrier=sum(any(a["stage"] == stage for a in r["arms"].values()) for r in failed),
                            failed_maps_only_barrier=sum(all(a["stage"] == stage for a in r["arms"].values()) for r in failed)))
    ranking.sort(key=lambda r: -r["failing_arms"])
    cells = []
    for fill in (40, 50, 60, 70, 80):
        for ship in ("all", "feeder_500", "feeder_800", "feeder_1700"):
            for mass in ("all", "high", "low"):
                cell = [r for r in rows if r["fill_target"] == fill and ship in ("all", r["ship"]) and mass in ("all", r["mass_level"])]
                done = [r for r in cell if r["resolved"]]
                cells.append(dict(fill=fill, ship=ship, mass=mass, planned=len(cell), resolved=len(done), pending=len(cell)-len(done),
                                  default=sum(r["arms"]["default"]["stage"] == "pass" for r in done),
                                  rule=sum(r["arms"]["rule"]["stage"] == "pass" for r in done),
                                  portfolio=sum(r["passed"] for r in done)))
    cohort_path = ROOT / "experiments/mvp/strategy/blockrule/cohort-medium.json"
    d185 = read(cohort_path)["maps"]
    assert len(d185) == 12 and all(j["split"] == "train" and "/train/" in j["input"] for j in d185)
    return dict(format="D199-scale-barrier-census-v1", updated_at=datetime.now(timezone.utc).isoformat(),
                run=str(run_path.relative_to(ROOT)), run_sha256=sha(run_path / "run.json"), summary_sha256=artifacts,
                planned_maps=len(rows), resolved_maps=len(resolved), failed_maps=len(failed), passing_maps=len(resolved)-len(failed),
                arm_stages=dict(stages), ranking=ranking, cells=cells, rows=rows,
                timeout_candidate_count=sum(a["timeout_candidate_count"] for a in arms),
                arms_with_timeout_candidate=sum(a["timeout_candidate_count"] > 0 for a in arms),
                carrier_messages=dict(Counter(a["message"] for a in arms if a["stage"] == "carrier")),
                carrier_backtracks=dict(Counter(str(a["constructor_backtracks"]) for a in arms if a["stage"] == "carrier")),
                stage_seconds_sum={key:sum(a["stage_seconds"][key] for a in arms) for key in arms[0]["stage_seconds"]},
                ladder_jobs=[dict(manifest_row(j), fill_target=j["fill_target"], mass_level=j["mass_level"]) for j in jobs if j["ship"] in ("feeder_500", "feeder_800") and j["fill_target"] in (60,70,80)],
                d185_cohort=str(cohort_path.relative_to(ROOT)), d185_cohort_sha256=sha(cohort_path), d185_jobs=[manifest_row(j) for j in d185],
                limits=["Stage attribution is first observed failure, not proof of infeasibility.",
                        "Carrier and finisher map coverage overlaps on mixed-stage maps.",
                        "Twenty-foot shortage and missing per-bay slotting cannot be causally counted from sweep telemetry; retain as candidate mechanisms within carrier failures.",
                        "All passes are stored unchanged full-checker certificates; this census runs no fresh checker or solver.",
                        "Pending outcomes are not failures; paired fills and masses share parent seeds."])


def markdown(data):
    lines = ["# D199 scale barrier ranking", "", f"Snapshot {data['updated_at']}. TRAIN only; read-only artifact census, no solver or checker calls.", "",
             f"{data['resolved_maps']}/{data['planned_maps']} maps resolved: {data['passing_maps']} portfolio passes, {data['failed_maps']} failures. {2*data['resolved_maps']} default/rule arms.", "",
             "| Barrier | Failing arms | Failed maps containing barrier | Failed maps with only barrier |", "|---|---:|---:|---:|"]
    for r in data["ranking"]:
        lines.append(f"| {r['barrier']} | {r['failing_arms']} | {r['failed_maps_with_barrier']} | {r['failed_maps_only_barrier']} |")
    lines += ["", "Fix carrier construction first. 61 of 64 carrier failures exhaust 512 constructor backtracks; 43 say no box fits an open slot, 19 report bounded exhaustion, and 2 reach a carrier deadline. Preserve compatible destinations (including twenty-foot, reefer, under-deck and long-box pools), explore different committed placements, and add discrete per-bay slotting before proposing more finisher time. The sweep does not isolate a count for each local rule or cargo pool.", "",
              f"Finisher watchdogs affect {data['arms_with_timeout_candidate']} arms and {data['timeout_candidate_count']} raw/compact candidates; one affected arm also has a passing candidate, leaving 13 arm-level finisher failures. Full-checker failures after those watchdogs are consequences of retained unfinished ballast states; they are not independent first-stage failures.", "",
              "## Current per-fill comparison", "", "Counts are passes / resolved, with planned maps separate. The order's f40 8/12 is not reproduced by committed summaries or checks: current saved certificates give 6/12.", "",
              "| Fill | Default | Rule | Better checked portfolio | Resolved / planned |", "|---|---:|---:|---:|---:|"]
    for c in data["cells"]:
        if c["ship"] == c["mass"] == "all":
            lines.append(f"| f{c['fill']} | {c['default']}/{c['resolved']} | {c['rule']}/{c['resolved']} | {c['portfolio']}/{c['resolved']} | {c['resolved']}/{c['planned']} |")
    lines += ["", "## f60 feeder500/800 baseline", "", "| Ship | Mass | Default | Rule | Portfolio | Resolved / planned |", "|---|---|---:|---:|---:|---:|"]
    for c in data["cells"]:
        if c["fill"] == 60 and c["ship"] in ("feeder_500", "feeder_800") and c["mass"] != "all":
            lines.append(f"| {c['ship']} | {c['mass']} | {c['default']}/{c['resolved']} | {c['rule']}/{c['resolved']} | {c['portfolio']}/{c['resolved']} | {c['resolved']}/{c['planned']} |")
    lines += ["", "The requested two-ship f60 ladder has 12 planned maps, of which 8 have paired baseline results (1 pass). Replicate 2 for each ship/mass is pending in the sweep; do not call these four failures or silently compare a 12-map new run against an 8-map baseline.", "",
              "## Runner bindings and evidence", "", "Use `barrier-ranking.json:ladder_jobs` for the 36 TRAIN feeder500/800 f60/f70/f80 jobs, in that rung order. Each carries map ID, input path, SHA256, ship, mass, seed and load count. Use `d185_jobs` for the exact 12-map cohort after ladder rungs; its authority is `experiments/mvp/strategy/blockrule/cohort-medium.json`, hash recorded in the JSON. All jobs have explicit TRAIN split authority.", "",
              "Baseline outcome schema is `rows[].arms.{default,rule}`: stage, moves from saved accepted full check, selected raw/compact variant, constructor depth/backtracks and timing by stage. New runs should preserve input hash, complete plan, successful finisher status, full checker report/hash, checker weighted_nonproductive moves, stage status and monotonic elapsed seconds. Never substitute constructor depth for moves. Every new claimed pass needs a fresh unchanged full checker; no repair.", "",
              "Stored baseline stage totals (seconds; raw+compact finisher/checker work both included):", "", "| Stage | Seconds |", "|---|---:|"]
    for key,value in data["stage_seconds_sum"].items():
        lines.append(f"| {key} | {value:.3f} |")
    lines += ["", "Constructor and greedy are nested within carrier; carrier and terminal are nested within solver. These totals must not be added as disjoint wall time.", "", "## Limits", ""] + ["- "+s for s in data["limits"]]
    return "\n".join(lines)+"\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=ROOT / "experiments/mvp/strategy/fillsweep/runs/main")
    args = parser.parse_args()
    data = build(args.run.resolve())
    (HERE / "barrier-ranking.json").write_text(json.dumps(data, indent=2, sort_keys=True)+"\n")
    (HERE / "barrier-ranking.md").write_text(markdown(data))
    print(json.dumps({k:data[k] for k in ("resolved_maps", "passing_maps", "arm_stages", "ranking")}))


if __name__ == "__main__":
    main()
