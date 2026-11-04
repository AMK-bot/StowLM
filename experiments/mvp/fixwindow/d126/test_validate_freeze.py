"""Boundary gate cases using inert temporary metadata, never producer files."""
from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("d126_freeze_validator", HERE / "validate_freeze.py")
validator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validator
spec.loader.exec_module(validator)


@pytest.fixture
def frozen(tmp_path):
    tools = tmp_path / "experiments/mvp/corpus_tools"
    corpus = tmp_path / "experiments/mvp/corpus"
    runtime = corpus / ".runtime/small"
    tools.mkdir(parents=True)
    runtime.mkdir(parents=True)

    def write(path, data):
        path.write_text(json.dumps(data))
        return sha256(path.read_bytes()).hexdigest()

    marker = tools / "STOP-D126.json"
    write(marker, {"decision": "D126"})
    batch = tools / "small_batch.py"
    batch.write_text("def main():\n    if STOP_FILE.exists():\n        raise SystemExit(75)\n    if args.worker_request:\n        pass\n")
    coordinator = tools / "run_d90.py"
    coordinator.write_text("def main():\n    if (TOOLS/'STOP-D126.json').exists():\n        raise SystemExit(75)\n")
    write(runtime / "campaign.json", {"pid": 123, "ended_at_utc": "synthetic", "complete": False})
    write(runtime / "priority-owner.json", {"pid": 456})
    log = runtime / "terminal.log"
    log.write_text("synthetic owner wait/reap metadata, no instance data\n")
    manifest_hash = write(corpus / "manifest.json", {"synthetic_inventory": True})
    stop = {"stopped": True,
        "producer_workers": "all admitted draw workers drained; nested wait returned; outer reaped",
        "nested_terminal": {"ended_at_utc": "synthetic"}, "outer_wave": {"exit_code": 0},
        "coordinator": {"pid": 789, "state": "failed", "has_reservation": False,
            "restart_blocked_by": str(marker.relative_to(tmp_path)), "sigterm_denied": "synthetic denied"},
        "nested_log": str(log.relative_to(tmp_path)), "nested_log_sha256": sha256(log.read_bytes()).hexdigest()}
    stop_path = tools / "D126-stop-evidence.json"
    stop_hash = write(stop_path, stop)
    receipt = {"status": "frozen_truncated", "complete": False,
        "uncommitted_instance_files": [], "manifest_sha256": manifest_hash,
        "stop_evidence": {"path": str(stop_path), "sha256": stop_hash, "content": stop},
        "runtime_file_sha256": {str(path): sha256(path.read_bytes()).hexdigest()
            for path in (runtime / "campaign.json", runtime / "priority-owner.json", batch, coordinator)}}
    receipt_path = corpus / "FROZEN-D126.json"
    write(receipt_path, receipt)
    return tmp_path, receipt_path, receipt, write


def test_actual_truncated_quota_semantics_and_idle_coordinator_allowed(frozen):
    root, _, _, _ = frozen
    result = validator.validate(root)
    assert result["producer_epoch_closed"] is True
    assert result["summary"]["requested_campaign_complete"] is False
    assert result["signals_sent"] is False


def test_absent_receipt_never_opens_gate(tmp_path):
    assert validator.validate(tmp_path)["producer_epoch_closed"] is False


@pytest.mark.parametrize("problem", ["not_drained", "uncommitted", "runtime_drift", "stop_guard_removed"])
def test_missing_drain_or_changed_poststop_metadata_closes_gate(frozen, problem):
    root, path, receipt, write = frozen
    if problem == "not_drained":
        stop_path = Path(receipt["stop_evidence"]["path"])
        stop = receipt["stop_evidence"]["content"]
        stop["stopped"] = False
        receipt["stop_evidence"]["sha256"] = write(stop_path, stop)
    elif problem == "uncommitted":
        receipt["uncommitted_instance_files"] = ["synthetic-uncommitted.json"]
    elif problem == "runtime_drift":
        (root / "experiments/mvp/corpus/.runtime/small/campaign.json").write_text("{}")
    else:
        (root / "experiments/mvp/corpus_tools/small_batch.py").write_text("def main():\n    if args.worker_request:\n        pass\n")
    write(path, receipt)
    assert validator.validate(root)["producer_epoch_closed"] is False
