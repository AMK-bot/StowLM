"""Evaluation must consume the selected allocation, not merely its hash policy."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from eval.splits import DEFAULT_MANIFEST, canonical_spec, load_inventory, spec_key, split_of


@pytest.fixture(scope="module")
def inventory():
    return load_inventory(DEFAULT_MANIFEST)


def _entries(inventory, *, split="val", rung="tiny"):
    return sorted((e for e in inventory.manifest["entries"]
                   if e["split"] == split and e["spec"]["rung"] == rung),
                  key=lambda e: spec_key(e["spec"]))


def _campaign(tmp_path, inventory, entries):
    root = tmp_path / "campaign"
    root.mkdir()
    shards = []
    for i, entry in enumerate(entries):
        spec = entry["spec"]
        shards.append({"instance": f"{i}.json", "sha256": hashlib.sha256(b"unused").hexdigest(),
                       "split_spec": spec, "spec_key": spec_key(spec), "split": entry["split"],
                       "split_policy": inventory.manifest["policy"]["hash_salt"],
                       "split_version": inventory.version})
    (root / "manifest.json").write_text(json.dumps({"groups": {"synthetic": {"shards": shards}}}))
    return root


def _select(root, **kw):
    from eval.harness_inventory import select_frozen_instances
    return select_frozen_instances(root, split_manifest=DEFAULT_MANIFEST, rungs=("tiny",), **kw)


def test_harness_inventory_rejects_policy_consistent_nonmember(tmp_path, inventory):
    spec = deepcopy(_entries(inventory)[0]["spec"])
    spec["seed"] = 17
    source = {"spec": spec, "split": split_of(spec)}
    root = _campaign(tmp_path, inventory, [source])
    with pytest.raises(ValueError, match="not in the selected frozen inventory"):
        _select(root, limits={"tiny": 1})


def test_harness_inventory_preserves_pins_and_canonical_rows(tmp_path, inventory):
    entry = _entries(inventory)[0]
    root = _campaign(tmp_path, inventory, [entry])
    selected = _select(root, limits={"tiny": 1})
    assert selected.split_version == inventory.version
    assert selected.split_entries_sha256 == inventory.entries_sha256
    assert selected.split_policy == inventory.manifest["policy"]["hash_salt"]
    assert len(selected.rows) == 1
    row = selected.rows[0]
    assert row["spec"] == canonical_spec(entry["spec"])
    assert row["spec_key"] == spec_key(entry["spec"])
    assert row["split"] == "val"
    assert row["split_version"] == inventory.version
    assert row["split_entries_sha256"] == inventory.entries_sha256
    assert Path(row["path"]).is_relative_to(root)
    assert not Path(row["path"]).exists()  # Selection must not open shard files.


def test_harness_inventory_reports_partial_allocation_coverage(tmp_path, inventory):
    entries = _entries(inventory)
    root = _campaign(tmp_path, inventory, entries[1:2])
    selected = _select(root, limits={"tiny": 1})
    assert selected.rows[0]["spec_key"] == spec_key(entries[1]["spec"])
    assert selected.available_count == 1
    assert selected.allocated_count == len(entries)


def test_harness_inventory_checks_claimed_label(tmp_path, inventory):
    entry = deepcopy(_entries(inventory)[0])
    entry["split"] = "train"
    root = _campaign(tmp_path, inventory, [entry])
    with pytest.raises(ValueError, match="claimed split"):
        _select(root, limits={"tiny": 1})


@pytest.mark.parametrize("field,value", [("split_version", "v1"), ("split_policy", "wrong"),
                                        ("spec_key", "wrong"), ("split_entries_sha256", "wrong"),
                                        ("instance", "../../escape.json")])
def test_harness_inventory_rejects_metadata_or_path_tamper(tmp_path, inventory, field, value):
    root = _campaign(tmp_path, inventory, _entries(inventory)[:1])
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["groups"]["synthetic"]["shards"][0][field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        _select(root, limits={"tiny": 1})


def test_harness_inventory_rejects_duplicate_member(tmp_path, inventory):
    entry = _entries(inventory)[0]
    root = _campaign(tmp_path, inventory, [entry, entry])
    with pytest.raises(ValueError, match="duplicate"):
        _select(root, limits={"tiny": 1})


def test_harness_inventory_requires_explicit_release(tmp_path):
    from eval.harness_inventory import select_frozen_instances
    with pytest.raises(TypeError, match="split_manifest"):
        select_frozen_instances(tmp_path)


def test_harness_inventory_rejects_release_file_tamper(tmp_path, inventory):
    root = _campaign(tmp_path, inventory, _entries(inventory)[:1])
    (root / "release.json").write_text(json.dumps({"manifest_sha256": "wrong"}))
    with pytest.raises(ValueError, match="checksum"):
        _select(root, limits={"tiny": 1})


def test_harness_inventory_uses_frozen_order_independent_of_campaign_order(tmp_path, inventory):
    entries = _entries(inventory)[:3]
    root = _campaign(tmp_path, inventory, list(reversed(entries)))
    selected = _select(root, limits={"tiny": 2})
    assert [r["spec_key"] for r in selected.rows] == [spec_key(e["spec"]) for e in entries[:2]]
    assert selected.available_count == 3


def test_harness_inventory_refuses_overrequest_without_repeats(tmp_path, inventory):
    root = _campaign(tmp_path, inventory, _entries(inventory)[:1])
    with pytest.raises(ValueError, match="only 1 available frozen members"):
        _select(root, limits={"tiny": 2})
