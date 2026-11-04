"""Bind a selected frozen release to immutable instance bytes and source.

The allocation's archived labels remain hash inputs. Vessel aliases are resolved
only when comparing the generated content, never when checking membership. This
helper is shared by harness, gold and SFT callers with explicit instance roots.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from pathlib import Path
import sys

from eval.splits import canonical_spec, spec_from_instance, spec_key
from eval.snapshot_io import read_snapshot
from stowage.conventions import canonical_vessel_id
from stowage.versions import component_versions

EPOCH_SCHEMA = "stowlm-epoch-instances-v1"
_SOURCE_ROOT = Path(__file__).resolve().parents[2]


def instance_matches_spec(instance, spec):
    """Compare complete generation inputs, resolving only the D33 ship alias."""
    actual, expected = canonical_spec(spec_from_instance(instance)), canonical_spec(spec)
    actual["vessel"] = canonical_vessel_id(actual["vessel"])
    expected["vessel"] = canonical_vessel_id(expected["vessel"])
    return actual == expected


def load_bound_instance(path, expected_sha256):
    """Hash and parse a single read, including the ordinary JSON schema check."""
    from stowage.model import Instance
    path = Path(path)
    snapshot = read_snapshot(path)
    if snapshot.sha256 != expected_sha256:
        raise ValueError(f"instance checksum mismatch: {path}")
    return snapshot.model(Instance)


def validate_epoch_instance(path, row):
    """Verify bytes and identity for a selected manifest-bound instance."""
    expected = row.get("input_sha256", row.get("sha256"))
    instance = load_bound_instance(path, expected)
    if not instance_matches_spec(instance, row.get("spec", row.get("split_spec"))):
        raise ValueError("instance identity disagrees with frozen split specification")
    return instance


def is_epoch_manifest(root, manifest):
    """Fail closed for epoch roots whose header was lost or edited."""
    return (Path(root).name.startswith("instances-e")
            or bool({"epoch_id", "source_sha256", "freeze_manifest"}.intersection(manifest))
            or manifest.get("schema_version") == EPOCH_SCHEMA)


def _source_snapshot(freeze):
    files, versions = freeze.get("files"), freeze.get("versions")
    if not isinstance(files, dict) or not files or not isinstance(versions, dict):
        raise ValueError("epoch freeze snapshot is missing files or versions")
    fingerprint = hashlib.sha256(json.dumps({"files": files, "versions": versions}, sort_keys=True).encode()).hexdigest()
    if fingerprint != freeze.get("source_sha256"):
        raise ValueError("epoch freeze snapshot fingerprint mismatch")
    for name, digest in files.items():
        path = (_SOURCE_ROOT / name).resolve()
        if not path.is_relative_to(_SOURCE_ROOT) or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("invalid epoch source path or checksum")
    return files


def validate_numerical_cache_snapshot(freeze):
    """Recheck the numerical inputs behind an epoch, including cache-only drift.

    This shared consumer validator has no dependency on experiment driver code.
    Mutable attempt additions are deliberately absent from the frozen base.
    """
    pin = freeze.get("numerical_caches")
    if (not isinstance(pin, dict) or not isinstance(pin.get("sources"), dict)
            or not isinstance(pin.get("files"), dict)
            or set(pin["sources"]) != {"stability", "vessels", "linear"}):
        raise ValueError("epoch numerical cache pin missing; create a new freeze")
    fingerprint = hashlib.sha256(json.dumps(
        {"sources": pin["sources"], "files": pin["files"]}, sort_keys=True).encode()).hexdigest()
    if fingerprint != pin.get("sha256"):
        raise ValueError("epoch numerical cache pin fingerprint mismatch")
    combined = hashlib.sha256((freeze["source_sha256"] + fingerprint).encode()).hexdigest()
    if combined != freeze.get("freeze_sha256"):
        raise ValueError("epoch source/numerical cache fingerprint mismatch")
    actual = {}
    for name, directory in sorted(pin["sources"].items()):
        root = Path(directory)
        if not root.exists():
            continue
        if root.is_symlink() or not root.is_dir():
            raise ValueError(f"invalid epoch numerical cache root: {root}")
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"epoch numerical cache symlink: {path}")
            if path.is_file():
                actual[f"{name}/{path.relative_to(root).as_posix()}"] = read_snapshot(path).sha256
    drift = [name for name in sorted(set(actual) | set(pin["files"]))
             if actual.get(name) != pin["files"].get(name)]
    if drift:
        raise ValueError("epoch numerical cache drift: " + ", ".join(drift))
    return fingerprint


def validate_active_numerical_cache_routes(freeze):
    """Require generic harness readers to use the cache roots frozen for them.

    Official generic evaluations require prewarmed caches. A new cache entry
    written to a frozen root fails the next content check; it is never silently
    admitted. Campaign adapters use independently verified attempt copies and
    therefore intentionally do not call this generic-reader route validator.
    """
    pin = freeze.get("numerical_caches")
    sources = pin.get("sources", {}) if isinstance(pin, dict) else {}
    from stowage.vessel import fleet_cache
    routes = [("vessels", "fleet_cache", fleet_cache._cache_dir())]
    modules = []
    for name, attribute, directory in (
        ("stowage.checker.stability_tables", "CACHE_DIR", "stability"),
        ("stowage.checker.stability_sounding", "CACHE_DIRECTORY", "stability"),
        ("stowage.checker.stability_liquid_tables", "CACHE_DIRECTORY", "stability"),
        ("stowage.checker.stability_liquid", "CACHE_DIRECTORY", "stability"),
        ("stowage.linear.cache", "_DEFAULT_ROOT", "linear"),
    ):
        module = importlib.import_module(name)
        modules.append((module, directory))
        routes.append((directory, name, getattr(module, attribute)))
    for directory, reader, path in routes:
        if directory not in sources or Path(path).resolve() != Path(sources[directory]).resolve():
            raise ValueError(f"numerical cache route differs from frozen {directory}: {reader}")
    # Some public readers capture their cache directory when defined. Checking
    # only a later module-global reassignment would leave that older route live.
    for module, directory in modules:
        expected = Path(sources[directory]).resolve()
        for item in vars(module).values():
            if not callable(item) or inspect.isclass(item) or getattr(item, "__module__", None) != module.__name__:
                continue
            for name, parameter in inspect.signature(item).parameters.items():
                if name in {"cache_dir", "cache_root"} and isinstance(parameter.default, Path):
                    if parameter.default.resolve() != expected:
                        raise ValueError(f"numerical cache route differs from frozen {directory}: {module.__name__}.{name}")
    # Long-lived harness invocations must not retain payloads read from an
    # earlier directory before the current frozen routes were admitted.
    fleet_cache.clear_vessel_memo()
    for name, module in list(sys.modules.items()):
        if not (name.startswith("stowage.checker.stability") or name == "stowage.linear.cache"):
            continue
        for attribute, item in list(vars(module).items()):
            clear = getattr(item, "cache_clear", None)
            if callable(clear):
                clear()
            elif attribute in {"_CACHE", "_CENTROID_CACHE", "_POLYNOMIAL_CACHE"} and isinstance(item, dict):
                item.clear()
    return pin["sources"]


def validate_epoch_manifest(root, manifest, inventory, *, allow_diagnostic=False, check_source=True):
    """Validate an epoch's source and complete spec-to-content binding metadata.

    Production always checks the frozen source and current component versions.
    A diagnostic caller can permit source drift; it must label downstream outputs
    unofficial. Legacy explicitly selected roots use the existing inventory path
    instead and are not silently promoted to frozen source epoch evidence.
    """
    root = Path(root).resolve()
    # A preexisting inventory object must not keep a superseded release usable.
    from eval.splits_inventory import _reject_superseded
    _reject_superseded(inventory.version)
    _reject_superseded(manifest.get("split_version"))
    if manifest.get("schema_version") != EPOCH_SCHEMA:
        raise ValueError("unsupported epoch instance manifest schema")
    diagnostic = manifest.get("diagnostic")
    if type(diagnostic) is not bool or diagnostic and not allow_diagnostic:
        raise ValueError("diagnostic instances cannot be used for official evidence")
    if not check_source and not (diagnostic and allow_diagnostic):
        raise ValueError("source checks may only be skipped for diagnostic instances")
    epoch = manifest.get("epoch_id")
    if not isinstance(epoch, str) or not epoch:
        raise ValueError("epoch instance manifest lacks epoch_id")
    if (inventory.version not in ("v2", "dg-stress-v3")
            or manifest.get("split_version") != inventory.version
            or manifest.get("split_entries_sha256") != inventory.entries_sha256):
        raise ValueError("epoch instances must bind the selected independently pinned allocation")
    freeze = manifest.get("freeze_manifest")
    if not isinstance(freeze, dict):
        raise ValueError("epoch instance manifest lacks freeze snapshot")
    files = _source_snapshot(freeze)
    validate_numerical_cache_snapshot(freeze)
    if (freeze.get("epoch_id") != epoch or freeze.get("diagnostic") != diagnostic
            or manifest.get("source_sha256") != freeze.get("source_sha256")):
        raise ValueError("epoch instance manifest disagrees with source freeze")
    versions = manifest.get("versions")
    if not isinstance(versions, dict) or not versions:
        raise ValueError("epoch instance manifest lacks component versions")
    if check_source:
        if versions != component_versions():
            raise ValueError("epoch component versions differ from current source")
        drift = [name for name, digest in files.items()
                 if not (_SOURCE_ROOT / name).is_file()
                 or read_snapshot(_SOURCE_ROOT / name).sha256 != digest]
        # Include newly introduced files under the frozen input paths too.
        actual = set()
        for name in freeze.get("paths", []):
            path = (_SOURCE_ROOT / name).resolve()
            if not path.is_relative_to(_SOURCE_ROOT):
                raise ValueError("epoch frozen input escapes source root")
            actual.update(str(p.relative_to(_SOURCE_ROOT)) for p in
                          (path.rglob("*") if path.is_dir() else [path]) if p.is_file())
        drift.extend(sorted(actual - files.keys()))
        if drift:
            raise ValueError("epoch source drift: " + ", ".join(sorted(set(drift))))
    bindings = manifest.get("bindings")
    if not isinstance(bindings, dict):
        raise ValueError("epoch instance manifest lacks spec-to-content bindings")
    seen = {}
    policy = inventory.manifest["policy"]["hash_salt"]
    for group_name, group in manifest.get("groups", {}).items():
        for row in group["shards"]:
            spec = row["split_spec"]
            inventory.require_membership(spec, split=row["split"])
            key = spec_key(spec)
            path = (root / group_name / row["instance"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("instance path escapes epoch root")
            if (row.get("spec_key") != key or row.get("split_policy") != policy
                    or row.get("split_version") != inventory.version
                    or row.get("split_entries_sha256") != inventory.entries_sha256):
                raise ValueError("epoch shard disagrees with frozen split metadata")
            binding = {"spec": canonical_spec(spec), "split": row["split"],
                       "instance": path.relative_to(root).as_posix(), "sha256": row["sha256"]}
            if key in seen:
                raise ValueError("duplicate epoch specification")
            seen[key] = binding
            if bindings.get(key) != binding:
                raise ValueError("epoch spec-to-content binding mismatch")
    if bindings != seen:
        raise ValueError("epoch manifest contains extra spec-to-content bindings")
