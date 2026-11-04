"""Pinned releases cannot be replaced by self-consistent campaign inventories."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

import eval.splits as splits

V1_PATH = Path(__file__).resolve().parents[2] / "experiments/splits/v1/manifest.json"


def archived_manifest():
    return json.loads(V1_PATH.read_text())


def test_archived_release_rejects_coherent_entry_removal():
    manifest = archived_manifest()
    manifest["entries"].pop()
    manifest["entries_sha256"] = splits.entries_sha256(manifest["entries"])
    manifest["counts"] = splits.summarize_entries(manifest["entries"])
    manifest["family_counts"] = splits.summarize_families(manifest["entries"])
    with pytest.raises(ValueError, match="release pin"):
        splits.validate_manifest(manifest)


def test_archived_release_rejects_policy_spoof():
    manifest = archived_manifest()
    manifest["policy"]["heldout_noise"] = 0.1
    with pytest.raises(ValueError, match="policy"):
        splits.validate_manifest(manifest)


def test_frozen_inventory_rejects_policy_consistent_nonmember():
    inventory = splits.load_inventory()
    member = inventory.manifest["entries"][0]
    candidate = splits.InstanceSpec(**member["spec"])
    # A valid policy label is insufficient: this future request is not frozen.
    candidate = replace(candidate, seed=10**30)
    with pytest.raises(ValueError, match="not.*inventory"):
        inventory.require_membership(candidate)


@pytest.mark.parametrize("mutation", ["remove", "policy", "identity_version", "metadata_type", "extra_metadata"])
def test_current_release_rejects_coherent_inventory_or_metadata_change(mutation):
    manifest = splits.load_manifest()
    if mutation == "remove":
        manifest["entries"].pop()
        manifest["entries_sha256"] = splits.entries_sha256(manifest["entries"])
        manifest["counts"] = splits.summarize_entries(manifest["entries"])
        manifest["family_counts"] = splits.summarize_families(manifest["entries"])
    elif mutation == "policy":
        manifest["policy"]["heldout_noise"] = 0.1
    elif mutation == "identity_version":
        manifest["identity_version"] = "v1"
    elif mutation == "metadata_type":
        manifest["policy"]["hash_buckets"] = 10000.0
    else:
        manifest["untrusted_release_claim"] = "same inventory"
    with pytest.raises(ValueError):
        splits.validate_manifest(manifest)


def test_inventory_lookup_returns_recorded_labels_and_checks_claim():
    inventory = splits.load_inventory()
    entries = inventory.manifest["entries"]
    for label in ("train", "val", "test"):
        entry = next(item for item in entries if item["split"] == label)
        assert inventory.require_membership(entry["spec"], split=label) == entry
        assert inventory.membership_label(entry["spec"]) == label
        assert splits.require_membership(entry["spec"], inventory=inventory) == entry
        assert splits.membership_label(entry["spec"], inventory=inventory) == label
        wrong_label = "val" if label != "val" else "train"
        with pytest.raises(ValueError, match="claimed split"):
            inventory.require_membership(entry["spec"], split=wrong_label)
    with pytest.raises(ValueError, match="invalid claimed split"):
        inventory.require_membership(entries[0]["spec"], split="future")
    assert inventory.version == "v2"
    assert inventory.entries_sha256 == splits.load_manifest()["entries_sha256"]


def test_inventory_isolated_from_mutated_inputs_and_lookup_results():
    from eval.splits_inventory import FrozenInventory

    manifest = splits.load_manifest()
    entry = deepcopy(manifest["entries"][0])
    inventory = FrozenInventory(manifest)
    manifest["entries"][0]["spec"]["seed"] = 1
    exposed = inventory.manifest
    exposed["entries"][0]["split"] = "val"
    returned = inventory.require_membership(entry["spec"])
    returned["split"] = "val"
    returned["spec"]["seed"] = 2
    assert inventory.require_membership(entry["spec"]) == entry


def test_explicit_archived_inventory_keeps_v1_identity_and_label_lookup():
    inventory = splits.load_inventory(V1_PATH)
    entry = inventory.manifest["entries"][0]
    assert inventory.version == "v1"
    assert inventory.require_membership(entry["spec"]) == entry
    candidate = splits.InstanceSpec(**entry["spec"], identity_version="v1")
    assert inventory.membership_label(candidate) == entry["split"]
    # A new identity is never admitted to v1 by dropping its added controls.
    with pytest.raises(ValueError, match="not.*inventory"):
        inventory.require_membership(replace(candidate, identity_version="v2", n_boxes=20))


@pytest.mark.parametrize("path", [None, V1_PATH])
def test_membership_accepts_canonical_ship_without_changing_archived_identity(path):
    inventory = splits.load_inventory(path)
    entry = next(row for row in inventory.manifest["entries"]
                 if row["spec"]["vessel"] == "feeder_1000")
    original = deepcopy(entry["spec"])
    canonical = dict(original, vessel="feeder_800")
    # Both public lookup paths return the original frozen provenance and label.
    assert inventory.require_membership(canonical) == entry
    assert splits.require_membership(canonical, inventory=inventory) == entry
    assert inventory.membership_label(canonical) == entry["split"]
    assert splits.canonical_spec(original)["vessel"] == "feeder_1000"
    assert splits.spec_key(original) != splits.spec_key(canonical)
    assert inventory.require_membership(original) == entry
    assert entry["spec"] == original
    wrong_split = "val" if entry["split"] != "val" else "train"
    with pytest.raises(ValueError, match="claimed split"):
        inventory.require_membership(canonical, split=wrong_split)
    with pytest.raises(ValueError, match="not.*inventory"):
        inventory.require_membership(dict(canonical, seed=10**30))


def test_public_policy_template_cannot_modify_trusted_release_validation():
    from eval.splits_inventory import CANONICAL_POLICY_V2

    original = deepcopy(CANONICAL_POLICY_V2)
    try:
        CANONICAL_POLICY_V2["heldout_noise"] = 0.1
        manifest = splits.load_manifest()
        manifest["policy"]["heldout_noise"] = 0.1
        with pytest.raises(ValueError, match="policy"):
            splits.validate_manifest(manifest)
    finally:
        CANONICAL_POLICY_V2.clear()
        CANONICAL_POLICY_V2.update(original)
