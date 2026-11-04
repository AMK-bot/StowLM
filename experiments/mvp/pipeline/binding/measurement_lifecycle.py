"""Fsynced append-only attempt evidence, including unknown interruptions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid


def durable_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x") as stream:
        stream.write(json.dumps(value, indent=2, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def append_event(path, value):
    data = (json.dumps(value, sort_keys=True, default=str) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        written = os.write(fd, data)
        if written != len(data):
            raise OSError("incomplete lifecycle append")
        os.fsync(fd)
    finally:
        os.close(fd)


class Attempt:
    def __init__(self, root, logical_key, identity, metadata):
        self.id = uuid.uuid4().hex
        self.root = Path(root)
        self.path = self.root / "attempts" / self.id
        self.path.mkdir(parents=True)
        self.started_at = time.time()
        self.started_monotonic = time.monotonic()
        self.start = dict(attempt_id=self.id, logical_key=logical_key,
                          measurement_identity=identity, started_at=self.started_at,
                          pid=os.getpid(), metadata=metadata)
        durable_json(self.path / "start.json", self.start)
        self.event("started", **self.start)

    def event(self, state, **fields):
        append_event(self.path / "lifecycle.jsonl", {"state": state, "at": time.time(),
                                                   "attempt_id": self.id, **fields})

    def incumbent(self, event):
        relative = "incumbents/" + uuid.uuid4().hex + ".json"
        durable_json(self.path / relative, event)
        self.event("checked_incumbent", evidence=relative, lifts=event.get("lifts"),
                   wall_s=event.get("wall_s"))

    def unknown(self, error):
        record = dict(status="unknown", verdict="unknown", attempt_id=self.id,
                      started_at=self.started_at, interrupted_at=time.time(),
                      wall_s=time.monotonic() - self.started_monotonic,
                      reason=type(error).__name__ + ": " + str(error))
        durable_json(self.path / "unknown.json", record)
        self.event("unknown", **{key: value for key, value in record.items() if key != "attempt_id"})
        return record

    def finish(self, row):
        durable_json(self.path / "final.json", row)
        self.event("finalized", row_key=row["key"], passed=bool(row.get("passed")),
                   wall_s=row["wall_s"])


def preserve_unfinished(root, logical_key, legacy_progress=None):
    """Copy prior evidence; never infer a verdict or alter an earlier attempt."""
    root = Path(root)
    prior = []
    for path in sorted((root / "attempts").glob("*/start.json")):
        start = json.loads(path.read_text())
        if start.get("logical_key") != logical_key:
            continue
        if (path.parent / "final.json").exists() or (path.parent / "unknown.json").exists():
            continue
        prior.append(dict(attempt_id=start["attempt_id"], start=start,
                          evidence_directory=str(path.parent), verdict="unknown",
                          reason="unfinished at resume; process liveness not inferred"))
    legacy = []
    if legacy_progress is not None:
        progress = Path(legacy_progress)
        header = progress / (logical_key + ".json")
        if header.exists() and not json.loads(header.read_text()).get("completed"):
            legacy = [path for path in progress.glob(logical_key + ".*") if path.is_file()]
    if not prior and not legacy:
        return None
    destination = root / "recovered" / uuid.uuid4().hex
    destination.mkdir(parents=True)
    copied = []
    for source in legacy:
        target = destination / source.name
        shutil.copy2(source, target)
        with target.open("rb") as stream:
            os.fsync(stream.fileno())
        copied.append(dict(source=str(source), file=target.name,
                           sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    record = dict(recovered_at=time.time(), logical_key=logical_key, verdict="unknown",
                  prior_attempts=prior, legacy_evidence=copied,
                  note="Historical incumbents belong only to their original attempt; originals untouched")
    if legacy:
        header = json.loads((destination / (logical_key + ".json")).read_text())
        original_identity = dict(logical_key=logical_key, started_at=header.get("started_at"),
                                 pid=header.get("pid"), input_sha256=header.get("measurement_identity", {}).get("payload", {}).get("input_sha256"))
        record["legacy_attempt_id"] = "legacy-" + hashlib.sha256(
            json.dumps(original_identity, sort_keys=True).encode()).hexdigest()
        record["legacy_original_identity"] = original_identity
    durable_json(destination / "recovery.json", record)
    return str(destination)


def unknown_attempts(root):
    root = Path(root)
    records = []
    for start_file in sorted((root / "attempts").glob("*/start.json")):
        path = start_file.parent
        if (path / "final.json").exists():
            continue
        start = json.loads(start_file.read_text())
        unknown = path / "unknown.json"
        records.append(dict(attempt_id=start["attempt_id"], logical_key=start["logical_key"],
                            status="unknown", started_at=start["started_at"],
                            detail=json.loads(unknown.read_text()) if unknown.exists() else
                            dict(reason="unfinished; abrupt interruption or running attempt, liveness unverified"),
                            evidence_directory=str(path)))
    seen_legacy = set()
    for recovery in sorted((root / "recovered").glob("*/recovery.json")):
        record = json.loads(recovery.read_text())
        if record.get("legacy_evidence"):
            original = record.get("legacy_original_identity")
            if original is None:
                # Earlier recovery sidecars used the raw header digest. Derive
                # the same stable original identity without rewriting them.
                header_path = recovery.parent / (record["logical_key"] + ".json")
                header = json.loads(header_path.read_text()) if header_path.exists() else {}
                original = dict(logical_key=record["logical_key"], started_at=header.get("started_at"),
                                pid=header.get("pid"), input_sha256=header.get("measurement_identity", {}).get("payload", {}).get("input_sha256"))
            identifier = "legacy-" + hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
            if identifier in seen_legacy:
                continue
            seen_legacy.add(identifier)
            start = original.get("started_at")
            records.append(dict(attempt_id=identifier,
                logical_key=record["logical_key"], status="unknown", started_at=start,
                detail=dict(reason="recovered unfinished legacy evidence; liveness unverified"),
                evidence_directory=str(recovery.parent)))
    return records
