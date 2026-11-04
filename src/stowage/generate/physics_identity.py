"""Current physical inputs shared by background acceptance and shard resume.

This is an input binding, never a checker verdict. External hull offsets are
read and checksum-validated on EVERY call, including memo and disk resume hits.
Numerical artifacts are content-hashed with a bounded metadata digest cache;
replacements, permissions, ordering and qualification bytes remain
part of the identity. A changed or unavailable binding cannot certify old work.
"""
from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
from functools import lru_cache
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import sys
import tomllib

from stowage.params import repo_root


class PhysicsInputError(ValueError):
    """A mandatory current physical input cannot be validated."""


_ARTIFACT_HASHES: OrderedDict[tuple, str] = OrderedDict()
_ARTIFACT_HASH_LIMIT = 262144


def _signature(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns,
            stat.st_ctime_ns, stat.st_mode)


def _artifact_identity(path: Path, *, observed_stat=None) -> dict:
    """Reuse content hashes only while inode, size, times and permissions agree.

    Unlike the mandatory hull pin, these are optional acceleration artifacts.
    A metadata change forces a fresh read; metadata includes ctime so restoring
    an old mtime cannot hide a rewrite or permission change.
    """
    try:
        before = observed_stat if observed_stat is not None else path.stat()
        signature = _signature(before)
        key = (str(path), *signature)
        digest = _ARTIFACT_HASHES.get(key)
        if digest is not None:
            _ARTIFACT_HASHES.move_to_end(key)
            return dict(path=str(path), sha256=digest, identity=list(signature))
        with path.open("rb") as stream:
            if _signature(os.fstat(stream.fileno())) != signature:
                raise PhysicsInputError(f"physical artifact changed while opening: {path}")
            if digest is None:
                checksum = sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(block)
                digest = checksum.hexdigest()
            else:
                stream.read(1)
            if _signature(os.fstat(stream.fileno())) != signature:
                raise PhysicsInputError(f"physical artifact changed while reading: {path}")
        if _signature(path.stat()) != signature:
            raise PhysicsInputError(f"physical artifact replaced while reading: {path}")
        _ARTIFACT_HASHES[key] = digest
        _ARTIFACT_HASHES.move_to_end(key)
        while len(_ARTIFACT_HASHES) > _ARTIFACT_HASH_LIMIT:
            _ARTIFACT_HASHES.popitem(last=False)
        return dict(path=str(path), sha256=digest, identity=list(signature))
    except OSError as error:
        # Optional numerical cache misses can select exact fallback paths. Bind
        # their unavailability instead of reusing an older available identity.
        return dict(path=str(path), unavailable=type(error).__name__)


def _artifact_directories(root: Path) -> list[Path]:
    from stowage.checker import stability_tables, stability_liquid_tables, stability_liquid

    bound = stability_tables.cached_geometry.__kwdefaults__["cache_dir"]
    # These qualified tables can participate in a public-checker verdict.
    # linear/cache's per-instance matrices are derived outputs, not physics
    # inputs: load_payload verifies their checksums and regenerates on a miss,
    # and every finisher candidate still requires the full public checker.
    # Binding that growing output directory would invalidate a witness merely
    # because another instance was generated. Linear implementation, rules and
    # qualification evidence ARE bound below, including for C1 campaigns.
    return sorted({Path(bound).resolve(),
                   Path(stability_liquid.CACHE_DIRECTORY).resolve(),
                   Path(stability_liquid_tables.CACHE_DIRECTORY).resolve()})


def _artifact_manifest(root: Path) -> list[dict]:
    result = []
    for directory in _artifact_directories(root):
        # Preserve directory iteration order: table dispatch also uses ordering
        # when selecting overlapping tables with equal modification times.
        checksum = sha256()
        count = 0
        def scan(folder):
            nonlocal count
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        scan(entry.path)
                    elif Path(entry.name).suffix in {".npz", ".json", ".toml"}:
                        item = _artifact_identity(Path(entry.path), observed_stat=entry.stat())
                        checksum.update(json.dumps(item, sort_keys=True,
                            separators=(",", ":")).encode())
                        checksum.update(b"\n")
                        count += 1
        if directory.is_dir():
            scan(directory)
        result.append(dict(directory=str(directory), exists=directory.is_dir(),
                           entries=count, contents_sha256=checksum.hexdigest()))
    return result


@lru_cache(maxsize=16)
def _hull_pin_fields(raw: bytes):
    """Cache parsing by freshly read contents, never source validation."""
    form = tomllib.loads(raw.decode("utf-8")).get("hull_form", {})
    if form.get("family") != "stowlm_offsets_v1":
        return None
    return form["offsets_path"], form["offsets_sha256"]


def _external_hulls(root: Path, fleet_sources: dict[Path, bytes]) -> list[dict]:
    from stowage.vessel.hull_offsets import verified_offset_bytes

    pins = {}
    for owner, raw in fleet_sources.items():
        try:
            fields = _hull_pin_fields(raw)
            if fields is None:
                continue
            name, expected = fields
            path = Path(name)
            path = path if path.is_absolute() else root / path
            # Do not memoize this call. It mirrors the public checker's pinned
            # source read even when an earlier acceptance was cached.
            verified = verified_offset_bytes(path, expected_sha256=expected)
            pins[(str(path.resolve()), expected)] = dict(
                path=str(path.resolve()), expected_sha256=expected,
                sha256=sha256(verified).hexdigest())
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise PhysicsInputError(f"pinned hull source invalid ({owner.name}): {error}") from error
    return [pins[key] for key in sorted(pins)]


def _referenced_evidence(root: Path, documents: list[bytes]) -> list[dict]:
    """Bind external qualification records, including missing legacy evidence.

    The checker/linear owner decides which certificate applies. Hashing unused
    legacy pins is deliberately conservative; this module does not approve or
    revoke their qualifications. Missing bytes still invalidate an old binding.
    """
    paths = set()
    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get("path"), str) and isinstance(value.get("sha256"), str):
                path = Path(value["path"])
                paths.add(path if path.is_absolute() else root / path)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    for raw in documents:
        try:
            visit(json.loads(raw))
        except (ValueError, UnicodeError):
            # Malformed rule/qualification bytes remain in the source hash;
            # the actual owner fails closed when that document is consumed.
            continue
    return [_artifact_identity(path) for path in sorted(paths)]


def current_physics_identity() -> dict:
    """Return the complete current binding, or raise for invalid hull sources.

    Includes the ballast finisher and linear dependencies even for a C1-only
    generator: the mandatory ballast finisher may use its linear search path.
    C2 configuration is separately bound by the campaign's requested config.
    """
    from stowage.versions import component_versions
    from stowage.vessel.fleet_conditions import CONDITION_VERSION

    root = repo_root()
    package = Path(__file__).resolve().parents[1]
    try:
        paths = {path for directory in (package, root / "params")
                 for path in directory.rglob("*")
                 if path.is_file() and path.suffix in {".py", ".json", ".toml"}}
        sources, fleet, documents = {}, {}, []
        for path in sorted(paths):
            raw = path.read_bytes()
            sources[str(path.relative_to(root))] = sha256(raw).hexdigest()
            if path.parent == root / "params/fleet" and path.suffix == ".toml":
                fleet[path] = raw
            if path.suffix == ".json":
                documents.append(raw)
        if not fleet:
            raise PhysicsInputError("no canonical fleet parameter sources")
        payload = dict(format="generator-physics-inputs-v1", sources=sources,
            components=component_versions(), condition_version=CONDITION_VERSION,
            dependencies={name: version(name) for name in ("numpy", "scipy", "pydantic", "ortools")},
            runtime=dict(python=sys.version, executable=sys.executable,
                         platform=platform.platform(), cwd=str(Path.cwd().resolve())),
            environment={key: value for key, value in os.environ.items()
                         if key.startswith("STOWAGE_")},
            pinned_sources=_external_hulls(root, fleet),
            artifacts=_artifact_manifest(root),
            referenced_qualification=_referenced_evidence(root, documents))
        payload["sha256"] = sha256(json.dumps(payload, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        return payload
    except PhysicsInputError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise PhysicsInputError(f"current physics input identity unavailable: {error}") from error
