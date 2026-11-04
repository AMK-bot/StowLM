"""Record stable binding source maps without importing binding or native code."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time


HERE = Path(__file__).resolve().parent
BINDING = HERE.parent / "binding"
RUNTIME_FILES = (
    "__init__.py", "adapter.py", "admission.py", "allocation.py",
    "confirmation.py", "hooks.py", "provenance.py", "runtime.py",
    "review3/__init__.py", "review3/api.py", "review3/hatch.py",
)


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         allow_nan=False, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def source_map(binding=BINDING):
    paths = {binding / name for name in RUNTIME_FILES}
    paths.update(p for p in (binding / "deadline").rglob("*.py")
                 if not p.name.startswith("test_"))
    result = {}
    for path in sorted(paths):
        with path.open("rb") as stream:
            result[path.relative_to(binding).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def observe(output, binding=BINDING):
    first = source_map(binding)
    second = source_map(binding)
    if first != second:
        return None
    canonical = {name: second[name] for name in RUNTIME_FILES}
    full_digest = digest(second)
    row = {
        "schema_version": "stowlm.binding-source-observation/1",
        "observed_utc": utc(), "observer_pid": os.getpid(),
        "binding_root": str(binding.resolve()),
        "sha256_json": full_digest,
        "fingerprint": full_digest,
        "canonical11_sha256": digest(canonical),
        "canonical_fingerprint": digest(canonical),
        "files": list(second), "source_sha256": second,
        "source_files": second,
        "canonical_runtime_files": canonical,
        "runtime_imports_deadline": False,
        "stable_double_read": True,
        "scope": "Fixed eleven runtime paths plus recursive deadline Python sources excluding test_ files; byte hashes only, no imports or native computation.",
    }
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{full_digest}.json"
    try:
        with target.open("x") as stream:
            json.dump(row, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError:
        return None
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-s", type=float, default=7200)
    parser.add_argument("--interval-s", type=float, default=2)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    output = HERE / "audits" / "binding-observations"
    output.mkdir(parents=True, exist_ok=True)
    launch = {"pid": os.getpid(), "started_utc": utc(),
              "duration_s": args.duration_s, "interval_s": args.interval_s,
              "canonical_runtime_files": list(RUNTIME_FILES),
              "log_path": str(output / "observer.log"), "native_computation": False}
    (output / f"observer-{os.getpid()}.json").write_text(json.dumps(launch, indent=2) + "\n")
    print(json.dumps({"event": "started", **launch}), flush=True)
    deadline = time.monotonic() + args.duration_s
    while True:
        try:
            row = observe(output)
            if row:
                print(json.dumps({"event": "new_digest", "utc": row["observed_utc"],
                                  "sha256_json": row["sha256_json"],
                                  "canonical11_sha256": row["canonical11_sha256"],
                                  "file_count": len(row["files"])}), flush=True)
        except (OSError, KeyError) as error:
            print(json.dumps({"event": "read_retry", "utc": utc(),
                              "error_type": type(error).__name__}), flush=True)
        if args.once or time.monotonic() >= deadline:
            break
        time.sleep(min(args.interval_s, max(0, deadline - time.monotonic())))
    print(json.dumps({"event": "finished", "utc": utc(), "pid": os.getpid()}), flush=True)


if __name__ == "__main__":
    main()
