"""Frozen split membership and leakage checks use specifications, never solved plans."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json

import pytest

import eval.splits as splits
from stowage.conventions import canonical_vessel_id


def spec(**changes):
    base = dict(generator="simulated", rung="small", vessel="feeder_500", slice="small",
                port="laem_chabang_like", noise=0.2, blocking_ratio=None, seed=17)
    return splits.InstanceSpec(**(base | changes))


def test_membership_is_seeded_sha256_of_draw_group():
    for seed in range(250):
        candidate = spec(seed=seed)
        serialized = json.dumps(splits.draw_group(candidate), sort_keys=True, separators=(",", ":"))
        bucket = int(hashlib.sha256((splits.SPLIT_POLICY + ":" + serialized).encode()).hexdigest(), 16) % 10000
        expected = "train" if bucket < 9000 else "val" if bucket < 9500 else "test"
        assert splits.split_of(candidate) == expected
    assert {splits.split_of(spec(seed=seed)) for seed in range(250)} == {"train", "val", "test"}


def test_spec_is_immutable_and_identity_uses_all_dimensions():
    original = spec()
    with pytest.raises(FrozenInstanceError):
        original.seed = 99
    variations = [replace(original, **changes) for changes in [
        {"generator": "random_yard", "noise": None, "blocking_ratio": 0.5},
        {"rung": "medium", "slice": "medium"}, {"vessel": "feeder_800"},
        {"port": "singapore_like"}, {"noise": 0.3}, {"seed": 18},
        {"cargo_profile": "no_dg", "without_dg": True},
    ]]
    assert len({splits.spec_key(item) for item in [original, *variations]}) == 8
    assert splits.spec_key(original) == splits.spec_key(splits.InstanceSpec(**splits.canonical_spec(original)))


@pytest.mark.parametrize(("changes", "family"), [
    ({"vessel": "feeder_1700", "rung": "medium", "slice": "medium"}, "heldout_slice"),
    ({"port": "us_west_coast"}, "heldout_port"),
    ({"noise": 0.8}, "heldout_noise"),
])
def test_heldout_families_always_go_to_test(changes, family):
    for seed in range(40):
        candidate = spec(**changes, seed=seed)
        assert splits.split_of(candidate) == "test"
        assert splits.test_family(candidate) == family
    assert splits.test_family(spec()) == "in_distribution"
    assert splits.test_family(spec(vessel="feeder_1700", slice="small")) == "in_distribution"


def test_overlapping_holdouts_keep_every_reason():
    candidate = spec(vessel="feeder_1700", rung="medium", slice="medium", port="us_west_coast", noise=0.8)
    assert set(splits.test_family(candidate).split("+")) == {"heldout_slice", "heldout_port", "heldout_noise"}


@pytest.mark.parametrize("changes", [
    {"generator": ""}, {"vessel": ""}, {"rung": ""}, {"port": ""},
    {"noise": -0.1}, {"noise": 1.1}, {"noise": float("nan")},
    {"blocking_ratio": -0.1}, {"blocking_ratio": 1.1}, {"blocking_ratio": float("inf")},
    {"seed": None}, {"seed": True},
])
def test_invalid_identity_is_rejected(changes):
    with pytest.raises((TypeError, ValueError)):
        splits.split_of(spec(**changes))


def tagged_instance(empty_ship_instance):
    instance = empty_ship_instance.model_copy(deep=True)
    instance.family = {"vessel": "feeder_500", "port": "laem_chabang_like", "size": "small",
                       "slice": "small", "noise": "0.2", "seed": "17", "generator": "1.0.0",
                       "cargo_profile": "feeder_export"}
    instance.seed = 17
    instance.size_class = "small"
    instance.provenance.created_by = "generate.instances"
    return instance


def test_regenerated_vessel_and_physics_keep_membership(empty_ship_instance):
    original = tagged_instance(empty_ship_instance)
    regenerated = original.model_copy(deep=True)
    regenerated.id = "new-file-name"
    regenerated.vessel.id = "new-embedded-vessel-version"
    regenerated.vessel.hull.lightship_mass_kg *= 1.001
    regenerated.family["generator"] = "2.0.0"
    regenerated.provenance.versions.update(vessel="2.0.0", checker="new-physics")
    before, after = splits.spec_from_instance(original), splits.spec_from_instance(regenerated)
    assert before == after
    assert before.generator == "simulated"
    assert splits.spec_key(before) == splits.spec_key(after)
    assert splits.split_of(before) == splits.split_of(after)


def test_missing_family_identity_does_not_fall_back_to_filename(empty_ship_instance):
    instance = tagged_instance(empty_ship_instance)
    del instance.family["vessel"]
    with pytest.raises(ValueError):
        splits.spec_from_instance(instance)


def test_manifest_has_budgeted_counts_and_no_leakage():
    manifest = splits.load_manifest()
    assert splits.validate_manifest(manifest) is None
    actual = Counter()
    seen = set()
    draw_splits = {}
    training_vessels = set()
    test_families = Counter()
    for entry in manifest["entries"]:
        candidate = splits.InstanceSpec(**entry["spec"])
        key = splits.spec_key(candidate)
        assert key not in seen
        seen.add(key)
        group = splits.draw_group_key(candidate)
        assert draw_splits.setdefault(group, entry["split"]) == entry["split"]
        assert entry["split"] == splits.split_of(candidate)
        actual[candidate.rung, entry["split"]] += 1
        if entry["split"] in {"train", "val"}:
            assert splits.test_family(candidate) == "in_distribution"
            assert entry["test_family"] is None
        if entry["split"] == "train":
            training_vessels.add(canonical_vessel_id(candidate.vessel))
        if entry["split"] == "test":
            assert entry["test_family"] == splits.test_family(candidate)
            test_families[entry["test_family"]] += 1
    assert training_vessels == {"feeder_500", "feeder_800", "feeder_1700"}
    assert test_families["in_distribution"] > 0
    assert all(test_families[name] > 0 for name in ["heldout_slice", "heldout_port", "heldout_noise"])
    for rung, train in {"tiny": 1500, "small": 1500, "medium": 2500, "full": 3500}.items():
        assert actual[rung, "train"] == train
        assert actual[rung, "val"] == 25
        assert actual[rung, "test"] == 75
    assert manifest["counts"] == splits.summarize_entries(manifest["entries"])
    assert manifest["entries_sha256"] == splits.entries_sha256(manifest["entries"])
    assert manifest["family_counts"] == splits.summarize_families(manifest["entries"])


@pytest.mark.parametrize("mutation", ["duplicate", "membership", "counts", "family_counts", "checksum", "family"])
def test_manifest_rejects_leakage_or_tampering(mutation):
    manifest = deepcopy(splits.load_manifest())
    if mutation == "duplicate":
        manifest["entries"].append(deepcopy(manifest["entries"][0]))
    elif mutation == "membership":
        manifest["entries"][0]["split"] = "val" if manifest["entries"][0]["split"] != "val" else "train"
    elif mutation == "counts":
        manifest["counts"] = {}
    elif mutation == "family_counts":
        manifest["family_counts"] = {}
    elif mutation == "checksum":
        manifest["entries_sha256"] = "0" * 64
    else:
        manifest["entries"][0]["test_family"] = "heldout_noise"
    with pytest.raises(ValueError):
        splits.validate_manifest(manifest)


def test_loading_manifest_from_explicit_path_checks_integrity(tmp_path):
    manifest = splits.load_manifest()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert splits.load_manifest(path) == manifest
    manifest["entries_sha256"] = "bad"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        splits.load_manifest(path)


@pytest.mark.parametrize("ratio", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_random_yard_rejects_invalid_active_blocking_ratio(ratio):
    with pytest.raises(ValueError):
        spec(generator="random_yard", noise=None, blocking_ratio=ratio)


def test_random_yard_adapter_uses_blocking_ratio_without_noise(empty_ship_instance):
    instance = tagged_instance(empty_ship_instance)
    instance.provenance.created_by = "generate.random_yard"
    instance.family.pop("noise")
    instance.family["blocking_ratio"] = "0.5"
    instance.family.pop("slice")  # Existing generators use the rung as their slice preset.
    candidate = splits.spec_from_instance(instance)
    assert candidate == spec(generator="random_yard", noise=None, blocking_ratio=0.5)


def test_duplicate_leakage_rejected_even_with_updated_summary_and_checksum():
    manifest = deepcopy(splits.load_manifest())
    copied = deepcopy(manifest["entries"][0])
    copied["split"] = "test" if copied["split"] != "test" else "train"
    manifest["entries"].append(copied)
    manifest["counts"] = splits.summarize_entries(manifest["entries"])
    manifest["entries_sha256"] = splits.entries_sha256(manifest["entries"])
    manifest["family_counts"] = splits.summarize_families(manifest["entries"])
    with pytest.raises(ValueError):
        splits.validate_manifest(manifest)
