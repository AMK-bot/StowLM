"""Hash-guarded D126 watchdog installation; check-only unless --apply is explicit.

Run --apply only after the owner declares the manual window complete. This
stdlib-only tool checks patch applicability without mutating git state, backs
up exact old bytes, installs exact reviewed candidates, promotes the staged
regression, and writes receipts plus a checker-notes template. It never appends
to leader/owner notes or runs tests/numerical builders.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FENCE = ROOT / "experiments/mvp/fixwindow"
FILES = (
    dict(target="src/stowage/checker/stability.py",
         candidate="experiments/mvp/fixwindow/d121-watchdog/stability-cache-timeout.py",
         before="72c4ec7390b541dd5dd397b1a18c433547f1851b04a49b8cd49589f7da4a2e97",
         after="64c0a06761da330b5ab78efb2776c8cea12d37225f8059f581893dddfa5410e5"),
    dict(target="experiments/mvp/gold_tools/driver.py",
         candidate="experiments/mvp/fixwindow/d121-watchdog/D126/gold-driver-d126-candidate.py",
         before="5dd16d6bfac236578cd8907f3d0d61a4997b79e6d8a4234d0729c04e69379c6a",
         after="14748e72644b7b20b8da96d81094734197cea6fa6c95e81e70857fc532f62bd4"),
    dict(target="tests/checker/test_external_watchdog.py",
         candidate="experiments/mvp/fixwindow/d126/tests/checker/test_external_watchdog.py",
         before=None,
         after="e0bb4d81bf73e16f552a5ec7f28e87e2d7fba035564a36a45cc1dcc98304b25b"),
)
PATCHES = (
    ("experiments/mvp/fixwindow/d121-watchdog/stability-cache-timeout.patch",
     "2c9d49653beb880cc843a7445da17f0af5547daefaa8faef7bb86193b6571370"),
    ("experiments/mvp/fixwindow/d121-watchdog/D126/gold-driver-d126.patch",
     "9de96c168c155133e766259010026766c1919ed01a21dcd80e75747764fcf402"),
)


def digest(raw):
    return sha256(raw).hexdigest()


def read_regular(path):
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"not a regular nonsymlink file: {path}")
    return path.read_bytes()


def target_path(relative):
    path = ROOT / relative
    if not path.resolve().is_relative_to(ROOT) or path.is_symlink():
        raise ValueError(f"target escapes repository or is a symlink: {relative}")
    return path


def current(path):
    return digest(read_regular(path)) if path.exists() or path.is_symlink() else None


def atomic_write(path, raw, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".d126-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save(path, value):
    atomic_write(path, (json.dumps(value, indent=2, allow_nan=False)+"\n").encode())


def readonly_patch_check(reverse=False):
    command = ["git", "apply", "--check"] + (["--reverse"] if reverse else []) + [str(ROOT/path) for path, _ in PATCHES]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    record = dict(command=command, exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    if result.returncode:
        raise ValueError("read-only patch applicability check failed: " + result.stderr.strip())
    return record


def preflight():
    candidates, rows = {}, []
    for relative, expected in PATCHES:
        raw = read_regular(ROOT/relative)
        if digest(raw) != expected:
            raise ValueError(f"prepared patch drift: {relative}")
    for declaration in FILES:
        row = dict(declaration)
        raw = read_regular(ROOT/row["candidate"])
        if digest(raw) != row["after"]:
            raise ValueError(f"prepared candidate drift: {row['candidate']}")
        ast.parse(raw, filename=row["target"])
        candidates[row["target"]] = raw
        row["current_sha256"] = current(target_path(row["target"]))
        rows.append(row)
    source_rows, test_row = rows[:2], rows[2]
    if all(row["current_sha256"] == row["before"] for row in source_rows):
        if test_row["current_sha256"] not in (None, test_row["after"]):
            raise ValueError("existing production regression differs; refusing overwrite")
        state, check = "ready", readonly_patch_check()
    elif all(row["current_sha256"] == row["after"] for row in rows):
        state, check = "already_installed", readonly_patch_check(reverse=True)
    else:
        raise ValueError("live source drift or mixed installation; no rebase/partial promotion permitted")
    return dict(state=state, files=rows, patches=[dict(path=path, sha256=value) for path, value in PATCHES],
                patch_check=check), candidates


def boundary_receipt(path):
    raw = read_regular(path)
    value = json.loads(raw)
    if value.get("producer_epoch_closed") is not True or not value.get("evidence"):
        raise ValueError("recorded producer-owner closure/evidence required")
    if not value.get("checks") or not all(item is True for item in value["checks"].values()):
        raise ValueError("producer-owner boundary contains an unpassed check")
    # Rehash the named metadata evidence only, never any corpus input body.
    for name in ("freeze_receipt", "owner_stop", "restart_stop_marker"):
        item = value["evidence"][name]
        evidence = Path(item["path"])
        if not evidence.resolve().is_relative_to(ROOT):
            raise ValueError("boundary evidence escapes repository")
        if current(evidence) != item["sha256"]:
            raise ValueError(f"producer boundary metadata drift: {name}")
    return dict(path=str(path.resolve()), sha256=digest(raw), producer_epoch_closed=True,
                manual_window_completion="owner assertion by explicit --apply invocation; not inferred from elapsed time")


def notes_template(record):
    lines = ["### D126 watchdog installed — " + record["finished_utc"] + " — checker held", "",
             "Applied exact reviewed watchdog candidates after the owner's manual window.",
             "The gold alarm is a private latched BaseException; its stage boundary records",
             "operational unavailable and converts only for worker logging. Optional-cache",
             "TimeoutError is re-raised. No physical rule, residual limit or kernel cap changed.", ""]
    for row in record["preflight"]["files"]:
        lines.append(f"- `{row['target']}` SHA256 `{row['after']}`")
    lines += ["", "Read-only forward/reverse applicability and installation hashes passed.",
              "Production regression promoted byte-exactly. Post-application tests: PENDING;",
              "do not claim them green from this installation receipt. Earlier candidate/helper",
              "receipts remain historical. Record fresh source epochs before gold/loop adoption.",
              "Source-qualified cache bundle manifests must be re-exported when their pins",
              "change; existing native qualification remains authoritative. Checker ownership",
              "is still held until the parent explicitly releases it.", ""]
    return "\n".join(lines)


def run(*, apply, receipt_dir, boundary=None):
    receipt_dir = Path(receipt_dir).resolve()
    if not receipt_dir.is_relative_to(FENCE) or receipt_dir == FENCE:
        raise ValueError("receipt directory must be strictly inside experiments/mvp/fixwindow")
    receipt_dir.mkdir(parents=True, exist_ok=False)
    record = dict(decision="D126", applied=False, mode="apply" if apply else "check",
                  started_utc=datetime.now(timezone.utc).isoformat(), installer_sha256=digest(Path(__file__).read_bytes()),
                  no_tests_or_numerical_work=True, publication_intents=[], written=[], rollback=[])
    receipt = receipt_dir/"RECEIPT.json"
    save(receipt, record)
    originals = {}
    try:
        record["preflight"], candidates = preflight()
        save(receipt, record)
        if not apply:
            record.update(passed=True, checked_only=True, finished_utc=datetime.now(timezone.utc).isoformat())
            save(receipt, record)
            return record
        if record["preflight"]["state"] != "ready":
            raise ValueError("already installed: use --check; never overwrite historical backups")
        if boundary is None:
            raise ValueError("--boundary is required with --apply, after the manual window")
        record["boundary"] = boundary_receipt(Path(boundary))
        # Save every original before the first target write; a new test has an
        # explicit absent-original record and is promoted only in this branch.
        for row in record["preflight"]["files"]:
            path = target_path(row["target"])
            raw = read_regular(path) if path.exists() else None
            if (digest(raw) if raw is not None else None) != row["current_sha256"]:
                raise ValueError("concurrent target edit before backup")
            mode = stat.S_IMODE(path.stat().st_mode) if raw is not None else 0o644
            originals[row["target"]] = (raw, mode)
            if raw is not None:
                backup = receipt_dir/"backups"/row["target"]
                atomic_write(backup, raw, mode)
                if digest(read_regular(backup)) != row["current_sha256"]:
                    raise ValueError("backup hash mismatch")
        record["backups_complete"] = True
        save(receipt, record)
        for row in record["preflight"]["files"]:
            path = target_path(row["target"])
            if current(path) != row["current_sha256"]:
                raise ValueError(f"concurrent target edit before publication: {row['target']}")
            # Journal intent before replacement, including cancellation between
            # os.replace() and recording the successful write below.
            record["publication_intents"].append(row["target"])
            save(receipt, record)
            atomic_write(path, candidates[row["target"]], originals[row["target"]][1])
            record["written"].append(row["target"])
            save(receipt, record)
            if current(path) != row["after"]:
                raise ValueError(f"installed hash mismatch: {row['target']}")
        record["reverse_patch_check"] = readonly_patch_check(reverse=True)
        for row in record["preflight"]["files"]:
            if current(target_path(row["target"])) != row["after"] or digest(read_regular(ROOT/row["candidate"])) != row["after"]:
                raise ValueError("post-install target/candidate hash mismatch")
        record.update(passed=True, applied=True, finished_utc=datetime.now(timezone.utc).isoformat())
        save(receipt, record)
        atomic_write(receipt_dir/"CHECKER-NOTES.template.md", notes_template(record).encode())
        return record
    except BaseException as error:
        # Restore only bytes written by this invocation, never a subsequent
        # foreign edit. Keep backups/receipts for manual conflict recovery.
        declared = {row["target"]: row for row in FILES}
        for relative in reversed(record["publication_intents"]):
            row = declared[relative]
            try:
                path = target_path(relative)
                raw, mode = originals[relative]
                now = current(path)
                original_sha = digest(raw) if raw is not None else None
                if now != original_sha:
                    if now != row["after"]:
                        raise ValueError("foreign/concurrent edit preserved; rollback refused")
                    if raw is None:
                        path.unlink()
                    else:
                        atomic_write(path, raw, mode)
                record["rollback"].append(dict(target=relative, restored=True))
            except Exception as failure:
                record["rollback"].append(dict(target=relative, restored=False, error=str(failure)))
        record.update(passed=False, applied=False, error=f"{type(error).__name__}: {error}",
                      finished_utc=datetime.now(timezone.utc).isoformat())
        save(receipt, record)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--apply", action="store_true", help="only after the parent declares the manual window complete")
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--boundary", type=Path)
    args = parser.parse_args(argv)
    try:
        record = run(apply=args.apply, receipt_dir=args.receipt_dir, boundary=args.boundary)
    except Exception as error:
        print(json.dumps(dict(passed=False, applied=False, error=f"{type(error).__name__}: {error}")))
        return 2
    print(json.dumps({key: record.get(key) for key in ("passed", "applied", "mode", "finished_utc")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
