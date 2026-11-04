"""Aggregate frozen G1 pilot checkpoints; do not read instance contents.

Each result contributes pipeline seconds once, including failed attempts.
Mixed-source observations retain raw gate results but never qualify as gold.
Uncheckpointed work is unknown rather than guessed from process or file times.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def production_approved(row):
    value = row.get("measurement", {})
    return bool(row.get("admitted") and row.get("source_changed_during_run") is False
                and row.get("identity", {}).get("diagnostic_c2_off") is False
                and value.get("approved") and value.get("completed")
                and value.get("checker_passed") and value.get("finalized_status") == "ok"
                and value.get("replay", {}).get("status") == "passed")


def aggregate(rows):
    admitted = [row for row in rows if row.get("admitted")]
    measurements = [row.get("measurement", {}) for row in admitted]
    approved = [row for row in admitted if production_approved(row)]
    searches = [value["timings_s"]["search"] for value in measurements
                if "search" in value.get("timings_s", {})]
    total = sum(row.get("measurement", {}).get("timings_s", {}).get("pipeline", 0)
                * row.get("identity", {}).get("cores", 1) for row in admitted)
    costs = [row["measurement"]["objective"] for row in approved
             if row["measurement"].get("objective") is not None]
    gaps = [row["measurement"]["gap_to_lb"] for row in approved
            if row["measurement"].get("gap_valid") and row["measurement"].get("gap_to_lb") is not None]
    checked_gaps = [value["gap_to_lb"] for value in measurements
                    if value.get("gap_valid") and value.get("gap_to_lb") is not None]
    failures = {key: Counter() for key in ("solver", "checker", "mask")}
    for value in measurements:
        metrics = value.get("solver_metrics", {})
        solver_ids = set(metrics.get("dead_end_rule_ids", {}))
        solver_ids.update(metrics.get("oracle_dead_end", {}).get("rule_ids", []))
        solver_ids.update(metrics.get("required_data_rule_ids", []))
        failures["solver"].update(solver_ids)
        failures["checker"].update({v["rule_id"] for v in value.get("checker_violations", [])
                                     if v.get("severity") == "hard"})
        failures["mask"].update(set((value.get("replay", {}).get("dead_end") or {}).get("rule_ids", [])))
    return {
        "recorded": len(rows), "attempts": len(admitted),
        "not_admitted": len(rows)-len(admitted),
        "plans_returned": sum(bool(v.get("plan_returned")) for v in measurements),
        "completed": sum(bool(v.get("completed")) for v in measurements),
        "checker_attempted": sum("checker_violations" in v or "finalized_status" in v
                                 for v in measurements),
        "checker_passed": sum(bool(v.get("checker_passed")) for v in measurements),
        "mask_membership_passed": sum(v.get("replay", {}).get("status") == "passed" or
                                      v.get("replay", {}).get("reason") == "incomplete_departure"
                                      for v in measurements),
        "mask_passed": sum(v.get("replay", {}).get("status") == "passed" for v in measurements),
        "raw_joint_gate_passed": sum(bool(v.get("raw_joint_gate_passed", v.get("joint_gate_passed"))) for v in measurements),
        "joint_approved": len(approved),
        "distinct_approved_inputs": len({row.get("input_sha256") for row in approved}),
        "source_changing_attempts": sum(bool(row.get("source_changed_during_run")) for row in admitted),
        "mean_solve_s": mean(searches) if searches else None,
        "mean_approved_objective": mean(costs) if costs else None,
        "mean_approved_gap_to_lb": mean(gaps) if gaps else None,
        "mean_raw_checker_qualified_gap_to_lb": mean(checked_gaps) if checked_gaps else None,
        "pipeline_core_seconds": total,
        "approved_plans_per_core_hour": 3600*len(approved)/total if total else None,
        "mask_status_counts": dict(Counter(v.get("replay", {}).get("status", "no_plan") for v in measurements)),
        "status_counts": dict(Counter(row.get("measurement", {}).get("status", row.get("status", "unknown")) for row in rows)),
        "failure_rule_case_counts": {key: dict(value) for key, value in failures.items()},
        "input_load_s_excluded": sum(row.get("input_load_s", 0) for row in admitted),
        "throughput_denominator": "All recorded admitted one-core pipelines including failures and source-changing work; excludes warm/input loading; uncheckpointed work unavailable",
        "mask_count_qualification": "Membership pass means every returned action passed legal_index membership/decode/push; incomplete_departure can pass membership while failing full departure replay (mask_passed). Neither alone grants joint approval.",
        "checker_attempt_qualification": "Inferred from checker_violations or finalized_status fields; partial prefixes without these fields were not finalized/checked, not checker failures.",
    }


def fmt(value, places=2):
    return "—" if value is None else f"{value:.{places}f}"


def configured_trial(default_rows):
    """Keep the named search-configuration experiment outside default counts."""
    directory = ROOT / "g1-v3-full-configured"
    snapshot = directory / "selected-manifest.json"
    if not snapshot.exists():
        return {"available": False, "excluded_from_default_cohort": True}
    manifest = read(snapshot)
    trial_rows = []
    for path in sorted((directory / "results").glob("*.json")):
        row = read(path)
        if row.get("selection", {}).get("shard", {}).get("split") not in {"train", "val"}:
            raise ValueError("Configured trial must use frozen train/val metadata")
        row["result_path"] = str(path.relative_to(ROOT))
        trial_rows.append(row)
    comparisons = []
    for selection in manifest.get("selections", []):
        checksum = selection["shard"]["sha256"]
        baseline = [row for row in default_rows
                    if row["selection"]["shard"]["sha256"] == checksum]
        configured = [row for row in trial_rows
                      if row["selection"]["shard"]["sha256"] == checksum]
        comparison = {"input_sha256": checksum, "selection": selection,
                      "baseline_result_paths": [row["result_path"] for row in baseline],
                      "configured_result_paths": [row["result_path"] for row in configured],
                      "status": "both checkpointed" if baseline and configured else "pending counterpart checkpoint",
                      "baseline": aggregate(baseline), "configured": aggregate(configured)}
        if baseline and configured:
            left, right = baseline[0], configured[0]
            comparison["source_identity_equal"] = (left["identity"]["source_sha256"] ==
                                                     right["identity"]["source_sha256"])
            comparison["pipeline_core_seconds_delta_configured_minus_baseline"] = (
                comparison["configured"]["pipeline_core_seconds"]-
                comparison["baseline"]["pipeline_core_seconds"])
            comparison["loaded_boxes"] = {}
            for label, row in (("baseline", left), ("configured", right)):
                measurement = row.get("measurement", {})
                loaded = (row["boxes"]-measurement["shut_out"]
                          if "boxes" in row and "shut_out" in measurement else
                          measurement.get("solver_metrics", {}).get("partial_loaded"))
                comparison["loaded_boxes"][label] = loaded
        comparisons.append(comparison)
    return {"available": True, "directory": directory.name,
            "excluded_from_default_cohort": True,
            "qualification": "Separate configured search experiment, production C2 unchanged; not pooled with default counts or throughput",
            "identity": manifest.get("identity"), "availability": manifest.get("availability"),
            "planned_cases": len(manifest.get("selections", [])),
            "checkpointed_cases": len(trial_rows), "summary": aggregate(trial_rows),
            "records": trial_rows, "same_input_comparisons": comparisons,
            "status": "checkpoints complete" if len(trial_rows) == len(manifest.get("selections", [])) else "provisional: pending/uncheckpointed work unknown"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extra-dir", type=Path, action="append", default=[],
                        help="Additional frozen G1 pilot output directory (e.g. later tiny shard)")
    args = parser.parse_args()
    directories = [ROOT / "g1-v3-a", ROOT / "g1-v3-b", *args.extra_dir]
    rows, cohorts, seen = [], [], set()
    for directory in directories:
        directory = directory.resolve()
        if not directory.is_relative_to(ROOT):
            parser.error("extra directories must remain inside scalable-solver experiments")
        if directory == (ROOT / "g1-v3-full-configured").resolve():
            parser.error("configured trial is reported separately automatically; do not pool it with --extra-dir")
        snapshot = directory / "selected-manifest.json"
        if not snapshot.exists():
            cohorts.append({"directory": str(directory), "status": "snapshot not available"})
            continue
        manifest = read(snapshot)
        cohort_rows = []
        for path in sorted((directory / "results").glob("*.json")):
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            row = read(path)
            if row.get("selection", {}).get("shard", {}).get("split") not in {"train", "val"}:
                raise ValueError(f"Refusing non-train/val result metadata: {path}")
            row["result_path"] = str(path.relative_to(ROOT))
            rows.append(row)
            cohort_rows.append(row)
        cohorts.append({"directory": str(directory.relative_to(ROOT)),
                        "identity": manifest.get("identity"),
                        "availability": manifest.get("availability"),
                        "planned_cases": len(manifest.get("selections", [])),
                        "checkpointed_cases": len(cohort_rows),
                        "uncheckpointed_cases": len(manifest.get("selections", []))-len(cohort_rows),
                        "status": "checkpoints complete" if len(cohort_rows) == len(manifest.get("selections", [])) else "pending/uncheckpointed work unknown"})
    rungs = sorted({r["selection"]["rung"] for r in rows} |
                   {rung for c in cohorts for rung in (c.get("availability") or {})})
    per_rung = {}
    for rung in rungs:
        selected = [row for row in rows if row["selection"]["rung"] == rung]
        changed = [row for row in selected if row.get("source_changed_during_run")]
        stable = [row for row in selected if not row.get("source_changed_during_run")]
        per_rung[rung] = dict(all_recorded=aggregate(selected),
                             stable_source=aggregate(stable), source_changing_exploratory=aggregate(changed))
    warm_path = ROOT / "g1-cache-warm-status.json"
    inventory_path = ROOT / "g1_inventory.json"
    inventory = read(inventory_path) if inventory_path.exists() else {}
    report = {
        "schema": "scalable-g1-report@1", "generated_utc": datetime.now(timezone.utc).isoformat(),
        "qualification": "Provisional instances-v3 pilot; canonical manifest incomplete/unreleased. Default production C2 on. Only unchanged-source joint checker/mask passes qualify as gold.",
        "boundaries": {"stop_admission_utc": "2026-10-27T23:10:00Z", "report_utc": "2026-10-27T23:15:00Z"},
        "cache_warm": read(warm_path) if warm_path.exists() else None,
        "inventory_snapshot": {"path": str(inventory_path.relative_to(ROOT)),
                               "generated_utc": inventory.get("generated_utc"),
                               "read_audit": inventory.get("read_audit"),
                               "note": "Earlier 63 train/val-path snapshot; running campaign availability may increase. Frozen cohort selections govern this pilot."},
        "cohorts": cohorts, "per_rung": per_rung, "overall": aggregate(rows),
        "configured_trial": configured_trial(rows),
        "source_changing_rows": [row for row in rows if row.get("source_changed_during_run")],
        "result_paths": [row["result_path"] for row in rows],
        "test_instance_contents_read_by_aggregator": 0,
        "caveats": ["Only result checkpoints and frozen selection metadata are read; no test inputs or medium are opened.",
                    "Raw checker/mask pass counts include exploratory mixed-source observations and are separated above; their approved count is zero.",
                    "Gap/objective production averages use only approved plans; raw checker-qualified gaps are separate. No-return and failure rows remain in throughput time.",
                    "An uncheckpointed running/interrupted case has unavailable elapsed work; reported throughput is provisional until jobs stop and checkpoints exist."]}
    (ROOT / "g1-report.json").write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    lines = ["# G1 production-mask pilot", "", report["qualification"], "",
             f"Report snapshot: {report['generated_utc']}. No new case may be admitted unless its search plus reserved overhead fits before **23:10 UTC**; reporting boundary **23:15 UTC**, 27 October 2026.", "",
             "The earlier inventory read 63 unique train/val inputs. It had no tiny shards; medium is excluded. The campaign is growing, so the frozen selected manifests below determine the actual pilot inputs. No test instance contents are read.", "",
             "Cache warm-up completed with exit 0: **103.493 seconds**, versus **240 seconds estimated**, one worker. Its status, log and summary are frozen in `g1-cache-warm*`. No cache warm-up is performed by this report script.", "",
             "A plan is approved only after ballast finalization, the full checker, and independent action-by-action membership/replay through the default public index mask. Source changes during a case quarantine it as exploratory; raw gate observations remain visible, and production approval remains zero for that case.", "",
             "| Rung | Attempts | Returned | Complete | Checker tried | Checker pass | Mask membership | Departure replay | Raw joint | Approved | Mean solve s | Approved gap | Plans/core-hour |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for rung, grouped in per_rung.items():
        v = grouped["all_recorded"]
        lines.append(f"| {rung} | {v['attempts']} | {v['plans_returned']} | {v['completed']} | {v['checker_attempted']} | {v['checker_passed']} | {v['mask_membership_passed']} | {v['mask_passed']} | {v['raw_joint_gate_passed']} | {v['joint_approved']} | {fmt(v['mean_solve_s'])} | {fmt(v['mean_approved_gap_to_lb'])} | {fmt(v['approved_plans_per_core_hour'])} |")
    lines += ["", "Mask membership counts returned sequences whose every action passed public legal_index membership, decoding and push. An incomplete departure can pass this action-membership criterion while failing complete-departure replay; both counts are shown. Joint approval still requires a completed plan, finalization, checker pass and complete replay. Checker tried is inferred from checker_violations or finalized_status fields; partial prefixes without these fields were not finalized/checked and are not checker failures.", "", "Throughput includes all recorded admitted pipeline seconds, including failures and mixed-source cases. Cache warming and input loading are reported separately. Uncheckpointed work is unknown, so this table remains provisional while cases are running. Objective and gap averages describe approved plans only; the JSON retains separate raw checker-qualified gaps and stable/exploratory tables.", "", "Frozen selections:", ""]
    for cohort in cohorts:
        lines.append(f"- `{cohort['directory']}`: {cohort.get('checkpointed_cases', 0)}/{cohort.get('planned_cases', 0)} checkpointed; availability `{json.dumps(cohort.get('availability', {}), sort_keys=True)}`.")
    trial = report["configured_trial"]
    if trial["available"]:
        lines += ["", "## Separate configured full-ship trial", "", trial["qualification"], "",
                  f"`{trial['directory']}`: {trial['checkpointed_cases']}/{trial['planned_cases']} checkpointed; {trial['status']}.", "",
                  f"Solver `{trial['identity']['solver_version']}`; search overrides `max_restarts=0`, `beam_width=1`, `early_beam_width=1`, `branch_factor=2`. Production C2 remains on. All frozen identities and complete records are retained in `g1-report.json`.", "",
                  "| Same-input variant | Attempts | Complete | Checker tried/pass | Membership/departure | Approved | Search s | Pipeline core-s |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for comparison in trial["same_input_comparisons"]:
            for variant in ("baseline", "configured"):
                value = comparison[variant]
                lines.append(f"| {variant} | {value['attempts']} | {value['completed']} | {value['checker_attempted']}/{value['checker_passed']} | {value['mask_membership_passed']}/{value['mask_passed']} | {value['joint_approved']} | {fmt(value['mean_solve_s'])} | {fmt(value['pipeline_core_seconds'])} |")
            lines += ["", f"Input SHA `{comparison['input_sha256']}`: {comparison['status']}; loaded boxes `{json.dumps(comparison.get('loaded_boxes', {}), sort_keys=True)}`. Configured failure IDs: `{json.dumps(comparison['configured']['failure_rule_case_counts'], sort_keys=True)}`.", ""]
    lines += ["", "Failure attribution (number of recorded cases mentioning each rule, not a proof of infeasibility):", "", "```json", json.dumps(report["overall"]["failure_rule_case_counts"], indent=2, sort_keys=True), "```", "", "Rebuild after checkpoints finish:", "", "```sh", "UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/scalable-solver/g1_report.py", "# Add a later frozen tiny cohort with --extra-dir experiments/scalable-solver/<directory>", "```", ""]
    (ROOT / "G1-README.md").write_text("\n".join(lines))
    print(json.dumps({"checkpointed_cases": len(rows), "approved": report["overall"]["joint_approved"],
                      "pending_cases": sum(c.get("uncheckpointed_cases", 0) for c in cohorts),
                      "output": "g1-report.json and G1-README.md"}))


if __name__ == "__main__":
    main()
