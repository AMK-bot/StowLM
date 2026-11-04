"""Capture an explicitly requested, coherent gold runtime; never refresh silently.

Source/config/tokenizer hashes pin the epoch. Large calibration evidence is
transitively pinned by RESOURCE-MANIFEST.json, including sealed stat fingerprints.
Numerical caches are private copies, disposable and outside the source identity.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import resource
import time
import tempfile


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def files_under(directory):
    return sorted(p for p in directory.rglob("*") if p.is_file()
                  and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"})


def source_paths(root):
    paths = {p for name in ("src", "params", "data/raw/kcs")
             for p in files_under(root / name)}
    for name in ("pyproject.toml", "models/glimmer-30b-4bit/tokenizer.json"):
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(path)
        paths.add(path)
    return paths


def evidence_paths(root, core):
    """Follow explicit evidence records, rather than copying whole campaigns."""
    found, scanned = set(), set()
    pending = [p for p in core if p.suffix == ".json" and "src" in p.relative_to(root).parts]
    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get("path"), str) and isinstance(value.get("sha256"), str):
                path = Path(value["path"])
                path = path if path.is_absolute() else root / path
                path = path.resolve()
                if not path.is_relative_to(root):
                    raise ValueError(f"External evidence needs an explicit mapping: {path}")
                if path.is_file() and path not in found:
                    found.add(path)
                    # Evidence files are checksum inputs, not runtime config.
                    # Do not parse multi-hundred-MB training corpora as config.
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    while pending:
        path = pending.pop()
        if path in scanned:
            continue
        scanned.add(path)
        try:
            visit(json.loads(path.read_text()))
        except (ValueError, UnicodeError) as exc:
            if isinstance(exc, json.JSONDecodeError):
                continue
            raise
    return found - core


def inventory(root):
    core = source_paths(root)
    resources = evidence_paths(root, core)
    return ({str(p.relative_to(root)): digest(p) for p in sorted(core)},
            {str(p.relative_to(root)): digest(p) for p in sorted(resources)})


def clone_private(source, destination):
    """Copy bytes, never symlink/hardlink back into a mutable live directory."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def copy_warm_caches(root, target):
    for family in ("linear", "stability"):
        for path in files_under(root / "experiments/cache" / family):
            destination = target / "experiments/cache" / family / path.relative_to(root / "experiments/cache" / family)
            # Pinned calibration evidence may also be a cache member. Keep its
            # already-copied, sealed bytes and fingerprints unchanged.
            if not destination.exists():
                clone_private(path, destination)


def create(root, output, *, warm_caches=True, attempts=4, overlays=None, new_epoch=False):
    root, output = root.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # The pointer is a freeze: another epoch requires an explicit operator action.
    pointer = output / "runtime-epoch.json"
    if pointer.exists() and not new_epoch:
        raise FileExistsError(f"Active epoch already recorded; no automatic resnapshot: {pointer}")
    overlays = {name: Path(path).resolve() for name, path in (overlays or {}).items()}
    def current_inventory():
        core, resources = inventory(root)
        for name, path in overlays.items():
            if name not in core:
                raise ValueError(f"Overlay must replace an existing core file: {name}")
            core[name] = digest(path)
        return core, resources
    stage = Path(tempfile.mkdtemp(prefix=".runtime-capture-", dir=output))
    try:
        for attempt in range(1, attempts + 1):
            core, resources = current_inventory()
            for name in core.keys() | resources.keys():
                clone_private(overlays.get(name, root / name), stage / name)
            copied = {name: digest(stage / name) for name in core.keys() | resources.keys()}
            after_core, after_resources = current_inventory()
            if (core == after_core and resources == after_resources
                    and copied == {**core, **resources}):
                break
            shutil.rmtree(stage)
            stage.mkdir()
        else:
            raise RuntimeError("Live tree changed during all coherent-copy attempts")
        for name in core.keys() | resources.keys():
            (stage / name).chmod(0o444)
        resource_records = {}
        for name, sha in resources.items():
            stat = (stage / name).stat()
            resource_records[name] = dict(sha256=sha, size=stat.st_size,
                mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns)
        resource_manifest = stage / "RESOURCE-MANIFEST.json"
        resource_manifest.write_text(json.dumps(dict(format="gold-runtime-resources-v1",
            source_root=str(root), files=resource_records), sort_keys=True, indent=2) + "\n")
        resource_manifest.chmod(0o444)
        core[resource_manifest.name] = digest(resource_manifest)
        if overlays:
            overlay_manifest = stage / "SOURCE-OVERLAYS.json"
            overlay_manifest.write_text(json.dumps(dict(
                reason="D97 explicitly pins two-step v4.3 until the next leader go; live tree is v4.4",
                files={name: dict(source=str(path), sha256=core[name])
                       for name, path in overlays.items()}), sort_keys=True, indent=2) + "\n")
            overlay_manifest.chmod(0o444)
            core[overlay_manifest.name] = digest(overlay_manifest)
        epoch_hash = hashlib.sha256(json.dumps(core, sort_keys=True,
                                              separators=(",", ":")).encode()).hexdigest()
        target = output / f"runtime-epoch-{epoch_hash}"
        manifest = dict(format="gold-runtime-epoch-v1", epoch_sha256=epoch_hash,
            created_utc=datetime.now(timezone.utc).isoformat(), source_root=str(root),
            files=core, resource_manifest="RESOURCE-MANIFEST.json", attempts=attempt,
            source_bytes=sum((stage / name).stat().st_size for name in core),
            resource_bytes=sum(r["size"] for r in resource_records.values()),
            private_warm_caches=warm_caches,
            approximation="Path relocation preserves pinned evidence bytes and checksums; private numerical caches are disposable and excluded from source identity.")
        (stage / "MANIFEST.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        (stage / "MANIFEST.json").chmod(0o444)
        if target.exists():
            raise FileExistsError(target)
        stage.rename(target)
        if warm_caches:
            copy_warm_caches(root, target)
        record = dict(runtime_root=str(target), runtime_epoch_sha256=epoch_hash,
                      created_utc=manifest["created_utc"], files=len(core),
                      resource_files=len(resources), source_bytes=manifest["source_bytes"],
                      resource_bytes=manifest["resource_bytes"])
        if pointer.exists():
            previous = json.loads(pointer.read_text())
            history = output / "epoch-pointers"
            history.mkdir(exist_ok=True)
            (history / f"{previous['runtime_epoch_sha256']}.json").write_text(pointer.read_text())
        temporary = pointer.with_name(".runtime-epoch.json.partial")
        temporary.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        temporary.replace(pointer)
        return record
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output", type=Path, default=Path("experiments/mvp/gold"))
    parser.add_argument("--no-warm-caches", action="store_true")
    parser.add_argument("--new-epoch", action="store_true", help="Explicit leader-authorized refresh only")
    parser.add_argument("--twostep-v43-root", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    estimate = dict(pid=os.getpid(), cores=1, ram_gb=0.5, gpu=False,
                    estimated_wall_s=120, source="~3.2GB pinned evidence +3.6GB private cache copies; streaming 1MB hashes; one local worker")
    capture_path = args.output / "SNAPSHOT-CAPTURE.json"
    capture_path.write_text(json.dumps(dict(status="capturing", estimate=estimate), indent=2)+"\n")
    try:
        overlays = None
        if args.twostep_v43_root:
            names = ("twostep.py", "twostep_adapter.py", "twostep_construct.py", "twostep_terminal.py")
            overlays = {f"src/stowage/solver/{name}": args.twostep_v43_root / "src/stowage/solver" / name
                        for name in names}
        record = create(args.source_root, args.output, warm_caches=not args.no_warm_caches,
                        new_epoch=args.new_epoch, overlays=overlays)
    except Exception as exc:
        capture_path.write_text(json.dumps(dict(status="failed", estimate=estimate,
            elapsed_s=time.monotonic()-started, error=f"{type(exc).__name__}: {exc}"), indent=2)+"\n")
        raise
    result = dict(status="complete", estimate=estimate, elapsed_s=time.monotonic()-started,
                  peak_rss_platform_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, epoch=record)
    capture_path.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
