"""Epoch inputs retain v2 membership while binding new bytes and source."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from eval import harness, splits_epoch
from eval.splits import DEFAULT_MANIFEST, load_inventory, spec_key
from stowage.versions import component_versions


@pytest.fixture
def epoch(tmp_path, monkeypatch):
    root = tmp_path / "instances-etest"
    root.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "owned.txt").write_text("frozen")
    monkeypatch.setattr(splits_epoch, "_SOURCE_ROOT", source)
    inventory = load_inventory()
    entry = next(e for e in inventory.manifest["entries"]
                 if e["spec"]["rung"] == "tiny" and e["split"] == "val")
    key = spec_key(entry["spec"])
    files = {"owned.txt": sha256(b"frozen").hexdigest()}
    versions = {"CHECKER_VERSION": "test"}
    source_hash = sha256(json.dumps({"files": files, "versions": versions}, sort_keys=True).encode()).hexdigest()
    freeze = dict(epoch_id="test", diagnostic=False, source_sha256=source_hash,
                  files=files, versions=versions, paths=["owned.txt"])
    cache_sources = {name: str(source / "cache" / name) for name in ("stability", "vessels", "linear")}
    cache_files = {}
    cache_hash = sha256(json.dumps({"sources": cache_sources, "files": cache_files}, sort_keys=True).encode()).hexdigest()
    freeze.update(schema_version=2, numerical_caches=dict(sources=cache_sources, files=cache_files, sha256=cache_hash),
                  freeze_sha256=sha256((source_hash + cache_hash).encode()).hexdigest())
    shard = dict(instance="a.json", sha256=sha256(b"instance").hexdigest(),
                 split_spec=entry["spec"], split=entry["split"], spec_key=key,
                 split_version="v2", split_entries_sha256=inventory.entries_sha256,
                 split_policy=inventory.manifest["policy"]["hash_salt"])
    manifest = dict(schema_version=splits_epoch.EPOCH_SCHEMA, epoch_id="test", diagnostic=False,
                    versions=component_versions(), split_version="v2",
                    split_entries_sha256=inventory.entries_sha256,
                    source_sha256=source_hash, freeze_manifest=freeze,
                    groups={"tiny": {"shards": [shard]}},
                    bindings={key: dict(spec=entry["spec"], split=entry["split"],
                                        instance="tiny/a.json", sha256=shard["sha256"])})
    return root, manifest, inventory, source


def validate(epoch, **kwargs):
    root, manifest, inventory, _ = epoch
    return splits_epoch.validate_epoch_manifest(root, manifest, inventory, **kwargs)


def test_epoch_binds_frozen_allocation_and_source(epoch):
    validate(epoch)
    root, manifest, _, source = epoch
    (source / "owned.txt").write_text("drift")
    with pytest.raises(ValueError, match="source drift: owned.txt"):
        validate(epoch)


def test_epoch_rechecks_frozen_numerical_cache_payloads(epoch):
    freeze = epoch[1]["freeze_manifest"]
    pin = freeze["numerical_caches"]
    vessel_cache = Path(pin["sources"]["vessels"])
    vessel_cache.mkdir(parents=True)
    payload = vessel_cache / "hull.json"
    payload.write_text('{"max_mass_kg":30000}')
    pin["files"]["vessels/hull.json"] = sha256(payload.read_bytes()).hexdigest()
    pin["sha256"] = sha256(json.dumps({"sources": pin["sources"], "files": pin["files"]}, sort_keys=True).encode()).hexdigest()
    freeze["freeze_sha256"] = sha256((freeze["source_sha256"] + pin["sha256"]).encode()).hexdigest()
    validate(epoch)
    payload.write_text('{"max_mass_kg":60000}')
    with pytest.raises(ValueError, match="numerical cache.*vessels/hull.json"):
        validate(epoch)


def test_epoch_without_numerical_cache_pin_cannot_be_official(epoch):
    del epoch[1]["freeze_manifest"]["numerical_caches"]
    with pytest.raises(ValueError, match="numerical cache pin"):
        validate(epoch)


def test_harness_rechecks_epoch_cache_before_loading_each_instance(epoch, monkeypatch, empty_ship_instance):
    root, manifest, _, source = epoch
    path = root / "input.json"
    path.write_text(empty_ship_instance.model_dump_json())
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    row = dict(path=str(path), input_sha256=sha256(path.read_bytes()).hexdigest(), spec={},
               regen_manifest_path=str(manifest_path),
               regen_manifest_sha256=sha256(manifest_path.read_bytes()).hexdigest())
    monkeypatch.setattr(splits_epoch, "instance_matches_spec", lambda *_: True)
    harness._load_selected(row, True)
    cache = source / "cache/vessels"
    cache.mkdir(parents=True)
    (cache / "unexpected.json").write_text('{"max_mass_kg":60000}')
    with pytest.raises(ValueError, match="numerical cache.*unexpected.json"):
        harness._load_selected(row, True)


@pytest.mark.parametrize("field,value", [("source_sha256", "bad"), ("epoch_id", "other"),
                                        ("split_entries_sha256", "bad"), ("versions", {"checker": "bad"})])
def test_epoch_rejects_provenance_tamper(epoch, field, value):
    epoch[1][field] = value
    with pytest.raises(ValueError):
        validate(epoch)


def test_epoch_binding_cannot_admit_nonmember_or_detach_bytes(epoch):
    manifest = epoch[1]
    key = next(iter(manifest["bindings"]))
    manifest["bindings"][key]["sha256"] = "detached"
    with pytest.raises(ValueError, match="binding mismatch"):
        validate(epoch)
    manifest["groups"]["tiny"]["shards"][0]["split_spec"] = deepcopy(manifest["bindings"][key]["spec"])
    manifest["groups"]["tiny"]["shards"][0]["split_spec"]["seed"] = 7
    with pytest.raises(ValueError, match="not in the selected frozen inventory"):
        validate(epoch)


def test_diagnostic_cannot_be_used_for_official_or_skip_production_checks(epoch):
    with pytest.raises(ValueError, match="only be skipped for diagnostic"):
        validate(epoch, check_source=False)
    epoch[1]["diagnostic"] = epoch[1]["freeze_manifest"]["diagnostic"] = True
    with pytest.raises(ValueError, match="diagnostic instances"):
        validate(epoch)
    (epoch[3] / "owned.txt").write_text("moving diagnostic source")
    validate(epoch, allow_diagnostic=True, check_source=False)


def test_epoch_identity_resolves_ship_alias_without_changing_v2_key(empty_ship_instance):
    inventory = load_inventory()
    entry = next(e for e in inventory.manifest["entries"]
                 if e["spec"]["rung"] == "tiny" and e["spec"]["vessel"] == "feeder_1000"
                 and e["spec"]["generator"] == "random_yard")
    spec = entry["spec"]
    family = dict(size="tiny", seed=str(spec["seed"]), vessel="feeder_800", slice="tiny",
                  port=spec["port"], blocking_ratio=str(spec["blocking_ratio"]),
                  cargo_profile=spec["cargo_profile"], requested_n_boxes="auto" if spec["n_boxes"] is None else str(spec["n_boxes"]),
                  dense_20ft=str(spec["dense_20ft"]).lower(),
                  tiny_dg_enabled=str(spec["tiny_dg_enabled"]).lower(),
                  without_dg=str(spec["without_dg"]).lower(), equipment_profile=spec["equipment_profile"])
    from stowage.model import Provenance
    instance = empty_ship_instance.model_copy(deep=True, update={"seed": spec["seed"],
        "size_class": "tiny", "family": family, "provenance": Provenance(created_by="generate.random_yard")})
    before = spec_key(spec)
    assert splits_epoch.instance_matches_spec(instance, spec)
    assert spec_key(spec) == before
    # Gold and SFT already use spec_from_instance directly: retaining the raw
    # allocation request makes their existing identity boundary work unchanged.
    from eval.splits import spec_from_instance, canonical_spec
    instance.family["split_vessel"] = spec["vessel"]
    assert spec_key(spec_from_instance(instance)) == before
    assert canonical_spec(spec_from_instance(instance)) == canonical_spec(spec)
    instance.family["split_vessel"] = "feeder_500"
    with pytest.raises(ValueError, match="archived split vessel"):
        splits_epoch.instance_matches_spec(instance, spec)
    instance.family["split_vessel"] = spec["vessel"]
    instance.family["port"] = "us_west_coast" if spec["port"] != "us_west_coast" else "small_feeder"
    assert not splits_epoch.instance_matches_spec(instance, spec)


def test_explicit_instance_root_required(tmp_path):
    with pytest.raises(ValueError, match="explicitly select an instance root"):
        harness.select_instances()
    with pytest.raises(SystemExit) as error:
        harness.main(["--run-dir", str(tmp_path)])
    assert error.value.code == 2


def test_epoch_missing_schema_cannot_fall_back_to_legacy(epoch):
    root, manifest, _, _ = epoch
    del manifest["schema_version"]
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unsupported epoch"):
        harness.select_instances(root, rungs=["tiny"])


def test_epoch_selection_validates_selected_bytes(epoch, monkeypatch):
    root, manifest, _, _ = epoch
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "tiny").mkdir()
    (root / "tiny/a.json").write_text("changed bytes")
    with pytest.raises(ValueError, match="instance checksum mismatch"):
        harness.select_instances(root, rungs=["tiny"])


def test_epoch_valid_binding_reaches_harness_with_resume_provenance(epoch, monkeypatch):
    root, manifest, _, _ = epoch
    (root / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(splits_epoch, "validate_epoch_instance", lambda *a: None)
    rows = harness.select_instances(root, rungs=["tiny"])
    assert rows[0]["epoch_id"] == "test"
    assert rows[0]["source_sha256"] == manifest["source_sha256"]
    assert rows[0]["regen_manifest_sha256"] == sha256((root / "manifest.json").read_bytes()).hexdigest()
