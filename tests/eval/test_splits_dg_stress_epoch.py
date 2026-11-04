"""The stress release is independently pinned and cannot borrow v2 epochs."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval import splits, splits_epoch
from stowage.versions import component_versions


@pytest.fixture(params=["dg-stress-v1", "dg-stress-v2"])
def superseded_path(request):
    return Path(__file__).resolve().parents[2] / "experiments/splits" / request.param / "manifest.json"


@pytest.mark.parametrize("kind", ["name", "path_name", "manifest", "missing"])
def test_superseded_stress_release_is_never_selected(superseded_path, kind):
    selector = {"name": superseded_path.parent.name,
                "path_name": Path(superseded_path.parent.name),
                "manifest": superseded_path,
                "missing": superseded_path.parent / "missing.json"}[kind]
    for loader in (splits.load_manifest, splits.load_inventory):
        with pytest.raises(ValueError, match="superseded"):
            loader(selector)


def test_superseded_stress_bytes_cannot_be_renamed_or_validated(tmp_path, superseded_path):
    from eval.splits_inventory import FrozenInventory

    renamed = tmp_path / "renamed.json"
    renamed.write_bytes(superseded_path.read_bytes())
    historical = json.loads(renamed.read_bytes())
    for action in (lambda: splits.load_inventory(renamed),
                   lambda: splits.validate_manifest(historical),
                   lambda: FrozenInventory(historical)):
        with pytest.raises(ValueError, match="superseded"):
            action()


def test_existing_superseded_inventory_cannot_supply_membership(superseded_path):
    from eval.splits_inventory import FrozenInventory

    # Represent an object retained by a long-running process before supersession.
    historical = json.loads(superseded_path.read_bytes())
    inventory = object.__new__(FrozenInventory)
    inventory._manifest = historical
    inventory._entries = {}
    with pytest.raises(ValueError, match="superseded"):
        inventory.require_membership(historical["entries"][0]["spec"])


def test_epoch_rejects_preexisting_superseded_inventory_before_source_checks(tmp_path, superseded_path):
    historical = json.loads(superseded_path.read_bytes())
    inventory = SimpleNamespace(version=historical["version"], entries_sha256=historical["entries_sha256"])
    manifest = dict(schema_version=splits_epoch.EPOCH_SCHEMA, epoch_id="old-stress", diagnostic=False,
                    split_version=inventory.version, split_entries_sha256=inventory.entries_sha256)
    with pytest.raises(ValueError, match="superseded"):
        splits_epoch.validate_epoch_manifest(tmp_path, manifest, inventory)


@pytest.mark.parametrize("selector", ["v2", Path("v2"), "dg-stress-v3", Path("dg-stress-v3")])
def test_frozen_release_can_be_selected_by_name(selector):
    inventory = splits.load_inventory(selector)
    assert inventory.version == str(selector)
    assert splits.load_manifest(selector) == inventory.manifest


def test_stress_pin_rejects_self_consistent_changes_and_cross_release_members():
    stress = splits.load_inventory("dg-stress-v3")
    current = splits.load_inventory()
    manifest = stress.manifest
    manifest["entries"].pop()
    manifest["entries_sha256"] = splits.entries_sha256(manifest["entries"])
    manifest["counts"] = splits.summarize_entries(manifest["entries"])
    manifest["family_counts"] = splits.summarize_families(manifest["entries"])
    with pytest.raises(ValueError, match="release pin"):
        splits.validate_manifest(manifest)
    for selected, foreign in ((stress, current), (current, stress)):
        with pytest.raises(ValueError, match="not.*inventory"):
            selected.require_membership(foreign.manifest["entries"][0]["spec"])


@pytest.mark.parametrize("field,value", [("identity_version", "v1"), ("split_seed", 1),
                                        ("policy", {}), ("version", "custom-stress")])
def test_stress_pin_rejects_release_metadata_tamper(field, value):
    manifest = splits.load_manifest("dg-stress-v3")
    manifest[field] = value
    with pytest.raises(ValueError):
        splits.validate_manifest(manifest)


@pytest.fixture
def stress_epoch(tmp_path, monkeypatch):
    inventory = splits.load_inventory("dg-stress-v3")
    entry = inventory.manifest["entries"][0]
    key = splits.spec_key(entry["spec"])
    root = tmp_path / "instances-estress"
    root.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "owned.txt").write_text("frozen")
    monkeypatch.setattr(splits_epoch, "_SOURCE_ROOT", source)
    files = {"owned.txt": sha256(b"frozen").hexdigest()}
    versions = {"fixture": "stress"}
    source_hash = sha256(json.dumps({"files": files, "versions": versions}, sort_keys=True).encode()).hexdigest()
    cache_sources = {name: str(source / "cache" / name) for name in ("stability", "vessels", "linear")}
    cache_pin = {"sources": cache_sources, "files": {}}
    cache_hash = sha256(json.dumps(cache_pin, sort_keys=True).encode()).hexdigest()
    cache_pin["sha256"] = cache_hash
    freeze = dict(epoch_id="stress", diagnostic=False, files=files, versions=versions,
                  paths=["owned.txt"], source_sha256=source_hash, numerical_caches=cache_pin,
                  freeze_sha256=sha256((source_hash + cache_hash).encode()).hexdigest())
    shard = dict(instance="a.json", sha256=sha256(b"instance").hexdigest(),
                 split_spec=entry["spec"], split="test", spec_key=key,
                 split_version=inventory.version, split_entries_sha256=inventory.entries_sha256,
                 split_policy=inventory.manifest["policy"]["hash_salt"])
    manifest = dict(schema_version=splits_epoch.EPOCH_SCHEMA, epoch_id="stress", diagnostic=False,
                    versions=component_versions(), split_version=inventory.version,
                    split_entries_sha256=inventory.entries_sha256,
                    source_sha256=source_hash, freeze_manifest=freeze,
                    groups={"tiny": {"shards": [shard]}},
                    bindings={key: dict(spec=entry["spec"], split="test", instance="tiny/a.json",
                                        sha256=shard["sha256"])})
    return root, manifest, inventory


def test_stress_epoch_binds_its_own_pinned_allocation(stress_epoch):
    splits_epoch.validate_epoch_manifest(*stress_epoch)


@pytest.mark.parametrize("level", ["manifest", "shard"])
@pytest.mark.parametrize("field", ["split_version", "split_entries_sha256"])
def test_stress_epoch_refuses_v2_release_metadata(stress_epoch, level, field):
    root, manifest, inventory = stress_epoch
    other = splits.load_inventory()
    target = manifest if level == "manifest" else manifest["groups"]["tiny"]["shards"][0]
    target[field] = other.version if field == "split_version" else other.entries_sha256
    with pytest.raises(ValueError, match="allocation|split metadata"):
        splits_epoch.validate_epoch_manifest(root, manifest, inventory)


def test_stress_epoch_refuses_foreign_inventory(stress_epoch):
    root, manifest, _ = stress_epoch
    with pytest.raises(ValueError, match="allocation"):
        splits_epoch.validate_epoch_manifest(root, deepcopy(manifest), splits.load_inventory())
