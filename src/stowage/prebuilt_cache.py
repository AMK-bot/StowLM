"""Explicit preparation and verified installation of local vessel JSON caches.

This module does not replace any fleet/checker provider. It uses the unchanged
public builder and its existing exact content-addressed cache filenames. Run
after ``stowage.data_setup``; raw offsets remain mandatory even on cache hits.
Bundles contain derived geometry and are local preparation artifacts: creating
one does not establish redistribution rights. No pickle or executable content
is read. A manifest checksum establishes integrity, not publisher authenticity.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys
import tempfile
import tomllib

# Set CLI thread limits before project imports: importing the vessel package
# itself can import NumPy, before main() has a chance to run.
if __name__ == "__main__":
    for _thread_key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                        "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[_thread_key] = "1"

from stowage.model import Vessel
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.vessel import fleet_cache
from stowage.vessel.fleet import _parameter_bytes, list_fleet, load_vessel
from stowage.vessel.hull_offsets import verified_offset_bytes

FORMAT = "stowlm-local-full-vessel-cache-v1"
PRODUCTION_SHIPS = ("feeder_500", "feeder_800", "feeder_1700")
MANIFEST = "manifest.json"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PAYLOAD_BYTES = 128 * 1024 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class PrebuiltCacheError(ValueError):
    """A local cache bundle cannot be qualified against this installation."""


def runtime_identity() -> dict:
    """Qualify numerical package builds and the Python/platform that made JSON."""
    import numpy as np
    import scipy

    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "compiler": platform.python_compiler(),
        "system": platform.system(),
        "machine": platform.machine(),
        "byteorder": sys.byteorder,
        "dependencies": {name: version(name) for name in ("numpy", "scipy", "pydantic")},
        "numpy_build": np.show_config(mode="dicts"),
        "scipy_build": scipy.show_config(mode="dicts"),
    }


def _ships(names) -> list[str]:
    names = list(names)
    if not names or any(not isinstance(name, str) or name not in PRODUCTION_SHIPS or name not in list_fleet() for name in names):
        raise PrebuiltCacheError(f"ships must be explicit canonical names from {PRODUCTION_SHIPS}")
    return list(dict.fromkeys(names))


def _request(name: str) -> dict:
    """Read actual current inputs and verify raw geometry before any cache hit."""
    payload = _parameter_bytes(name)
    params = tomllib.loads(payload.decode("utf-8"))
    form = params.get("hull_form", {})
    if form.get("family") != "stowlm_offsets_v1":
        raise PrebuiltCacheError(f"{name}: requires the production checksum-pinned offsets family")
    raw = verified_offset_bytes(form["offsets_path"], expected_sha256=form["offsets_sha256"])
    slice_name = "full.rule-freeze-v1" if rule_freeze_v1_enabled() else "full"
    key = fleet_cache._cache_key(name, slice_name, True, payload)
    return {
        "ship": name, "slice": slice_name, "with_hull": True,
        "construction_key": key,
        "parameter_sha256": sha256(payload).hexdigest(),
        "offsets_sha256": sha256(raw).hexdigest(),
        "filename": f"{name}.{slice_name}.hull.{key}.json",
    }


def _encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n"


def _read_regular(path: Path, limit: int) -> bytes:
    """Bound reads and refuse symlink members rather than following them."""
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise PrebuiltCacheError(f"not a regular bundle file: {path.name}")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise PrebuiltCacheError(f"not a regular bundle file: {path.name}")
            if metadata.st_size <= 0 or metadata.st_size > limit:
                raise PrebuiltCacheError(f"bundle file size outside permitted bounds: {path.name}")
            data = stream.read(limit + 1)
    except OSError as exc:
        raise PrebuiltCacheError(f"cannot read bundle file {path.name}: {exc}") from exc
    if len(data) > limit:
        raise PrebuiltCacheError(f"bundle file exceeds permitted bounds: {path.name}")
    return data


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".prebuilt-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate_destinations(directory: Path, names) -> None:
    """Reject all predictable destination-type failures before publication."""
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise PrebuiltCacheError("destination must be a nonsymlink directory")
    for name in names:
        path = directory / name
        if path.is_symlink() or (path.exists() and not stat.S_ISREG(path.lstat().st_mode)):
            raise PrebuiltCacheError(f"refusing nonregular/symlink destination: {name}")


@contextmanager
def _using_cache(directory: Path):
    keys = ("STOWAGE_VESSEL_CACHE_DIR", "STOWAGE_VESSEL_CACHE")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ[keys[0]] = str(directory.resolve())
    os.environ[keys[1]] = "1"
    fleet_cache.clear_vessel_memo()
    try:
        yield
    finally:
        fleet_cache.clear_vessel_memo()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def prepare(ships, *, output: Path, cache_dir: Path) -> dict:
    """Sequentially prepare current full ships and export unchanged JSON bytes.

    Calls the ordinary cached builder; existing entries are reusable. This is
    not an independent uncached numerical qualification. It temporarily selects
    the explicit cache root and is intended as a standalone setup command, not
    a thread-safe runtime hook.
    """
    names = _ships(ships)
    output, cache_dir = Path(output), Path(cache_dir)
    before = {name: _request(name) for name in names}
    identity = runtime_identity()
    prepared = []
    with _using_cache(cache_dir):
        for name in names:
            vessel = load_vessel(name, with_hull=True)
            if vessel.id != name or vessel.slice_of is not None or vessel.hull is None:
                raise PrebuiltCacheError(f"{name}: builder did not return the requested full physical vessel")
            payload = vessel.model_dump_json().encode("utf-8")
            if not payload or len(payload) > MAX_PAYLOAD_BYTES:
                raise PrebuiltCacheError(f"{name}: cache payload exceeds permitted bounds")
            if _request(name) != before[name]:
                raise PrebuiltCacheError(f"{name}: construction inputs changed during preparation; retry after freeze")
            prepared.append((dict(before[name], size_bytes=len(payload), sha256=sha256(payload).hexdigest()), payload))
    # Qualify every entry again before publishing any bundle member/manifest.
    if identity != runtime_identity() or any(_request(name) != before[name] for name in names):
        raise PrebuiltCacheError("construction identity changed during preparation; retry after freeze")
    manifest = {
        "format": FORMAT, "runtime": identity,
        "provenance": "Local derived geometry; raw KCS required; redistribution rights not established",
        "entries": [entry for entry, _ in prepared],
    }
    _validate_destinations(output, [entry["filename"] for entry, _ in prepared] + [MANIFEST])
    for entry, payload in prepared:
        _atomic_write(output / entry["filename"], payload)
    _atomic_write(output / MANIFEST, _encoded(manifest))
    return manifest


def install(*, bundle: Path, cache_dir: Path) -> dict:
    """Validate the entire bundle before writing any existing-cache entry.

    A stale bundle is rejected clearly. Regenerate it with ``prepare`` against
    current inputs; no nearest-key fallback or automatic physics build occurs.
    Payload hashes are local integrity guards, not trusted signatures.
    """
    bundle, cache_dir = Path(bundle), Path(cache_dir)
    try:
        manifest = json.loads(_read_regular(bundle / MANIFEST, MAX_MANIFEST_BYTES))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PrebuiltCacheError("malformed bundle manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise PrebuiltCacheError("unsupported bundle manifest format")
    if manifest.get("runtime") != runtime_identity():
        raise PrebuiltCacheError("bundle runtime/build identity differs; run prepare on this installation")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not 1 <= len(entries) <= len(PRODUCTION_SHIPS):
        raise PrebuiltCacheError("bundle must contain one to three explicit full-ship entries")
    names = [entry.get("ship") if isinstance(entry, dict) else None for entry in entries]
    if len(set(name for name in names if isinstance(name, str))) != len(names):
        raise PrebuiltCacheError("bundle has duplicate or malformed ship entries")
    _ships(names)
    requests = {name: _request(name) for name in names}  # Raw KCS is mandatory.
    checked = []
    fields = set(next(iter(requests.values()))) | {"size_bytes", "sha256"}
    for entry in entries:
        name = entry["ship"]
        request = requests[name]
        if (set(entry) != fields or any(type(entry.get(key)) is not type(value) or entry.get(key) != value
                                        for key, value in request.items())):
            raise PrebuiltCacheError(f"{name}: stale or malformed construction identity; run prepare again")
        size, digest = entry["size_bytes"], entry["sha256"]
        if (type(size) is not int or not 0 < size <= MAX_PAYLOAD_BYTES
                or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)):
            raise PrebuiltCacheError(f"{name}: invalid payload size or SHA-256")
        # filename must equal our current generated basename, never arbitrary input.
        payload = _read_regular(bundle / request["filename"], MAX_PAYLOAD_BYTES)
        if len(payload) != size or sha256(payload).hexdigest() != digest:
            raise PrebuiltCacheError(f"{name}: payload checksum/size mismatch")
        try:
            vessel = Vessel.model_validate_json(payload)
        except ValueError as exc:
            raise PrebuiltCacheError(f"{name}: invalid vessel JSON") from exc
        if vessel.id != name or vessel.slice_of is not None or vessel.hull is None:
            raise PrebuiltCacheError(f"{name}: payload is not the declared full physical vessel")
        checked.append((cache_dir / request["filename"], payload))
    if manifest["runtime"] != runtime_identity() or any(_request(name) != requests[name] for name in names):
        raise PrebuiltCacheError("construction identity changed during verification; retry after freeze")
    # Validation errors above preserve every existing good target and create no dir.
    _validate_destinations(cache_dir, [target.name for target, _ in checked])
    for target, payload in checked:
        _atomic_write(target, payload)
    fleet_cache.clear_vessel_memo()
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare", help="prepare local full-vessel cache bundle sequentially")
    build.add_argument("--ships", nargs="+", required=True, choices=PRODUCTION_SHIPS)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--cache-dir", type=Path, required=True)
    restore = commands.add_parser("install", help="verify and install an exact local cache bundle")
    restore.add_argument("--bundle", type=Path, required=True)
    restore.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    # CLI is sequential; numerical libraries import lazily in builders/runtime.
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    try:
        if args.command == "prepare":
            result = prepare(args.ships, output=args.output, cache_dir=args.cache_dir)
        else:
            result = install(bundle=args.bundle, cache_dir=args.cache_dir)
    except (ValueError, OSError, KeyError) as exc:
        print(f"prebuilt cache: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"command": args.command, "integrity_verified": True,
                      "numerical_qualification": "unchanged full checker required",
                      "ships": [entry["ship"] for entry in result["entries"]]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
