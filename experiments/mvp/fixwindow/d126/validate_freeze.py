"""Read-only D126 producer boundary validator; never reads corpus inputs.

Only the optional report under this experiment is written. The producer owner
supplies actual wait/reap evidence; an idle stopped/failed coordinator need not
be absent. No signals, subprocess enumeration or generator imports are used.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def snapshot(path):
    raw = path.read_bytes()
    return json.loads(raw), {"path": str(path), "sha256": sha256(raw).hexdigest(),
                             "bytes": len(raw), "mtime_ns": path.stat().st_mtime_ns}


def has_pre_worker_stop_guard(path):
    tree = ast.parse(path.read_bytes())
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    guards = [index for index, node in enumerate(main.body) if isinstance(node, ast.If)
              and "STOP_FILE.exists()" == ast.unparse(node.test)]
    worker = [index for index, node in enumerate(main.body) if isinstance(node, ast.If)
              and "args.worker_request" == ast.unparse(node.test)]
    return bool(guards and worker and guards[0] < worker[0])


def validate(root=ROOT):
    receipt_path = root / "experiments/mvp/corpus/FROZEN-D126.json"
    report = {"producer_epoch_closed": False, "checked_utc": datetime.now(timezone.utc).isoformat(),
        "evidence": {}, "checks": {}, "independent_os_liveness": "not asserted; ps is sandbox-denied; owner wait/reap evidence used",
        "test_artifact_bodies_read": False, "signals_sent": False, "producer_files_written": False}
    if not receipt_path.is_file():
        report["checks"]["freeze_receipt_exists"] = False
        report["status"] = "waiting_for_FROZEN-D126"
        return report
    receipt, identity = snapshot(receipt_path)
    report["evidence"]["freeze_receipt"] = identity
    checks = report["checks"]
    checks["freeze_receipt_exists"] = True
    checks["frozen_status"] = receipt.get("status") == "frozen_truncated"
    checks["no_uncommitted_instance_files"] = receipt.get("uncommitted_instance_files") == []
    stop_reference = receipt.get("stop_evidence", {})
    stop_path = Path(stop_reference.get("path", ""))
    expected_stop_path = root / "experiments/mvp/corpus_tools/D126-stop-evidence.json"
    checks["owner_stop_path"] = stop_path.resolve() == expected_stop_path.resolve()
    if not checks["owner_stop_path"] or not stop_path.is_file():
        report["status"] = "owner_stop_evidence_unverified"
        return report
    stopped, stop_identity = snapshot(stop_path)
    report["evidence"]["owner_stop"] = stop_identity
    checks["owner_stop_hash"] = stop_identity["sha256"] == stop_reference.get("sha256")
    checks["embedded_owner_stop_matches"] = stopped == stop_reference.get("content")
    checks["owner_reports_stopped"] = stopped.get("stopped") is True
    text = stopped.get("producer_workers", "")
    checks["owner_wait_reap_attestation"] = (isinstance(text, str) and
        all(value in text for value in ("draw workers drained", "wait returned", "reaped")))
    terminal = stopped.get("nested_terminal", {})
    checks["nested_stream_terminal"] = bool(terminal.get("ended_at_utc"))
    checks["outer_wave_reaped_exit0"] = stopped.get("outer_wave", {}).get("exit_code") == 0
    stop_marker = root / "experiments/mvp/corpus_tools/STOP-D126.json"
    marker, marker_identity = snapshot(stop_marker)
    report["evidence"]["restart_stop_marker"] = marker_identity
    checks["restart_stop_marker_present"] = marker.get("decision") == "D126"
    coordinator = stopped.get("coordinator", {})
    checks["coordinator_restart_blocked"] = coordinator.get("restart_blocked_by") == str(stop_marker.relative_to(root))
    run_d90 = root / "experiments/mvp/corpus_tools/run_d90.py"
    coordinator_main = next(node for node in ast.parse(run_d90.read_bytes()).body
        if isinstance(node, ast.FunctionDef) and node.name == "main")
    first = coordinator_main.body[0]
    checks["coordinator_restart_guard_present"] = (isinstance(first, ast.If) and
        "STOP-D126.json" in ast.unparse(first.test) and
        any(isinstance(node, ast.Raise) and "SystemExit(75)" in ast.unparse(node)
            for node in first.body))
    # The owner stop note's historical guard digest need not describe the
    # later finalized coordinator source. Current bytes must match the actual
    # FROZEN runtime pin below; check the current STOP branch directly above.
    report["evidence"]["owner_recorded_guard_sha256"] = coordinator.get("restart_guard_sha256")
    small_batch = root / "experiments/mvp/corpus_tools/small_batch.py"
    checks["new_draws_stop_before_worker_entry"] = has_pre_worker_stop_guard(small_batch)
    pinned = receipt.get("runtime_file_sha256", {})
    metadata = [root / "experiments/mvp/corpus/.runtime/small/campaign.json",
                root / "experiments/mvp/corpus/.runtime/small/priority-owner.json", small_batch, run_d90]
    stable = []
    for path in metadata:
        actual = sha256(path.read_bytes()).hexdigest()
        expected = pinned.get(str(path))
        stable.append({"path": str(path), "sha256": actual, "matches_frozen_metadata": actual == expected})
    report["evidence"]["post_stop_metadata"] = stable
    checks["post_stop_metadata_stable"] = all(row["matches_frozen_metadata"] for row in stable)
    nested_log = (root / stopped.get("nested_log", "")).resolve()
    checks["nested_terminal_log_hash"] = (nested_log.is_relative_to(root.resolve()) and nested_log.is_file() and
        sha256(nested_log.read_bytes()).hexdigest() == stopped.get("nested_log_sha256"))
    manifest = root / "experiments/mvp/corpus/manifest.json"
    checks["frozen_inventory_manifest_hash"] = sha256(manifest.read_bytes()).hexdigest() == receipt.get("manifest_sha256")
    # A stable second receipt read closes a concurrent metadata replacement
    # window without requiring an arbitrary sleep or elapsed-time threshold.
    checks["receipt_unchanged_during_validation"] = sha256(receipt_path.read_bytes()).hexdigest() == identity["sha256"]
    report["evidence"]["owner_producer_workers"] = text
    report["evidence"]["coordinator"] = {key: coordinator.get(key)
        for key in ("pid", "state", "has_reservation", "sigterm_denied")}
    report["summary"] = {"frozen_at_utc": receipt.get("frozen_at_utc"),
        "status": receipt.get("status"), "requested_campaign_complete": receipt.get("complete"),
        "active_counts": receipt.get("active_counts"), "source_epoch_count": len(receipt.get("source_epochs", {})),
        "producer_gates": receipt.get("producer_gates")}
    report["producer_epoch_closed"] = all(checks.values())
    report["status"] = "producer_boundary_verified_from_owner_reap_evidence" if report["producer_epoch_closed"] else "gate_not_verified"
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = validate()
    except (OSError, ValueError, TypeError, KeyError) as error:
        report = {"producer_epoch_closed": False, "evidence": {}, "error": f"{type(error).__name__}: {error}"}
    if args.output:
        output = args.output.resolve()
        if not output.is_relative_to(HERE.resolve()):
            raise ValueError("Report output must remain under fixwindow/d126")
        output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"producer_epoch_closed": report["producer_epoch_closed"],
        "failed_checks": [key for key, value in report.get("checks", {}).items() if not value],
        "receipt": report.get("evidence", {}).get("freeze_receipt"), "status": report.get("status"),
        "error": report.get("error")}, indent=2))
    raise SystemExit(0 if report["producer_epoch_closed"] else 2)


if __name__ == "__main__":
    main()
