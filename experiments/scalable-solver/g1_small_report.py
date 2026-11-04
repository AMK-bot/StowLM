"""Report the four-worker small extension without confusing its source cohorts.

Only frozen selection metadata and completed result checkpoints are read.
Uncheckpointed attempts have unknown cost and outcomes. The shared G1 aggregate
owns admission and throughput semantics; the configured full-ship trial stays
outside both extension and combined default results.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def load_aggregate():
    spec = importlib.util.spec_from_file_location("g1_extension_report_shared", ROOT / "g1_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extended_aggregate(module, rows):
    result = module.aggregate(rows)
    admitted = [row for row in rows if row.get("admitted")]
    result.update(
        checker_failed=sum("checker_violations" in row.get("measurement", {}) and
                           not row["measurement"].get("checker_passed") for row in admitted),
        actions_masked_cases=sum(row.get("measurement", {}).get("replay", {}).get("reason") ==
                                "action_masked" for row in admitted),
        split_counts=dict(Counter(row["selection"]["shard"]["split"] for row in admitted)),
        returned_candidate_artifacts=sum(len(row.get("returned_candidates", [])) for row in admitted))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--final", action="store_true", help="Request final closure; incomplete workers remain explicitly provisional")
    parser.add_argument("--end-utc", help="Optional leader-observed finish timestamp, not inferred from checkpoint mtimes")
    args = parser.parse_args()
    module = load_aggregate()
    roster_path = ROOT / "g1-small-roster.json"
    roster = read(roster_path)
    eligible = {entry["shard"]["sha256"] for entry in roster["entries"]}
    rows, workers, directories = [], [], []
    for index in range(4):
        directory = ROOT / "g1-small-extend" / f"worker-{index}"
        snapshot_path = directory / "selected-manifest.json"
        if not snapshot_path.exists():
            workers.append({"worker": index, "status": "snapshot missing", "complete": False})
            continue
        directories.append(directory)
        snapshot = read(snapshot_path)
        worker_rows = []
        for path in sorted((directory / "results").glob("*.json")):
            row = read(path)
            entry = row["selection"]
            if entry["shard"].get("split") not in {"train", "val"} or entry["rung"] != "small":
                raise ValueError("Refusing non-small or non-train/val extension checkpoint")
            if entry["shard"]["sha256"] not in eligible:
                raise ValueError("Checkpoint input absent from frozen extension roster")
            row["result_path"] = str(path.relative_to(ROOT))
            worker_rows.append(row)
            rows.append(row)
        selected = snapshot.get("selections", [])
        complete = len(worker_rows) == len(selected) and all(
            row.get("admitted") and "measurement" in row or
            not row.get("admitted") and row.get("status", "").startswith("not_admitted")
            for row in worker_rows)
        workers.append({"worker": index, "identity": snapshot.get("identity"),
                        "selected": len(selected), "checkpointed": len(worker_rows),
                        "complete": complete, "uncheckpointed": len(selected)-len(worker_rows),
                        "summary": extended_aggregate(module, worker_rows)})
    attempted = {row["selection"]["shard"]["sha256"] for row in rows if row.get("admitted")}
    if len(attempted) != sum(bool(row.get("admitted")) for row in rows):
        raise ValueError("Duplicate admitted input SHA across disjoint workers")
    complete = len(workers) == 4 and all(worker["complete"] for worker in workers)
    remaining_entries = [entry for entry in roster["entries"]
                         if entry["shard"]["sha256"] not in attempted]
    subset_path = ROOT / "g1-small-unattempted-roster.json"
    subset = {**roster, "created_utc": datetime.now(timezone.utc).isoformat(),
              "entries": remaining_entries,
              "availability": {"total_entries": len(remaining_entries),
                               "by_source": dict(Counter(entry.get("source_label", "unknown")
                                                         for entry in remaining_entries)),
                               "by_split": dict(Counter(entry["shard"]["split"] for entry in remaining_entries))},
              "resumption": {"parent_roster": str(roster_path),
                             "parent_roster_sha256": hashlib.sha256(roster_path.read_bytes()).hexdigest(),
                             "excluded_all_admitted_sha256": sorted(attempted),
                             "excluded_count": len(attempted),
                             "preserved_original_priority": True,
                             "current_workers_fully_checkpointed": complete,
                             "qualification": "Excludes every admitted result, including failures and source changes; uncheckpointed in-flight admissions are unknown. Regenerate after workers stop before any future authorized run."}}
    subset["notes"] = [*roster.get("notes", []),
                       "Resume in a NEW output directory with a newly authorized stop UTC; previous worker identities pin deadline and non-admission checkpoints."]
    subset_path.write_text(json.dumps(subset, indent=2, sort_keys=True)+"\n")
    partition_sets = [{entry["shard"]["sha256"] for entry in remaining_entries[index::4]}
                      for index in range(4)]
    if (sum(map(len, partition_sets)) != len(set().union(*partition_sets)) or
            len(set().union(*partition_sets)) != len(remaining_entries) or
            set().union(*partition_sets) & attempted):
        raise ValueError("Unattempted roster partition is not disjoint/exhaustive")
    finish = datetime.fromisoformat(args.end_utc.replace("Z", "+00:00")) if args.end_utc else None
    if finish is not None and finish.tzinfo is None:
        parser.error("--end-utc must include a timezone")
    # Preserve the pilot closure exactly once before rebuilding the combined
    # primary table. Repeated invocations never overwrite this historical copy.
    archive = ROOT / "G1-README-pilot.md"
    current_readme = ROOT / "G1-README.md"
    if not archive.exists() and current_readme.exists():
        with archive.open("x") as stream:
            stream.write(current_readme.read_text())
    command = [sys.executable, str(ROOT / "g1_report.py")]
    for directory in directories:
        command.extend(["--extra-dir", str(directory)])
    subprocess.run(command, check=True, capture_output=True, text=True)
    combined_report = read(ROOT / "g1-report.json")
    overall = extended_aggregate(module, rows)
    by_source = {label: extended_aggregate(module, [row for row in rows
                 if row["selection"].get("source_label", "unknown") == label])
                 for label in sorted({row["selection"].get("source_label", "unknown") for row in rows} |
                                     {entry.get("source_label", "unknown") for entry in roster["entries"]})}
    report = {
        "schema": "scalable-g1-small-extension-report@1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "final_requested": args.final, "final": complete,
        "status": "all worker selections checkpointed" if complete else "provisional; active/uncheckpointed work unknown",
        "leader_observed_end_utc": finish.isoformat() if complete and finish else None,
        "closure_warning": "Final requested while checkpoints incomplete" if args.final and not complete else None,
        "stop_admission_utc": "2026-10-27T23:10:00Z", "report_boundary_utc": "2026-10-27T23:15:00Z",
        "roster": str(roster_path.relative_to(ROOT)), "roster_eligible": len(eligible),
        "unique_attempted_inputs": len(attempted), "remaining_eligible_not_attempted": len(eligible-attempted),
        "remaining_qualification": "Includes uncheckpointed running cases until their admitted result is recorded",
        "unattempted_subset_roster": str(subset_path.relative_to(ROOT)),
        "subset_excludes_every_admitted_result": True,
        "verification": {"prior_protocol_tests": 14,
                         "prior_protocol_qualification": "Previously reported protocol coverage; not rerun while four numerical workers are active",
                         "protocol_command": "UV_CACHE_DIR=$PWD/.uv-cache uv run pytest -q tests/solver/test_scalable_g1_pilot.py",
                         "compile_command": "UV_CACHE_DIR=$PWD/.uv-cache uv run python -m compileall -q experiments/scalable-solver/g1_small_report.py",
                         "partition_smoke": {"passed": True, "partition_sizes": list(map(len, partition_sets)),
                                             "disjoint_exhaustive": True, "admitted_sha_retained": 0,
                                             "unchanged_source_priority": True}},
        "extension_only": overall, "by_actual_source_label": by_source,
        "stable_source_extension": extended_aggregate(module, [row for row in rows if not row.get("source_changed_during_run")]),
        "source_changing_exploratory": extended_aggregate(module, [row for row in rows if row.get("source_changed_during_run")]),
        "combined_default_small": combined_report.get("per_rung", {}).get("small"),
        "workers": workers, "result_paths": [row["result_path"] for row in rows],
        "prior_pilot_closure": "G1-README-pilot.md", "prior_pilot_compute": "g1-compute.json",
        "separate_configured_full_trial": "g1-report.json:configured_trial; excluded from extension and combined default counts",
        "qualification": "Default solver 2.6.3, production C2 on; provisional v3 sources, no test input reads. Base-mask VAL and canonical train sources are shown separately before any combined figure.",
        "budget_caveat": "Old pilot small had 300s validation/replay reserve; extension has 60s reserve. Both use 120s default search; this affects combined pipeline throughput.",
    }
    (ROOT / "g1-small-extension-report.json").write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    fmt = module.fmt
    lines = ["", "## Four-worker small extension", "", report["qualification"], "",
             f"{report['status']}. Of **{len(eligible)}** unique eligible roster inputs, **{len(attempted)}** have recorded admitted attempts; **{len(eligible-attempted)}** remain unattempted in the checkpoints. In-flight work remains provisional.", "",
             "| Source / scope | Attempts | Complete | Checker tried/pass | Membership/departure | G1 approved | Plans/core-hour | Approved cost | Approved gap |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    combined = report["combined_default_small"] or {}
    sections = [*by_source.items(), ("Extension only", overall)]
    if combined:
        sections.append(("Combined default small", combined["all_recorded"]))
    for label, value in sections:
        lines.append(f"| {label} | {value['attempts']} | {value['completed']} | {value['checker_attempted']}/{value['checker_passed']} | {value['mask_membership_passed']}/{value['mask_passed']} | {value['joint_approved']} | {fmt(value['approved_plans_per_core_hour'])} | {fmt(value['mean_approved_objective'])} | {fmt(value['mean_approved_gap_to_lb'])} |")
    lines += ["", report["budget_caveat"], "",
              "All failed recorded pipelines remain in throughput denominators. Source-changing rows remain exploratory and never become approved gold. Action-membership pass can describe an incomplete prefix; only complete, finalized, checker-passing departures with full mask replay count as G1 approved.", "",
              f"Extension checker failures: **{overall['checker_failed']}**; action-masked cases: **{overall['actions_masked_cases']}**; source-changing attempts: **{overall['source_changing_attempts']}**. Partial prefixes without a checker attempt are not checker failures.", "",
              "The closed original pilot is preserved in [G1-README-pilot.md](G1-README-pilot.md), with its separate compute record [g1-compute.json](g1-compute.json). Extension and combined statistics are in [g1-small-extension-report.json](g1-small-extension-report.json). The configured full-ship trial remains a separate section and is never pooled.", "",
              "Stop admission **23:10 UTC** and report by **23:15 UTC**, 27 October 2026. Final closure requires all four workers to checkpoint every selected row, including deadline non-admissions.", "",
              "Regenerate after workers finish:", "", "```sh", "UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/scalable-solver/g1_small_report.py --final", "```", ""]
    lines += ["", "### Resume unattempted inputs only after new authorization", "",
              f"[g1-small-unattempted-roster.json](g1-small-unattempted-roster.json) retains **{len(remaining_entries)}** entries in original source-priority order. It excludes **every admitted result**, including failures, rather than only approved plans. The original roster and running worker identities are unchanged. Regenerate this subset after workers stop: active admissions without checkpoints are currently unknown.", "",
              "Future authorized run only: reserve compute again, supply a newly authorized stop timestamp, and choose a new, nonexistent output directory. Existing worker paths pin their old deadline and not-admitted checkpoints and cannot simply resume under changed settings. These commands are documentation and are not launched by reporting.", "",
              "```sh",
              "g1_resume_stop_utc='YYYY-MM-DDTHH:MM:SSZ'  # Replace with newly authorized UTC",
              "g1_resume_output='experiments/scalable-solver/g1-small-resume-NEW_RUN'",
              "test ! -e \"$g1_resume_output\" || exit 1",
              "mkdir -p \"$g1_resume_output\"",
              "for g1_resume_worker in 0 1 2 3; do",
              "  UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/scalable-solver/g1_small_extend.py --roster experiments/scalable-solver/g1-small-unattempted-roster.json --output \"$g1_resume_output\" --workers 4 --worker-index \"$g1_resume_worker\" --stop-admission-utc \"$g1_resume_stop_utc\" > \"$g1_resume_output/worker-$g1_resume_worker.log\" 2>&1 &",
              "done", "wait", "```", "",
              "Verification: 14 protocol tests were previously reported. No numerical tests are rerun while four workers are active. Metadata-only compilation and a disjoint/exhaustive four-way subset partition smoke check cover this report change. After compute is released, rerun the protocol command:", "",
              "```sh", report["verification"]["protocol_command"],
              report["verification"]["compile_command"],
              "for g1_smoke_worker in 0 1 2 3; do",
              "  UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/scalable-solver/g1_small_extend.py --roster experiments/scalable-solver/g1-small-unattempted-roster.json --output experiments/scalable-solver/g1-small-resume-SMOKE --workers 4 --worker-index \"$g1_smoke_worker\" --list-only > /dev/null",
              "done", "```", ""]
    with current_readme.open("a") as stream:
        stream.write("\n".join(lines))
    print(json.dumps({"final": complete, "extension_attempts": overall["attempts"],
                      "extension_approved": overall["joint_approved"],
                      "combined_small_approved": combined.get("all_recorded", {}).get("joint_approved"),
                      "remaining": len(eligible-attempted)}))


if __name__ == "__main__":
    main()
