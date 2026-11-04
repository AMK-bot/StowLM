"""Checkpointed three-core MVP suite; launch only after the smoke/admission GO.

Every subprocess collects from the repository root before exact node filtering,
so nested fixtures retain their normal semantics. Historical-evidence markers
are explicit, reviewed exclusions; they never become passing results.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from run_fast_v54 import ENV, OUT, ROOT, POOL_FILES
from summarize_fast_v54 import classify

PREFIX = "v54b-suite"
MARKERS = "not slow and not gpu and not net and not archive and not requires_linear_requalification"
# These files contain real or synthetic explicit child-process tests. Run them
# as one isolated pytest before three serial shards; never overlap pool tests.
ISOLATED = POOL_FILES | {
    "tests/test_provenance.py", "tests/solver/test_exact_deadline.py",
    "tests/solver/test_exact_securing_1028.py", "tests/solver/test_exact_index.py",
    "tests/eval/test_harness.py", "tests/eval/test_harness_postflight.py",
}
ENV = {key: value for key, value in ENV.items() if not key.startswith("V54_")}
ENV.update(PYTHONUNBUFFERED="1")


def write_json(path, value):
    target = OUT / path
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(target)


def source_snapshot():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in ("src", "tests") for path in sorted((ROOT / folder).rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts}


def command(name, *, collect=False):
    args = [sys.executable, "-m", "pytest", "-p", "v54_fast_plugin",
            "-p", "v54_mvp_triage_plugin", "--mvp-triage", "-m", MARKERS,
            f"--mvp-triage-report={OUT / (name + '-triage.json')}"]
    if collect:
        args += ["--collect-only", "-q"]
    else:
        args += ["--tb=short", "--durations=30", f"--junitxml={OUT / (name + '.xml')}"]
    return args


def load_outcomes():
    outcomes, collection_skips = {}, {}
    artifacts = sorted(OUT.glob(PREFIX + "-*-reports.jsonl"), key=lambda p: p.stat().st_mtime)
    for path in artifacts:
        attempt = {}
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # A concurrently appended final line is read again next poll.
                continue
            node = row["nodeid"]
            if row["when"] == "call" or (row["when"] == "setup" and row["outcome"] != "passed") or (row["when"] == "teardown" and row["outcome"] == "failed"):
                attempt[node] = dict(row, artifact=path.name)
        outcomes.update(attempt)
    for path in OUT.glob(PREFIX + "-*.xml"):
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            continue
        for case in tree.getroot().iter("testcase"):
            skipped = case.find("skipped")
            if skipped is not None and skipped.attrib.get("message") == "collection skipped":
                collection_skips[case.attrib["name"]] = dict(reason=skipped.text, artifact=path.name)
    return outcomes, collection_skips, [path.name for path in artifacts]


def summarize(manifest, active=(), *, finished=False):
    expected = set(manifest["selected_nodeids"])
    all_outcomes, collection_skips, artifacts = load_outcomes()
    outcomes = {node: row for node, row in all_outcomes.items() if node in expected}
    counts = Counter(row["outcome"] for row in outcomes.values())
    failures = []
    for row in outcomes.values():
        if row["outcome"] != "failed":
            continue
        classification, reason = classify(row)
        if row["nodeid"] == "tests/generate/test_background_loaded_reference.py::test_1700_medium_seagoing_witness_is_the_loaded_reference_and_passes":
            classification, reason = "ON-only", "Both module fixture and gate call explicitly enter rule_freeze_v1(enabled=True); the ON background gate rejects background.physics_input.changed fail-closed. No OFF checker rule failure is established; ON behavior left unchanged per order."
        if row["nodeid"] == "tests/generate/test_random_yard.py::test_cli_resume_and_configuration_validation" and "generation inputs changed while building shard" in (row.get("longrepr") or ""):
            classification, reason = "OFF source-epoch invalidation", "Generator rejects changed before/after generation-input identities fail-closed. Concurrent construct_v4/scalable source edits are measured; the exact differing binding field during this call was not captured. Fresh stable-source rerun required; no generator/guard edits."
        if "test_ownership_documentation_matches_map" in row["nodeid"]:
            classification, reason = "OFF ownership documentation mirror", "Ownership source map repaired; leader-owned architecture table still requires the proposed exact rows. Assertion unchanged."
        if "test_actual_standard_manual_condition_passes_complete_voyage_checker[feeder_500-C2]" in row["nodeid"]:
            classification, reason = "OFF synthetic manual witness invalid", "Real synthetic HEEL/manual-recipe regression, reported per leader instruction: iscode.loading.draft / port.call.draft reject draught7.0612144104520675m >7.05m. Hard assertions/limits/recipe unchanged; C2 is the condition label."
        if "test_actual_standard_manual_condition_passes_complete_voyage_checker[feeder_1700-C2]" in row["nodeid"]:
            classification, reason = "OFF synthetic manual witness invalid", "Real synthetic HEEL/manual-recipe regression, reported per leader instruction: first hard rule is iscode.a2_2.initial_gm, GM-1.3419069784503033m <0.15m, also weather. Hard assertions/limits/recipe unchanged; C2 is the condition label."
        failures.append({"nodeid": row["nodeid"], "classification": classification,
                         "reason": reason, "trace_artifact": row["artifact"]})
    missing = sorted(expected - outcomes.keys())
    summary = {
        "status": ("failed" if failures else "passed") if finished and not missing else "incomplete" if finished else "running",
        "command": "UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/mvp/smoke/run_v54b_suite.py --run --workers 2 (later admitted resume commands are recorded separately)",
        "runner_commands_artifact": PREFIX + "-runner-commands.json",
        "started_utc": manifest["created_utc"], "updated_utc": datetime.now(timezone.utc).isoformat(),
        "root_selection_collected_utc": manifest.get("final_selection_utc", manifest["created_utc"]),
        "initial_root_selection_collected_utc": manifest["created_utc"],
        "final_delta_added_count": len(manifest.get("final_added_nodeids", [])),
        "final_delta_removed_count": len(manifest.get("final_removed_nodeids", [])),
        "wall_s": time.time() - manifest["started_epoch"],
        "marker_expression": MARKERS, "manifest": PREFIX + "-manifest.json",
        "expected_selected": len(expected), "completed_selected": len(outcomes),
        "counts": dict(counts), "xfail": sum(bool(row.get("wasxfail")) and row["outcome"] == "skipped" for row in outcomes.values()),
        "xpass": sum(bool(row.get("wasxfail")) and row["outcome"] == "passed" for row in outcomes.values()),
        "counts_note": "Counts.skipped includes xfail; counts.passed includes non-strict xpass. These are separate explicitly counted subsets.",
        "collection_skipped": collection_skips, "complete": not missing,
        "missing_count": len(missing), "missing": missing,
        "failures": sorted(failures, key=lambda row: row["nodeid"]),
        "failure_classification_counts": dict(Counter(row["classification"] for row in failures)),
        "historical_excluded": manifest["historical_excluded"],
        "historical_exclusion_count": len(manifest["historical_excluded"]),
        "active_process_count": len(active), "active_pids": [job["child"].pid for job in active],
        "extra_noncanonical_count": len(all_outcomes.keys() - expected), "artifacts": artifacts,
        "source_coverage_note": "Exact coverage of the initial root-collected node snapshot. Source identity changes are reported separately; checkpointed processes do not establish a coherent frozen-source rerun if concurrent edits occurred.",
    }
    write_json(PREFIX + "-summary.json", summary)
    return summary


def collect_snapshot():
    initial_sources = source_snapshot()
    name = PREFIX + "-collect"
    env = dict(ENV, V54_SELECTION_PATH=str(OUT / (name + "-selection.json")))
    began = time.time()
    with (OUT / (name + ".log")).open("w") as output:
        child = subprocess.Popen(command(name, collect=True), cwd=ROOT, env=env,
                                 stdout=output, stderr=subprocess.STDOUT)
        record = {"command": command(name, collect=True), "pid": child.pid,
                  "started_epoch": began}
        write_json(name + "-command.json", record)
        result = child.wait()
    record.update(exit_code=result, elapsed_s=time.time() - began)
    write_json(name + "-command.json", record)
    if result:
        raise RuntimeError(f"Root collection failed ({result}); see {name}.log")
    selected = json.loads((OUT / (name + "-selection.json")).read_text())
    triage = json.loads((OUT / (name + "-triage.json")).read_text())
    excluded = [row for row in triage["matching_historical_cases"] if not row["selected"]]
    if len(excluded) != 51:
        raise RuntimeError(f"Expected exactly 51 reviewed historical exclusions, found {len(excluded)}; review before running")
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(), "started_epoch": began,
                "marker_expression": MARKERS, "selected_nodeids": selected,
                "historical_excluded": excluded, "initial_source_sha256": initial_sources,
                "max_cores": 3, "native_threads": 1, "max_parallel_pytest": 3,
                "source_coverage_note": "Fixed initial root snapshot; new test additions require an explicitly separate delta, never silently blended."}
    write_json(PREFIX + "-manifest.json", manifest)
    return manifest


def measured_weights():
    weights = {}
    for path in sorted(OUT.glob("v54-fast-*-reports.jsonl"), key=lambda p: p.stat().st_mtime):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row["when"] == "call":
                weights[row["nodeid"]] = max(row["duration"], .01)
    return weights


def collect_final_delta(manifest):
    """One final root snapshot, retaining the initial epoch and all raw outcomes."""
    if manifest.get("final_selection_utc"):
        raise RuntimeError("The one final root delta was already collected; resume its existing snapshot")
    name = PREFIX + "-final-collect"
    env = dict(ENV, V54_SELECTION_PATH=str(OUT / (name + "-selection.json")))
    began = time.time()
    with (OUT / (name + ".log")).open("w") as output:
        child = subprocess.Popen(command(name, collect=True), cwd=ROOT, env=env,
                                 stdout=output, stderr=subprocess.STDOUT)
        record = {"command": command(name, collect=True), "pid": child.pid, "started_epoch": began}
        write_json(name + "-command.json", record)
        result = child.wait()
    record.update(exit_code=result, elapsed_s=time.time() - began)
    write_json(name + "-command.json", record)
    if result:
        raise RuntimeError(f"Final root collection failed ({result}); see {name}.log")
    selected = json.loads((OUT / (name + "-selection.json")).read_text())
    triage = json.loads((OUT / (name + "-triage.json")).read_text())
    excluded = [row for row in triage["matching_historical_cases"] if not row["selected"]]
    if len(excluded) != 51:
        raise RuntimeError(f"Final root requires exactly51 reviewed exclusions, got {len(excluded)}")
    write_json(PREFIX + "-initial-manifest.json", manifest)
    old = set(manifest["selected_nodeids"])
    manifest = dict(manifest, selected_nodeids=selected, historical_excluded=excluded,
                    final_selection_utc=datetime.now(timezone.utc).isoformat(),
                    final_selection_artifact=name + "-selection.json",
                    final_added_nodeids=sorted(set(selected) - old),
                    final_removed_nodeids=sorted(old - set(selected)))
    write_json(PREFIX + "-manifest.json", manifest)
    return manifest


def partition(nodes, workers=3):
    weights = measured_weights()
    by_file = defaultdict(list)
    for node in nodes:
        by_file[node.split("::", 1)[0]].append(node)
    isolated = sorted(node for file, group in by_file.items() if file in ISOLATED for node in group)
    shards, totals = [[] for _ in range(workers)], [0. for _ in range(workers)]
    pending = [(sum(weights.get(node, .05) for node in group), file, group)
               for file, group in by_file.items() if file not in ISOLATED]
    for weight, file, group in sorted(pending, reverse=True):
        index = min(range(workers), key=totals.__getitem__)
        shards[index].extend(group)
        totals[index] += weight
    return isolated, shards, totals


def start(nodes, name):
    targets = OUT / (name + "-targets.json")
    write_json(targets.name, sorted(nodes))
    env = dict(ENV, V54_NODE_SELECTION_PATH=str(targets),
               V54_SELECTION_PATH=str(OUT / (name + "-selection.json")),
               V54_REPORT_PATH=str(OUT / (name + "-reports.jsonl")))
    output = (OUT / (name + ".log")).open("w")
    (OUT / (name + "-reports.jsonl")).write_text("")
    began = time.time()
    child = subprocess.Popen(command(name), cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
    record = {"command": command(name), "pid": child.pid, "started_epoch": began,
              "target_count": len(nodes), "targets": targets.name, "native_threads": 1,
              "selection_environment": {key: value for key, value in env.items() if key.startswith("V54_")}}
    write_json(name + "-command.json", record)
    return {"child": child, "output": output, "name": name, "record": record}


def run_group(jobs, manifest):
    active = list(jobs)
    try:
        while active:
            for job in list(active):
                child = job["child"]
                if child.poll() is not None:
                    job["record"].update(exit_code=child.returncode,
                                         elapsed_s=time.time() - job["record"]["started_epoch"])
                    write_json(job["name"] + "-command.json", job["record"])
                    job["output"].close()
                    active.remove(job)
            summarize(manifest, active)
            if active:
                time.sleep(2)
    except KeyboardInterrupt:
        # Only these already-recorded owned child PIDs receive a signal.
        for job in active:
            if job["child"].poll() is None:
                job["child"].send_signal(signal.SIGINT)
        for job in active:
            try:
                job["child"].wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
        summarize(manifest, [job for job in active if job["child"].poll() is None], finished=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Start ONLY after smoke completion and admitted 3CPU ledger reservation")
    parser.add_argument("--resume", action="store_true", help="Reuse the exact root manifest; run only node IDs without outcomes")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=3,
                        help="Admitted serial pytest workers; below3 defers isolated child-process tests until a3CPU resume")
    parser.add_argument("--final-delta", action="store_true",
                        help="With --run --resume, root-collect exactly once and run newly missing current nodes")
    parser.add_argument("--allow-two-core-isolated", action="store_true",
                        help="Run only the source/node-audited fast isolated set alone under2CPU; no worker overrides")
    parser.add_argument("--rerun-node", action="append", default=[],
                        help="Explicit existing node to rerun in a fresh checkpoint process (repeatable)")
    parser.add_argument("--summarize", action="store_true", help="Only regenerate persisted summary; no tests started")
    args = parser.parse_args()
    if args.summarize:
        manifest = json.loads((OUT / (PREFIX + "-manifest.json")).read_text())
        summary = summarize(manifest, finished=True)
        print(json.dumps({k: v for k, v in summary.items() if k in {"status", "counts", "expected_selected", "completed_selected", "missing_count", "historical_exclusion_count"}}))
        return int(not summary["complete"] or bool(summary["failures"]))
    if not args.run:
        parser.print_help()
        return 0
    path = OUT / (PREFIX + "-manifest.json")
    if path.exists() and not args.resume:
        parser.error("Existing checkpoint: use --run --resume; do not overwrite evidence")
    if args.final_delta and not args.resume:
        parser.error("--final-delta requires --run --resume")
    manifest = json.loads(path.read_text()) if args.resume else collect_snapshot()
    if args.final_delta:
        manifest = collect_final_delta(manifest)
    summary = summarize(manifest)
    targets = set(summary["missing"]) | set(args.rerun_node)
    if not targets <= set(manifest["selected_nodeids"]):
        parser.error("--rerun-node must belong to the selected root snapshot")
    isolated, shards, totals = partition(sorted(targets), workers=args.workers)
    pool_admitted = args.workers == 3
    if args.allow_two_core_isolated:
        if args.workers != 2:
            parser.error("--allow-two-core-isolated requires --workers2")
        audit = json.loads((OUT / (PREFIX + "-isolated-audit.json")).read_text())
        if not set(isolated) <= set(audit["approved_nodeids"]):
            raise RuntimeError("An isolated node lacks the current two-core source audit")
        for filename, digest in audit["test_source_sha256"].items():
            if hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"Isolated source audit changed: {filename}")
        pool_admitted = True
    attempt = 1 + len(list(OUT.glob(PREFIX + "-partition-r*.json")))
    write_json(f"{PREFIX}-partition-r{attempt}.json", {
        "isolated_nodeids": isolated, "shard_nodeids": shards,
        "workers": args.workers, "isolated_deferred_for_capacity": bool(isolated and not pool_admitted),
        "two_core_isolated_audit": bool(args.allow_two_core_isolated),
        "estimated_shard_call_s_from_prior_measurements": totals,
        "new_unmeasured_node_default_s": .05,
        "note": "Measured call durations balance files, not a runtime guarantee; all subprocesses root-collect before exact node filtering."})
    if isolated and pool_admitted:
        run_group([start(isolated, f"{PREFIX}-isolated-r{attempt}")], manifest)
    jobs = [start(nodes, f"{PREFIX}-shard{index + 1}-r{attempt}")
            for index, nodes in enumerate(shards) if nodes]
    run_group(jobs, manifest)
    after = source_snapshot()
    before = manifest["initial_source_sha256"]
    drift = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    write_json(PREFIX + "-source-closure.json", {"checked_utc": datetime.now(timezone.utc).isoformat(),
               "changed_source_or_test_paths": drift, "after_source_sha256": after,
               "coherent_frozen_source_claim": not drift,
               "note": "Source identity change detection is separate from exact node-ID coverage."})
    summary = summarize(manifest, finished=True)
    if summary["missing"] and not pool_admitted and set(summary["missing"]) <= set(isolated):
        summary.update(status="queued_pool_capacity", active_process_count=0,
                       note="All non-pool nodes completed; isolated real child-process tests await a3CPU admitted --run --resume")
        write_json(PREFIX + "-summary.json", summary)
    print(json.dumps({key: summary[key] for key in ["status", "counts", "xfail", "xpass", "expected_selected", "completed_selected", "missing_count", "historical_exclusion_count", "wall_s"]}))
    return int(not summary["complete"] or bool(summary["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
