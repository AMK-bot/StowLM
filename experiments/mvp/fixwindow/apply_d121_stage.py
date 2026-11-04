"""Install exact prepared patch outputs after a recorded owner epoch boundary.

Default mode only checks. No git mutation, fuzzy application or source rebase.
"""
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

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = HERE / "d121-20261030"


def digest(raw):
    return sha256(raw).hexdigest()


def prepared(stage):
    if stage == "heat":
        manifest = json.loads((HERE / "heat/manifest.json").read_text())
        rows = [{"target": manifest["live_source"], "candidate": manifest["scratch_source"],
                 "before": manifest["live_before_sha256"], "after": manifest["scratch_sha256"]},
                {"target": manifest["intended_test_target"], "candidate": manifest["standalone_test"],
                 "before": None, "after": digest((ROOT / manifest["standalone_test"]).read_bytes())}]
        patch, expected = HERE / "heat-ids.patch", manifest["patch_sha256"]
    else:
        manifest = json.loads((HERE / "manual/source-pins.json").read_text())
        rows = [{"target": relative, "candidate": str((HERE / "manual/candidate" / relative).relative_to(ROOT)),
                 "before": pins["before_sha256"], "after": pins["candidate_sha256"]}
                for relative, pins in manifest["source_files"].items()]
        patch, expected = HERE / "manual-c2.patch", manifest["patch_sha256"]
    if digest(patch.read_bytes()) != expected:
        raise RuntimeError("prepared patch hash changed")
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=ROOT, check=True)
    for row in rows:
        candidate = ROOT / row["candidate"]
        raw = candidate.read_bytes()
        if digest(raw) != row["after"]:
            raise RuntimeError(f"candidate drift: {candidate}")
        ast.parse(raw, filename=row["target"])
        target = ROOT / row["target"]
        current = digest(target.read_bytes()) if target.exists() else None
        if current != row["before"] or target.is_symlink():
            raise RuntimeError(f"live source drift: {row['target']}")
    return patch, expected, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("heat", "manual"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--boundary", type=Path)
    args = parser.parse_args()
    patch, patch_hash, rows = prepared(args.stage)
    record = {"stage": args.stage, "patch_sha256": patch_hash, "files": rows,
              "checked_utc": datetime.now(timezone.utc).isoformat(), "applied": False}
    if not args.apply:
        (OUT / f"{args.stage}-ready.json").write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps(record))
        return
    if args.boundary is None:
        raise RuntimeError("recorded producer-owner epoch boundary required before source mutation")
    boundary_raw = args.boundary.read_bytes()
    boundary = json.loads(boundary_raw)
    if boundary.get("producer_epoch_closed") is not True or not boundary.get("evidence"):
        raise RuntimeError("boundary receipt must record actual producer closure and evidence")
    if args.stage == "manual":
        prior = json.loads((OUT / "heat-applied.json").read_text())
        if not prior.get("applied"):
            raise RuntimeError("heat stage must precede manual stage")
    backup = OUT / f"before-{args.stage}"
    backup.mkdir(exist_ok=False)
    for row in rows:
        target = ROOT / row["target"]
        if target.exists():
            saved = backup / row["target"]
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(target.read_bytes())
    for row in rows:
        target = ROOT / row["target"]
        current = digest(target.read_bytes()) if target.exists() else None
        if current != row["before"]:
            raise RuntimeError(f"concurrent edit before publication: {target}")
        mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".d121-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((ROOT / row["candidate"]).read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
        if digest(target.read_bytes()) != row["after"]:
            raise RuntimeError(f"published hash mismatch: {target}")
    subprocess.run(["git", "apply", "--reverse", "--check", str(patch)], cwd=ROOT, check=True)
    record.update(applied=True, applied_utc=datetime.now(timezone.utc).isoformat(),
                  boundary_sha256=digest(boundary_raw), boundary=str(args.boundary),
                  decision=boundary.get("decision", "D121"))
    (OUT / f"{args.stage}-applied.json").write_text(json.dumps(record, indent=2) + "\n")
    meaning = ("E8 heated-tank/source namespace repair; distances and limits unchanged" if args.stage == "heat"
               else "C2 synthetic manual recipes and static ballast witness/provenance repaired; limits unchanged")
    with (ROOT / "experiments/mvp/determinism/checker-notes.md").open("a") as stream:
        stream.write(f"\n\n### {record['decision']} {args.stage} applied — {record['applied_utc']} — checker held\n\n{meaning}.\n")
        for row in rows:
            stream.write(f"- `{row['target']}` SHA256 `{row['after']}`\n")
        stream.write(f"\nReceipt: `fixwindow/d121-20261030/{args.stage}-applied.json`. Tests pending.\n")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
