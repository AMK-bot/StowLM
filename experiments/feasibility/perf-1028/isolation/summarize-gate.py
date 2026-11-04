"""Audit all independent gate shards; partial coverage can never pass."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
TERMINAL = {"complete", "dead", "measurement-prefixes"}


def summarize():
    corpus = json.loads((HERE / "gate-all48-manifest.json").read_text())
    rows = corpus["instances"]
    fresh = json.loads((HERE / "fresh-manifest.json").read_text())
    assert len(rows) >= 48 and len(fresh["instances"]) * 2 >= len(rows)
    assert fresh["complete"] and all(r["c2_screened"] and r["background_accepted"] for r in fresh["instances"])
    expected = {f"{row['id']}/c2-1/seed-{seed}" for row in rows for seed in corpus["seeds"]}
    expected |= {f"{row['id']}/c2-1/measurement" for row in rows if row["measurement"]}
    records, active, shards, failures = {}, [], [], []
    worker_drift, identity_errors = {}, []
    source_identity = runtime_identity = None
    for path in sorted(HERE.glob("gate-*.json")):
        data = json.loads(path.read_text())
        if "runs" not in data:
            continue
        if source_identity is None:
            source_identity, runtime_identity = data["sources"], data["runtime"]
        if data["sources"] != source_identity:
            identity_errors.append(f"Different code identity: {path.name}")
        if data["runtime"] != runtime_identity or data.get("runtime_after", runtime_identity) != runtime_identity:
            identity_errors.append(f"Different runtime identity: {path.name}")
        if data.get("changed_sources"):
            worker_drift[path.name] = data["changed_sources"]
        if data.get("first_mismatch"):
            failures.append(dict(shard=path.name, mismatch=data["first_mismatch"]))
        validated = "sources_after" in data
        audit_passed = validated and not data.get("changed_sources") and data.get("runtime_after") == data["runtime"]
        shards.append(dict(path=path.name, validated=validated, usage=data.get("usage"), failure=data.get("failure")))
        for key, run in data["runs"].items():
            if key not in expected:
                continue
            if run["status"] in TERMINAL:
                prior = records.get(key)
                if prior:
                    assert prior["states"] == run["states"] or [s["mask_sha256"] for s in prior["states"]] == [s["mask_sha256"] for s in run["states"]]
                if prior is None or validated:
                    records[key] = dict(run, shard=path.name, source_audit_finished=validated, source_audit_passed=audit_passed)
            else:
                active.append(dict(key=key, status=run["status"], actions=len(run["actions"]), states=len(run["states"]), shard=path.name))
    current_drift = {}
    for path, expected_hash in (source_identity or {}).items():
        source = ROOT / path
        actual = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
        if actual != expected_hash:
            current_drift[path] = dict(expected=expected_hash, actual=actual)
    for source in (ROOT / "src").rglob("*"):
        if not source.is_file() or "__pycache__" in source.parts or source.suffix == ".pyc":
            continue
        relative = str(source.relative_to(ROOT))
        if relative not in (source_identity or {}):
            current_drift[relative] = dict(expected=None, actual=hashlib.sha256(source.read_bytes()).hexdigest())
    missing = sorted(expected - records.keys())
    invalidated = bool(worker_drift or current_drift or identity_errors)
    complete = not invalidated and not failures and not missing and all(r["source_audit_passed"] for r in records.values())
    result = dict(status="INVALIDATED" if invalidated else "PASS" if complete else "FAIL" if failures else "INCOMPLETE", expected_cases=len(expected),
        completed_cases=len(records), terminal_rollouts=sum("/seed-" in k for k in records),
        measurement_replays=sum(k.endswith("/measurement") for k in records),
        completed_paired_states=sum(len(r["states"]) for r in records.values()),
        fresh_instances=len(fresh["instances"]), total_instances=len(rows), missing=missing,
        active=active, shards=shards, failures=failures,
        cases_with_clean_worker_audit=sum(r["source_audit_passed"] for r in records.values()),
        current_source_drift=current_drift, worker_source_drift=worker_drift, identity_errors=identity_errors,
        interpretation="Coverage counts describe observed equality only. Any source drift disqualifies the current-production gate.")
    (HERE / "gate-summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("status", "expected_cases", "completed_cases", "terminal_rollouts", "measurement_replays", "completed_paired_states", "active")}))
    return result


if __name__ == "__main__":
    summarize()
