"""Join campaign shards to an explicitly selected, pinned pre-solve inventory.

A campaign can publish a partial allocation. Selection reports that coverage and
reads only allocation/campaign metadata; neither checker nor solver outcomes can
change which available members are selected. Missing shards are never replaced
by policy-consistent requests outside the frozen allocation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from eval.splits import canonical_spec, load_inventory, spec_key
from eval.snapshot_io import read_snapshot


@dataclass(frozen=True)
class FrozenSelection:
    rows: list[dict]
    split_version: str
    split_entries_sha256: str
    split_policy: str
    allocated_count: int
    available_count: int
    allocated_rows: list[dict] = field(default_factory=list)


def normalize_spec_keys(spec_keys):
    """Treat explicit selection as a nonempty set, sorted for stable run identity."""
    if spec_keys is None:
        return None
    if isinstance(spec_keys, (str, bytes)):
        raise ValueError("spec_keys must be a nonempty iterable of specification keys")
    try:
        keys = list(spec_keys)
    except TypeError as exc:
        raise ValueError("spec_keys must be a nonempty iterable of specification keys") from exc
    if not keys or any(not isinstance(key, str) or not key for key in keys):
        raise ValueError("spec_keys must contain nonempty string keys")
    return sorted(set(keys))


def select_frozen_instances(instances_dir, *, split_manifest, split="val",
                            rungs=("tiny", "small"), limits=None, regen_manifest=None,
                            allow_diagnostic=False, spec_keys=None):
    """Select available exact members of an explicitly pinned release.

    The stable per-rung spec-key prefix is taken from available campaign members.
    This permits evaluation of a published subset without pretending all frozen
    requests were generated. ``allocated_count`` and ``available_count`` expose
    this distinction. Callers must retain the pin and coverage in run artifacts.
    Claims and paths are validated before opening any selected instance files.
    Explicit ``spec_keys`` replace prefix limits and must belong to this frozen
    split/rung allocation; unavailable requested members remain unattempted.
    """
    if split_manifest is None:
        raise ValueError("split_manifest must explicitly select a pinned release")
    if split not in ("train", "val", "test"):
        raise ValueError("split must be train, val or test")
    rungs = tuple(rungs)
    if not rungs or set(rungs) - {"tiny", "small", "medium", "full"}:
        raise ValueError("rungs must contain tiny/small/medium/full")
    if limits and (set(limits) - set(rungs)
                   or any(type(v) is not int or v < 1 for v in limits.values())):
        raise ValueError("limits must be positive counts for selected rungs")
    inventory = load_inventory(split_manifest)
    frozen = inventory.manifest
    spec_keys = normalize_spec_keys(spec_keys)
    allocated = [entry for entry in frozen["entries"]
                 if entry["split"] == split and entry["spec"]["rung"] in rungs]
    if spec_keys is not None:
        unknown = set(spec_keys) - {spec_key(entry["spec"]) for entry in allocated}
        if unknown:
            raise ValueError("spec_keys outside the requested frozen allocation: " + ", ".join(sorted(unknown)))
    policy = frozen["policy"]["hash_salt"]
    if instances_dir is None:
        raise ValueError("instances_dir must explicitly select an instance root")
    root = Path(instances_dir).resolve()
    manifest_path = Path(regen_manifest) if regen_manifest is not None else root / "manifest.json"
    release_path = root / "release.json"
    manifest_snapshot = read_snapshot(manifest_path)
    if release_path.exists():
        release = read_snapshot(release_path).json()
        if release.get("manifest_sha256") != manifest_snapshot.sha256:
            raise ValueError("instance release manifest checksum mismatch")
    manifest = manifest_snapshot.json()
    from eval.splits_epoch import is_epoch_manifest, validate_epoch_manifest, validate_epoch_instance
    epoch = regen_manifest is not None or is_epoch_manifest(root, manifest)
    if epoch:
        validate_epoch_manifest(root, manifest, inventory, allow_diagnostic=allow_diagnostic,
                                check_source=not (allow_diagnostic and manifest.get("diagnostic")))
    available = {}
    for group_name, group in sorted(manifest["groups"].items()):
        for source in group["shards"]:
            spec = source.get("split_spec")
            if not spec:
                raise ValueError("campaign shard lacks frozen split specification")
            if spec.get("rung") not in rungs:
                continue
            if source.get("split") not in ("train", "val", "test"):
                raise ValueError("campaign shard lacks a valid claimed split")
            entry = inventory.require_membership(spec, split=source["split"])
            key = spec_key(spec)
            frozen_key = spec_key(entry["spec"])
            if (source.get("spec_key") != key or source.get("split_policy") != policy
                    or source.get("split_version") != inventory.version
                    or ("split_entries_sha256" in source and
                        source["split_entries_sha256"] != inventory.entries_sha256)):
                raise ValueError("campaign contains inconsistent frozen split metadata")
            if frozen_key in available:
                raise ValueError("duplicate instance specification")
            path = (root / group_name / source["instance"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("instance path escapes campaign")
            available[frozen_key] = {"path": str(path), "spec_key": frozen_key,
                              "source_spec_key": key, "split": entry["split"],
                              "test_family": entry.get("test_family") or "in_distribution",
                              "rung": spec["rung"], "spec": canonical_spec(spec),
                              "input_sha256": source["sha256"],
                              "split_version": inventory.version,
                              "split_entries_sha256": inventory.entries_sha256}
            if epoch:
                available[frozen_key].update(epoch_id=manifest["epoch_id"],
                                      source_sha256=manifest["source_sha256"],
                                      regen_manifest_path=str(manifest_path.resolve()),
                                      regen_manifest_sha256=manifest_snapshot.sha256,
                                      diagnostic=manifest["diagnostic"])
    # The independently pinned list drives the join, not source manifest order
    # or hash-policy eligibility. A valid policy label alone never admits a row.
    rows = [available[key] for entry in allocated
            if (key := spec_key(entry["spec"])) in available]
    if spec_keys is not None:
        selected = [available[key] for key in spec_keys if key in available]
    else:
        selected = []
        for rung in dict.fromkeys(rungs):
            candidates = sorted((row for row in rows if row["rung"] == rung),
                                key=lambda row: row["spec_key"])
            count = (limits or {}).get(rung, len(candidates))
            if count > len(candidates):
                raise ValueError(f"requested {count} {rung} {split} instances; "
                                 f"only {len(candidates)} available frozen members")
            selected.extend(candidates[:count])
    if not allocated:
        raise ValueError("no instances match the requested frozen split and rungs")
    rejected = {}
    for group_name, group in manifest["groups"].items():
        for source in group.get("rejected_shards", []):
            spec = source.get("spec", source.get("split_spec"))
            if not spec or spec.get("rung") not in rungs:
                continue
            entry = inventory.require_membership(spec, split=source.get("split"))
            key = spec_key(spec)
            frozen_key = spec_key(entry["spec"])
            if source.get("spec_key") != key or frozen_key in rejected or frozen_key in available:
                raise ValueError("inconsistent or duplicate rejected allocation")
            path = (root / group_name / source["rejection"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("rejection path escapes campaign")
            read_snapshot(path, expected_sha256=source["sha256"])
            rejected[frozen_key] = {"rejection_path": str(path), "rejection_sha256": source["sha256"]}
    allocated_rows = [{"spec_key": spec_key(entry["spec"]), "split": split,
                       "split_version": inventory.version,
                       "split_entries_sha256": inventory.entries_sha256,
                       "rung": entry["spec"]["rung"], "spec": entry["spec"],
                       "test_family": entry.get("test_family") or "in_distribution",
                       "emitted": spec_key(entry["spec"]) in available,
                       "feasibility_status": ("generation-rejected" if
                           spec_key(entry["spec"]) in rejected else "unknown"),
                       "campaign_manifest_path": str(manifest_path.resolve()),
                       "campaign_manifest_sha256": manifest_snapshot.sha256,
                       **rejected.get(spec_key(entry["spec"]), {})}
                      for entry in allocated]
    if epoch:
        for row in allocated_rows:
            row.update(epoch_id=manifest["epoch_id"], source_sha256=manifest["source_sha256"],
                       regen_manifest_path=str(manifest_path.resolve()),
                       regen_manifest_sha256=manifest_snapshot.sha256)
    if epoch:
        for row in selected:
            validate_epoch_instance(row["path"], row)
    return FrozenSelection(selected, inventory.version, inventory.entries_sha256,
                           policy, len(allocated), len(rows), allocated_rows)
