"""Measure live/sealed equality for absolute margin-policy evidence pins.

This audits exposure in already-running pre-bootstrap ballast children; it does
not certify an entire plan or substitute for the checker and replay gates.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from driver import atomic_json


def digest_stable(path):
    before = path.stat()
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    after = path.stat()
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, key) != getattr(after, key) for key in fields):
        raise ValueError(f"resource changed during hashing: {path}")
    return dict(sha256=checksum, size=after.st_size, mtime_ns=after.st_mtime_ns,
                ctime_ns=after.st_ctime_ns, inode=after.st_ino)


def verify(snapshot, live_root):
    snapshot, live_root = Path(snapshot).resolve(), Path(live_root).resolve()
    bank = snapshot / "src/stowage/linear/margin_policy_data.json"
    resources = json.loads((snapshot / "RESOURCE-MANIFEST.json").read_text())["files"]
    core = json.loads((snapshot / "MANIFEST.json").read_text())["files"]
    paths = set()

    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get("path"), str) and Path(value["path"]).is_absolute():
                paths.add(value["path"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(json.loads(bank.read_text()))
    records = []
    for name in sorted(paths):
        record = dict(live_path=name, equal=False)
        try:
            relative = str(Path(name).relative_to(live_root))
            sealed = snapshot / relative
            pin = core.get(relative) or resources[relative]["sha256"]
            record.update(sealed_path=str(sealed), live=digest_stable(Path(name)),
                          sealed=digest_stable(sealed),
                          manifest_sha256=pin)
            record["equal"] = (record["live"]["sha256"] == record["sealed"]["sha256"]
                               == record["manifest_sha256"])
        except (OSError, ValueError, KeyError) as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        records.append(record)
    return dict(verified_utc=datetime.now(timezone.utc).isoformat(),
                runtime_snapshot=str(snapshot), live_root=str(live_root),
                bank_sha256=digest_stable(bank)["sha256"],
                absolute_evidence_paths=len(records),
                matching_paths=sum(row["equal"] for row in records),
                all_equal=bool(records) and all(row["equal"] for row in records),
                limitation="Point-in-time byte equality for absolute bank evidence only; rerun after the pre-bootstrap probe completes.",
                records=records)


def main():
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--live-root", type=Path, default=root)
    parser.add_argument("--output", type=Path,
                        default=root / "experiments/mvp/gold/D100-child-resource-equivalence.json")
    args = parser.parse_args()
    snapshot = args.snapshot
    if snapshot is None:
        pointer = root / "experiments/mvp/gold/runtime-epoch.json"
        snapshot = Path(json.loads(pointer.read_text())["runtime_root"])
    result = verify(snapshot, args.live_root)
    atomic_json(args.output, result)
    print(json.dumps({key: result[key] for key in
                      ("verified_utc", "absolute_evidence_paths", "matching_paths", "all_equal")}))
    raise SystemExit(0 if result["all_equal"] else 1)


if __name__ == "__main__":
    main()
