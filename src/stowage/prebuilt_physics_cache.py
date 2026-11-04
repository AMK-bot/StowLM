"""Explicit local packaging of existing numerical-cache bytes and provenance.

This tool never constructs, loads, or numerically qualifies a table. Existing
native checker certificates remain authoritative and may reject copied files.
The bundle is local derived geometry, not a redistribution permission. Raw KCS
must remain checksum-valid during export/install, including existing-cache hits.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import tomllib

if __name__ == "__main__":
    # Set native caps before project imports can load numpy/scipy. Library
    # callers retain responsibility for their own process admission/caps.
    for _thread_key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[_thread_key] = "1"

from stowage.params import repo_root
from stowage.prebuilt_cache import PRODUCTION_SHIPS, runtime_identity
from stowage.vessel.fleet import _parameter_bytes
from stowage.vessel.hull_offsets import verified_offset_bytes

FORMAT = "stowlm-local-physics-cache-v1"
MANIFEST = "physics-manifest.json"
MAX_FILES = 10000
MAX_FILE_BYTES = 2 * 1024**3
MAX_TOTAL_BYTES = 2 * 1024**3
MAX_MANIFEST_BYTES = 4 * 1024**2
CHUNK_BYTES = 1024**2
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_KEY = r"[0-9a-f]{64}(?:-[0-9a-f]{16})?"
_NAME = re.compile(
    rf"(?:(?:{_KEY}|(?:liquid(?:-raw)?|sounding|geometry)-{_KEY})\.npz"
    rf"|{_KEY}\.certificate\.json|liquid-{_KEY}\.qualified\.json"
    r"|liquid-vessel-[0-9a-f]{64}\.json)\Z"
)


class PhysicsCacheError(ValueError):
    """A local numerical-cache bundle failed structural/integrity checks."""


def _asset_identity() -> dict:
    result = {}
    for name in PRODUCTION_SHIPS:
        params = tomllib.loads(_parameter_bytes(name).decode("utf-8"))
        form = params.get("hull_form", {})
        if form.get("family") != "stowlm_offsets_v1":
            raise PhysicsCacheError(f"{name}: production checksum-pinned offsets required")
        raw = verified_offset_bytes(form["offsets_path"], expected_sha256=form["offsets_sha256"])
        result[name] = {"offsets_sha256": sha256(raw).hexdigest()}
    return result


@contextmanager
def _regular(path: Path):
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise PhysicsCacheError(f"not a regular cache member: {path.name}")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
                raise PhysicsCacheError(f"not a regular cache member: {path.name}")
            yield stream, metadata
    except OSError as exc:
        raise PhysicsCacheError(f"cannot read cache member {path.name}: {exc}") from exc


def _stream(path: Path, *, destination: Path | None = None, limit=MAX_FILE_BYTES, allow_empty=False) -> dict:
    """Hash/copy inert bytes in bounded chunks, detecting ordinary file mutation."""
    digest, count = sha256(), 0
    with _regular(path) as (source, before):
        if not (0 if allow_empty else 1) <= before.st_size <= limit:
            raise PhysicsCacheError(f"cache member size outside bounds: {path.name}")
        sink = destination.open("xb") if destination is not None else None
        try:
            while chunk := source.read(CHUNK_BYTES):
                count += len(chunk)
                if count > limit:
                    raise PhysicsCacheError(f"cache member exceeds size bound: {path.name}")
                digest.update(chunk)
                if sink is not None:
                    sink.write(chunk)
            if sink is not None:
                sink.flush()
                os.fsync(sink.fileno())
            after = os.fstat(source.fileno())
            current = path.lstat()
            signature = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
            if signature(before) != signature(after) or signature(after) != signature(current) or count != before.st_size:
                raise PhysicsCacheError(f"cache member changed during read: {path.name}")
        finally:
            if sink is not None:
                sink.close()
    return {"filename": path.name, "size_bytes": count, "sha256": digest.hexdigest(), "mtime_ns": before.st_mtime_ns}


def _source_identity() -> dict:
    """Pin actual checker, vessel, schema and parameter source bytes."""
    root = repo_root()
    paths = set()
    for prefix in ("src/stowage/checker", "src/stowage/vessel", "src/stowage/model", "params"):
        directory = root / prefix
        if not directory.is_dir():
            raise PhysicsCacheError(f"missing construction source directory: {prefix}")
        paths.update(path for path in directory.rglob("*") if path.suffix in {".py", ".json", ".toml"} and path.is_file())
    for relative in ("src/stowage/conventions.py", "src/stowage/params.py", "src/stowage/versions.py",
                     "src/stowage/rules/catalogue.py", "src/stowage/toml_cache.py"):
        path = root / relative
        if path.is_file():
            paths.add(path)
    if not paths:
        raise PhysicsCacheError("no construction source identity available")
    return {path.relative_to(root).as_posix(): _stream(path, allow_empty=True)["sha256"] for path in sorted(paths)}


def _identity() -> dict:
    return {"runtime": runtime_identity(), "sources": _source_identity(), "raw_assets": _asset_identity()}


def _separate(first: Path, second: Path) -> None:
    a, b = first.resolve(), second.resolve()
    if a == b or a.is_relative_to(b) or b.is_relative_to(a):
        raise PhysicsCacheError("source/bundle/target directories must be separate, without nesting")


def _manifest_bytes(value: dict) -> bytes:
    payload = json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    if len(payload) > MAX_MANIFEST_BYTES:
        raise PhysicsCacheError("outer manifest exceeds size bound")
    return payload


def _validate_entries(entries) -> list[dict]:
    if not isinstance(entries, list) or not 0 < len(entries) <= MAX_FILES:
        raise PhysicsCacheError("outer manifest has invalid file count")
    names, total = set(), 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"filename", "size_bytes", "sha256", "mtime_ns"}:
            raise PhysicsCacheError("malformed member declaration")
        name, size, digest, mtime = (entry[key] for key in ("filename", "size_bytes", "sha256", "mtime_ns"))
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in names:
            raise PhysicsCacheError("unsupported, duplicate or unsafe cache filename")
        if type(size) is not int or not 0 < size <= MAX_FILE_BYTES:
            raise PhysicsCacheError(f"invalid size for {name}")
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise PhysicsCacheError(f"invalid digest for {name}")
        if type(mtime) is not int or not 0 <= mtime < 2**63:
            raise PhysicsCacheError(f"invalid timestamp for {name}")
        names.add(name)
        total += size
        if total > MAX_TOTAL_BYTES:
            raise PhysicsCacheError("total numerical-cache bytes exceed bound")
    return entries


def export(*, source_cache: Path, output: Path) -> dict:
    """Snapshot approved-name regular root files; no native table is opened."""
    source_cache, output = Path(source_cache), Path(output)
    _separate(source_cache, output)
    if not source_cache.is_dir():
        raise PhysicsCacheError("source cache directory is absent")
    identity = _identity()  # Raw offsets must exist even when export is reusable.
    # Retain source enumeration order and mtimes: native selectors sort by mtime.
    members = [path for path in source_cache.iterdir() if _NAME.fullmatch(path.name)]
    if not 0 < len(members) <= MAX_FILES:
        raise PhysicsCacheError("no supported root cache files, or too many files")
    entries = []
    total = 0
    # Validate all bytes before writing the requested bundle directory.
    for path in members:
        entry = _stream(path)
        total += entry["size_bytes"]
        if total > MAX_TOTAL_BYTES:
            raise PhysicsCacheError("total numerical-cache bytes exceed bound")
        entries.append(entry)
    _validate_entries(entries)
    manifest = {"format": FORMAT, "identity": identity, "entries": entries,
                "qualification": "Byte packaging only; native certificates/dispatch remain authoritative",
                "provenance": "Local derived geometry; redistribution rights not established"}
    manifest_payload = _manifest_bytes(manifest)
    _publish(source_cache, output, entries, manifest_payload, identity, exported=True)
    return manifest


def _publish(source: Path, target: Path, entries: list[dict], manifest_payload: bytes | None,
             identity: dict, *, exported=False) -> None:
    """Stage *all* validated copies before replacing any destination member."""
    def validate_destinations():
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise PhysicsCacheError("destination must be a nonsymlink directory")
        names = [entry["filename"] for entry in entries] + ([MANIFEST] if exported else [])
        for name in names:
            path = target / name
            if path.is_symlink() or (path.exists() and not stat.S_ISREG(path.lstat().st_mode)):
                raise PhysicsCacheError(f"refusing nonregular/symlink destination: {name}")
    validate_destinations()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".physics-stage-") as temporary:
        staging = Path(temporary)
        for entry in entries:
            staged = staging / entry["filename"]
            copied = _stream(source / entry["filename"], destination=staged)
            comparisons = entry if exported else {key: value for key, value in entry.items() if key != "mtime_ns"}
            if any(copied[key] != value for key, value in comparisons.items()):
                raise PhysicsCacheError(f"cache member changed after validation: {entry['filename']}")
            os.utime(staged, ns=(entry["mtime_ns"], entry["mtime_ns"]))
        if _identity() != identity:
            raise PhysicsCacheError("source/runtime/raw-asset identity changed; retry after freeze")
        if exported and [path.name for path in source.iterdir() if _NAME.fullmatch(path.name)] != [entry["filename"] for entry in entries]:
            raise PhysicsCacheError("source cache inventory changed during export")
        # Check every destination before the first write, including late changes.
        validate_destinations()
        target.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            os.replace(staging / entry["filename"], target / entry["filename"])
        if manifest_payload is not None:
            staged_manifest = staging / MANIFEST
            staged_manifest.write_bytes(manifest_payload)
            os.replace(staged_manifest, target / MANIFEST)


def install(*, bundle: Path, target_cache: Path) -> dict:
    """Verify all identity/file bytes, then install unchanged regular root files."""
    bundle, target_cache = Path(bundle), Path(target_cache)
    _separate(bundle, target_cache)
    with _regular(bundle / MANIFEST) as (stream, metadata):
        if not 0 < metadata.st_size <= MAX_MANIFEST_BYTES:
            raise PhysicsCacheError("outer manifest exceeds size bound")
        payload = stream.read(MAX_MANIFEST_BYTES + 1)
    try:
        manifest = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise PhysicsCacheError("malformed outer manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise PhysicsCacheError("unsupported outer manifest format")
    entries = _validate_entries(manifest.get("entries"))
    identity = _identity()  # Unconditionally validates all pinned raw offsets.
    if manifest.get("identity") != identity:
        raise PhysicsCacheError("stale source/runtime/raw-asset identity; export again on this installation")
    for entry in entries:
        actual = _stream(bundle / entry["filename"])
        if any(actual[key] != entry[key] for key in ("filename", "size_bytes", "sha256")):
            raise PhysicsCacheError(f"cache member hash/size mismatch: {entry['filename']}")
    _publish(bundle, target_cache, entries, None, identity)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    outgoing = commands.add_parser("export")
    outgoing.add_argument("--source-cache", type=Path, required=True)
    outgoing.add_argument("--output", type=Path, required=True)
    incoming = commands.add_parser("install")
    incoming.add_argument("--bundle", type=Path, required=True)
    incoming.add_argument("--target-cache", type=Path, required=True)
    args = parser.parse_args(argv)
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    try:
        result = (export(source_cache=args.source_cache, output=args.output) if args.command == "export"
                  else install(bundle=args.bundle, target_cache=args.target_cache))
    except (ValueError, OSError, KeyError) as exc:
        print(f"physics cache: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"command": args.command, "files": len(result["entries"]),
                      "bytes": sum(entry["size_bytes"] for entry in result["entries"]),
                      "integrity_verified": True, "numerical_qualification": "native checker required"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
