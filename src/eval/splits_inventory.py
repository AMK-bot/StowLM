"""Trusted release pins and exact membership lookup for frozen split inventories.

``split_of`` assigns a policy label to any request. Membership additionally proves
that the request was allocated by the selected, independently pinned release.
This module never generates instances, screens cargo, or reads solver outcomes.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

from eval.snapshot_io import read_snapshot

_ROOT = Path(__file__).resolve().parents[2]
_CANONICAL_POLICY_V1_JSON = '''{"hash_salt":"stowlm-splits-v1:20261027","hash_buckets":10000,"train_below":9000,"val_below":9500,"heldout_slice":{"vessel":"feeder_1700","slice":"medium"},"heldout_port":"us_west_coast","heldout_noise":0.8,"selection":"seed ascending; vessel, port, generator-setting product order"}'''
_CANONICAL_POLICY_V2_JSON = '''{"hash_salt":"stowlm-splits-v2:20261027","hash_buckets":10000,"train_below":9000,"val_below":9500,"heldout_slice":{"vessel":"feeder_1700","slice":"medium"},"heldout_port":"us_west_coast","heldout_noise":0.8,"selection":"seed ascending; vessel, port, generator-setting product order; globally disjoint candidate seeds","seed_assignment":"2**48 + family_index*2**40 + rung_index*2**32 + candidate_index"}'''
_CANONICAL_POLICY_DG_STRESS_V3_JSON = '''{"hash_salt":"stowlm-dg-stress-v3:20261027","split":"test","test_family":"dg_stress","selection":"fixed rung, vessel, cell order; no outcome screening","seed_assignment":"2**62 + rung_index*2**32 + candidate_index","small_pair_cohorts":{"flammable_water_reactive":8,"flammable_solid_flammable_liquid":8},"pair_definitions":{"flammable_water_reactive":{"classes":["3","4.3"],"un_numbers":["1263","1402"]},"flammable_solid_flammable_liquid":{"classes":["4.1","3"],"un_numbers":["1333","1263"]}}}'''
# Public templates are copies; verification uses the private immutable strings.
CANONICAL_POLICY_V1 = json.loads(_CANONICAL_POLICY_V1_JSON)
CANONICAL_POLICY_V2 = json.loads(_CANONICAL_POLICY_V2_JSON)
CANONICAL_POLICY_DG_STRESS_V3 = json.loads(_CANONICAL_POLICY_DG_STRESS_V3_JSON)


@dataclass(frozen=True)
class ReleasePin:
    version: str
    entries_sha256: str
    policy_json: str
    identity_version: str
    split_seed: int = 20261027


# The entries digests are release constants, never loaded from the candidate file.
RELEASE_PINS = MappingProxyType({
    "v1": ReleasePin("v1", "b776af130fdeee71e622d08c535e5fdfdb30053ae52b843042ca817ebd8f143c",
                     _CANONICAL_POLICY_V1_JSON, "v1"),
    "v2": ReleasePin("v2", "7fefd88f6afda2f185e3b252e61e831fc3fe09972c1ec82b2776b6f841f821f4", _CANONICAL_POLICY_V2_JSON, "v2"),
    "dg-stress-v3": ReleasePin("dg-stress-v3", "271935b242f9243a871d96bc84de88d457f8b5f5e1cdab6e7fd94423b7496504",
                               _CANONICAL_POLICY_DG_STRESS_V3_JSON, "v3"),
})


def _reject_superseded(version: str) -> None:
    if version in ("dg-stress-v1", "dg-stress-v2"):
        raise ValueError(f"{version} is superseded; select dg-stress-v3")


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def validate_manifest(manifest: dict) -> None:
    """Validate a selected trusted release, including its independently pinned list.

    Historical v1 remains readable for audit; it is not certified draw-disjoint.
    V2 remains the default allocation; DG stress is a separate test-only release.
    No custom self-signed releases are accepted.
    """
    from eval import splits

    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    version = manifest.get("version")
    _reject_superseded(version)
    if not isinstance(version, str) or version not in RELEASE_PINS:
        raise ValueError("unsupported manifest version")
    pin = RELEASE_PINS[version]
    expected_keys = {"version", "split_seed", "policy", "entries_sha256", "counts", "family_counts", "entries"}
    if pin.identity_version != "v1":
        expected_keys.add("identity_version")
        if manifest.get("identity_version") != pin.identity_version:
            raise ValueError("unsupported manifest identity version")
    if manifest.keys() != expected_keys:
        raise ValueError("manifest schema does not match selected release")
    if type(manifest.get("split_seed")) is not int or manifest["split_seed"] != pin.split_seed:
        raise ValueError("unsupported manifest split seed")
    if _json(manifest.get("policy")) != _json(json.loads(pin.policy_json)):
        raise ValueError("manifest policy does not match selected release")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest entries must be a nonempty list")
    try:
        actual_digest = _digest(entries)
    except (TypeError, ValueError) as exc:
        raise ValueError("manifest entries are not canonical JSON") from exc
    if manifest.get("entries_sha256") != actual_digest:
        raise ValueError("manifest checksum mismatch")
    if actual_digest != pin.entries_sha256:
        raise ValueError("manifest entries do not match independent release pin")
    if _json(manifest.get("counts")) != _json(splits.summarize_entries(entries)):
        raise ValueError("manifest counts mismatch")
    if _json(manifest.get("family_counts")) != _json(splits.summarize_families(entries)):
        raise ValueError("manifest family counts mismatch")
    seen = set()
    draw_labels = {}
    for entry in entries:
        try:
            if entry.keys() != {"spec", "split", "test_family"}:
                raise ValueError("incorrect inventory entry schema")
            fields = dict(entry["spec"])
            if version == "v1":
                fields["identity_version"] = "v1"
            spec = splits.InstanceSpec(**fields)
            if spec.identity_version != pin.identity_version or splits.canonical_spec(spec) != entry["spec"]:
                raise ValueError("noncanonical spec or incorrect identity version")
            key = splits.spec_key(spec)
            if key in seen:
                raise ValueError("duplicate spec (possible split leakage)")
            seen.add(key)
            if entry["split"] != splits.split_of(spec):
                raise ValueError("spec assigned to incorrect split")
            expected_family = splits.test_family(spec) if entry["split"] == "test" else None
            if entry["test_family"] != expected_family:
                raise ValueError("incorrect test family label")
            if pin.identity_version != "v1":
                draw_key = splits.draw_group_key(spec)
                if draw_labels.setdefault(draw_key, entry["split"]) != entry["split"]:
                    raise ValueError("underlying draw group crosses split labels")
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("malformed inventory entry") from exc


def load_manifest(path: str | Path | None = None) -> dict:
    """Load default v2, an explicit trusted release name, or a manifest path."""
    selector = "v2" if path is None else str(path)
    for component in Path(selector).parts:
        _reject_superseded(component)
    target = (_ROOT / "experiments/splits" / selector / "manifest.json"
              if selector in RELEASE_PINS else Path(selector))
    manifest = read_snapshot(target).json()
    validate_manifest(manifest)
    return manifest


class FrozenInventory:
    """A validated inventory indexed by the complete generation identity.

    Returned manifests and entries are defensive copies, so downstream edits
    cannot silently change the release that subsequent lookups enforce.
    """

    def __init__(self, manifest: dict):
        from eval.splits import _membership_spec

        validate_manifest(manifest)
        self._manifest = deepcopy(manifest)
        self._entries = {_digest(_membership_spec(entry["spec"])): entry
                         for entry in self._manifest["entries"]}

    @property
    def manifest(self) -> dict:
        return deepcopy(self._manifest)

    @property
    def version(self) -> str:
        return self._manifest["version"]

    @property
    def entries_sha256(self) -> str:
        return self._manifest["entries_sha256"]

    def require_membership(self, spec, *, split: str | None = None) -> dict:
        """Return its frozen entry; reject nonmembers and mismatched claimed labels."""
        from eval import splits

        _reject_superseded(self.version)
        if split is not None and split not in ("train", "val", "test"):
            raise ValueError("invalid claimed split")
        # Raw archived maps have no identity-version field. They remain auditable
        # exactly as recorded; v2 objects cannot alias them by dropping controls.
        if self.version == "v1" and isinstance(spec, Mapping) and set(spec) == {
            "generator", "rung", "vessel", "slice", "port", "noise", "blocking_ratio", "seed", "cargo_profile",
        }:
            canonical = dict(spec)
        else:
            canonical = splits.canonical_spec(spec)
        entry = self._entries.get(_digest(splits._membership_spec(canonical)))
        if entry is None:
            raise ValueError("specification is not in the selected frozen inventory")
        if split is not None and entry["split"] != split:
            raise ValueError("claimed split disagrees with frozen inventory label")
        return deepcopy(entry)

    def membership_label(self, spec) -> str:
        """Look up a frozen label without assigning a label to unknown requests."""
        return self.require_membership(spec)["split"]


def load_inventory(path: str | Path | None = None) -> FrozenInventory:
    return FrozenInventory(load_manifest(path))


def require_membership(spec, *, inventory: FrozenInventory | None = None, split: str | None = None) -> dict:
    return (inventory if inventory is not None else load_inventory()).require_membership(spec, split=split)


def membership_label(spec, *, inventory: FrozenInventory | None = None) -> str:
    return (inventory if inventory is not None else load_inventory()).membership_label(spec)
