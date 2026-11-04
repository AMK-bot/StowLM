"""Reconcile canonical default node IDs with persisted shard and focused results."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent


def classify(report):
    node, trace = report["nodeid"], report.get("longrepr") or ""
    if "test_perf_equivalence_1028.py" in node:
        if "Pinned boundary GM shifted" in trace:
            return "OFF stale archive evidence", "Current full-reference GM differs from archived boundary scalar; corpus manifest is outside the fence."
        if "vessel parameters changed" in trace:
            return "OFF stale archive evidence", "Archived instance embeds obsolete fleet-parameter digest; full checker correctly refuses it."
    if "test_perf_canonical_policy_1028.py" in node:
        if "test_actual_saved_09" in node:
            return "OFF stale archive evidence", "Saved cold-regression input embeds obsolete fleet-parameter digest; checker refuses it."
        return "OFF stale optional projection pin", "Exact canonical metadata projection is disabled by stale reviewed construction/provider pins; public fallback independently checked bitwise."
    if "test_perf_d33_alias_integration.py" in node:
        return "OFF stale archive evidence", "Legacy D33 fixtures retain obsolete parent-parameter hashes or stored physical-number snapshots; no archive values were retagged or repinned."
    if "test_fleet_alias.py::test_archived_instances_keep_verdict_and_every_numeric_measurement" in node:
        return "OFF stale archive evidence", "Stored legacy full/realistic report snapshots differ under current parent-parameter validation, matching the D33 archived-evidence failures."
    if "test_d49_integration.py" in node and "parameter_sha256" in trace:
        return "OFF / C2-on stale evidence", "D49 fixture fleet-parameter hash is stale. MVP C2 is off under D79; no linear requalification performed."
    if "test_performance_equivalence.py" in node and "production linear policy epoch is stale" in trace:
        return "OFF / C2-on stale qualification", "Test requires qualified C2 global screens; both reference/current screens correctly refuse the stale linear epoch. MVP C2 is off (D79)."
    if "test_w2_in_port.py::test_port_action_helper" in node:
        return "ON-only", "Fixture explicitly forces rule_freeze_v1_enabled=True; synthetic string action lacks box/slot attributes needed by the activated port-screen helper. OFF production action path is not exercised."
    if "test_perf_slots_1028.py" in node:
        return "OFF cache integration (fix pending retest)", "Known convention string LRUs bypassed scoped/reference isolation and live-provider invalidation. Checker-only raw delegation fix and concurrency regressions await a fresh-process targeted rerun."
    if "tests/test_ownership.py" in node:
        return "OFF ownership/documentation migration", "Ownership map lacks v4 task IDs/new source families; the matching docs/architecture.md table is outside the fence, so both remain unchanged."
    if "test_fleet_manual_restore.py::test_actual_standard_manual_condition_passes_complete_voyage_checker[feeder_500-C2]" in node:
        return "OFF synthetic manual witness invalid", "Fresh-process confirmed: C2 is the manual condition label; full checker rejects draught 7.0612144104520675 m > 7.05 m (iscode.loading.draft, port.call.draft). Vessel recipe is outside the changed-module fence."
    if "test_fleet_manual_restore.py::test_actual_standard_manual_condition_passes_complete_voyage_checker[feeder_1700-C2]" in node:
        return "OFF synthetic manual witness invalid", "Fresh-process confirmed: C2 is the manual condition label; first hard rule is iscode.a2_2.initial_gm (GM -1.3419069784503033 m < 0.15 m), with weather failures. Limits and recipe unchanged."
    if "tests/yard/test_contract_workload.py::test_switch_off_preserves_seeded_workload_bytes" in node:
        return "OFF upstream workload byte drift (field cause unverified)", "Current and retained pre-D72 simulator produce identical complete payloads with current dependencies for both seeds; old pinned hashes differ. Exact changed model/parameter/cargo field is unverified without old payload. See v54-yard-byte-comparison.json; no expected hashes updated."
    if "tests/generate/test_random_yard.py::test_rungs_roundtrip" in node:
        return "OFF test harness fixture omission (retest pending)", "Original explicit-file shard omitted nested geometry-only diagnostic fixture; rerun with full-tree collection before attributing to production."
    return "unclassified", "Inspect persisted raw report before deciding whether this affects the MVP OFF path."


def main():
    expected = set(json.loads((OUT / "v54-fast-default-selection.json").read_text()))
    reports = {}
    extras = set()
    logs = sorted(OUT.glob("v54-fast-*-reports.jsonl"), key=lambda path: path.stat().st_mtime)
    for path in logs:
        for line in path.read_text().splitlines():
            report = json.loads(line)
            node = report["nodeid"]
            if node not in expected:
                extras.add(node)
                continue
            if report["when"] == "call" or (report["when"] == "setup" and report["outcome"] != "passed"):
                reports[node] = {**report, "artifact": path.name}
    # This focused rerun repairs the initial activation-read fixture assertion.
    focused = OUT / "v54-fast-batch-retest.xml"
    if focused.exists():
        for case in ET.parse(focused).getroot().iter("testcase"):
            node = case.attrib["classname"].replace(".", "/") + ".py::" + case.attrib["name"]
            if node in expected:
                failure = case.find("failure")
                reports[node] = {"nodeid": node, "when": "call", "duration": float(case.attrib.get("time", 0)),
                    "outcome": "failed" if failure is not None else "passed",
                    "longrepr": failure.text if failure is not None else None, "artifact": focused.name}
    counts = Counter(report["outcome"] for report in reports.values())
    collection_skips = {}
    for path in OUT.glob("v54-fast-*.xml"):
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            continue
        for case in tree.getroot().iter("testcase"):
            skipped = case.find("skipped")
            if skipped is not None and skipped.attrib.get("message") == "collection skipped":
                collection_skips[case.attrib["name"]] = dict(artifact=path.name, reason=skipped.text)
    missing = sorted(expected - reports.keys())
    failures = []
    for report in reports.values():
        if report["outcome"] == "failed":
            classification, reason = classify(report)
            failures.append(dict(nodeid=report["nodeid"], classification=classification, reason=reason,
                                 trace_artifact=report["artifact"]))
    starts = []
    for path in OUT.glob("v54-fast-*-command.json"):
        starts.append(json.loads(path.read_text())["started"])
    wall_s = max(path.stat().st_mtime for path in logs) - min(starts) if starts and logs else None
    pause = OUT / "v54-fast-pause.json"
    queued = pause.exists() and json.loads(pause.read_text()).get("workers_running") == 0
    summary = {"status": ("queued_capacity" if queued else "running") if missing else "failed" if failures else "passed",
        "active_process_count": 0 if queued else (1 if missing else 0),
        "command": "UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/mvp/smoke/run_fast_v54.py (plus checkpoint resume / final delta documented in v54-fast-report.md)",
        "wall_s": wall_s, "wall_definition": "First pytest subprocess start to latest persisted test report",
        "root_selection_snapshot": "v54-fast-default-selection.json",
        "root_selection_collected_utc": datetime.fromtimestamp((OUT / "v54-fast-default-selection.json").stat().st_mtime, timezone.utc).isoformat(),
        "source_coverage_note": "Exact node-ID coverage across checkpointed processes. This is not a coherent frozen-tree rerun: other executives changed 4.7 source concurrently. Their module gates and the settled 5.4b smoke remain separate requirements.",
        "default_marker_expression": "not slow and not gpu and not net",
        "expected_selected": len(expected), "completed_selected": len(reports),
        "counts": dict(counts), "missing_count": len(missing), "complete": not missing,
        "collection_skipped": collection_skips,
        "counts_including_collection_skips": {**dict(counts), "skipped": counts.get("skipped", 0) + len(collection_skips)},
        "counts_note": "Skipped includes xfail; the initial shard telemetry did not distinguish xfail/xpass. Focused complete-run JUnit is retained where available.",
        "extra_noncanonical_count": len(extras), "failure_classification_counts": dict(Counter(row["classification"] for row in failures)), "failures": failures,
        "on_only_failures": [row for row in failures if row["classification"] == "ON-only"],
        "missing": missing, "artifacts": [path.name for path in logs],
        "note": "Initial file shards lose 26 bounds parametrizations; directory rerun supplies all 52 canonical bounds cases. Counts use exact default node IDs, excluding unparametrized extras. Tree changes are measured by the latest saved root collection."}
    (OUT / "fast-suite-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key not in ("failures", "missing", "artifacts")}, indent=2))
    for failure in summary["failures"]:
        print(failure["nodeid"], failure["classification"], failure["reason"])


if __name__ == "__main__":
    main()
