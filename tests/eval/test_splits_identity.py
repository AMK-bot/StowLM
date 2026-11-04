"""Regression coverage for versioned generation identity and shared draw groups."""
from dataclasses import replace
import hashlib
import json

import pytest

import eval.splits as splits


def spec(**changes):
    values = dict(generator="simulated", rung="small", vessel="feeder_500", slice="small",
                  port="laem_chabang_like", noise=0.2, blocking_ratio=None, seed=17)
    return splits.InstanceSpec(**(values | changes))


def tagged_instance(empty_ship_instance, **changes):
    instance = empty_ship_instance.model_copy(deep=True)
    instance.seed = 17
    instance.size_class = "small"
    instance.provenance.created_by = "generate.instances"
    instance.family = dict(vessel="feeder_500", port="laem_chabang_like", size="small",
        noise="0.2", seed="17", cargo_profile="feeder_export", requested_n_boxes="10",
        n_boxes="10", dense_20ft="false", tiny_dg_enabled="true", without_dg="false",
        equipment_profile="full_mix") | changes
    return instance


def test_requested_quantity_survives_adapter_and_changes_identity(empty_ship_instance):
    first = tagged_instance(empty_ship_instance)
    second = tagged_instance(empty_ship_instance, requested_n_boxes="20", n_boxes="20")
    a, b = map(splits.spec_from_instance, (first, second))
    assert splits.spec_key(a) != splits.spec_key(b)
    assert a.n_boxes == 10 and b.n_boxes == 20


def test_dense_equipment_survives_adapter_and_changes_identity(empty_ship_instance):
    first = tagged_instance(empty_ship_instance)
    second = tagged_instance(empty_ship_instance, dense_20ft="true", equipment_profile="dense_20ft")
    a, b = map(splits.spec_from_instance, (first, second))
    assert splits.spec_key(a) != splits.spec_key(b)
    assert b.dense_20ft and b.equipment_profile == "dense_20ft"


def test_auto_quantity_is_requested_control_not_realized_count(empty_ship_instance):
    first = tagged_instance(empty_ship_instance, requested_n_boxes="auto", n_boxes="15")
    second = tagged_instance(empty_ship_instance, requested_n_boxes="15", n_boxes="15")
    a, b = map(splits.spec_from_instance, (first, second))
    assert splits.spec_key(a) != splits.spec_key(b)
    assert a.n_boxes is None and b.n_boxes == 15


def test_empty_dry20_identity_is_supported_and_explicit(empty_ship_instance):
    instance = tagged_instance(empty_ship_instance, cargo_profile="empty_dry20",
        equipment_profile="empty_dry20", tiny_dg_enabled="false")
    instance.size_class = instance.family["size"] = "tiny"
    candidate = splits.spec_from_instance(instance)
    assert candidate.cargo_profile == candidate.equipment_profile == "empty_dry20"
    assert not candidate.tiny_dg_enabled


def test_same_seed_cargo_variants_share_draw_group_and_assignment():
    original = spec()
    variants = [replace(original, noise=0.5), replace(original, n_boxes=10),
        replace(original, dense_20ft=True, equipment_profile="dense_20ft"),
        replace(original, generator="random_yard", noise=None, blocking_ratio=0.5)]
    assert len({splits.draw_group_key(s) for s in [original, *variants]}) == 1
    assert len({splits.split_of(s) for s in [original, *variants]}) == 1
    assert splits.draw_group_key(original) != splits.draw_group_key(replace(original, seed=18))


def test_v1_identity_and_assignment_remain_reproducible():
    candidate = spec(identity_version="v1")
    canonical = splits.canonical_spec(candidate)
    assert set(canonical) == {"generator", "rung", "vessel", "slice", "port", "noise", "blocking_ratio", "seed", "cargo_profile"}
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    bucket = int.from_bytes(hashlib.sha256(b"stowlm-splits-v1:20261027:" + payload).digest(), "big") % 10000
    assert splits.split_of(candidate) == ("train" if bucket < 9000 else "val" if bucket < 9500 else "test")


@pytest.mark.parametrize("changes", [
    {"requested_n_boxes": "-1"}, {"requested_n_boxes": "1.5"}, {"dense_20ft": "maybe"},
    {"without_dg": "maybe"}, {"tiny_dg_enabled": "maybe"},
    {"equipment_profile": "dense_20ft", "dense_20ft": "false"},
])
def test_adapter_rejects_invalid_or_conflicting_controls(empty_ship_instance, changes):
    with pytest.raises(ValueError):
        splits.spec_from_instance(tagged_instance(empty_ship_instance, **changes))


def test_random_adapter_accepts_actual_dense_population_tag(empty_ship_instance):
    instance = tagged_instance(empty_ship_instance, cargo_profile="dense_dry20_dg",
        equipment_profile="dense_20ft", dense_20ft="true", tiny_dg_enabled="false")
    instance.provenance.created_by = "generate.random_yard"
    instance.family["blocking_ratio"] = instance.family.pop("noise")
    candidate = splits.spec_from_instance(instance)
    assert candidate.cargo_profile == "dense_dry20_dg" and candidate.dense_20ft


def test_random_tiny_compatibility_flag_does_not_mean_empty_only(empty_ship_instance):
    instance = tagged_instance(empty_ship_instance, equipment_profile="dry20_with_dg", tiny_dg_enabled="false")
    instance.provenance.created_by = "generate.random_yard"
    instance.family["blocking_ratio"] = instance.family.pop("noise")
    instance.size_class = instance.family["size"] = "tiny"
    candidate = splits.spec_from_instance(instance)
    assert candidate.cargo_profile == "feeder_export" and not candidate.tiny_dg_enabled


def test_legacy_canonical_mapping_keeps_v1_key_and_assignment():
    candidate = spec(identity_version="v1")
    legacy_mapping = splits.canonical_spec(candidate)
    assert splits.spec_key(legacy_mapping) == splits.spec_key(candidate)
    assert splits.split_of(legacy_mapping) == splits.split_of(candidate)
