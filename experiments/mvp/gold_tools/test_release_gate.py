"""Synthetic byte fixtures for the small-release coordinator gate."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_gate import SUPPORTED_SHIP_SETTINGS, release_ready


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def frozen(tmp_path):
    rows, hashes, preserved = [], {}, []
    test_specs, showcase_records = [], []
    specs = [(split, "small", ship) for split in ("test", "showcase") for ship in SUPPORTED_SHIP_SETTINGS]
    specs += [("test", "tiny", "feeder_500"), ("showcase", "full", "feeder_1700")]
    for seed, (split, rung, ship) in enumerate(specs):
        path = f"{split}/{rung}/{ship}/{seed}.json"
        audit_path = path.removesuffix(".json") + ".audit.json"
        config = dict(vessel=ship, rung=rung, seed=seed, **(SUPPORTED_SHIP_SETTINGS[ship] if rung == "small" else {}))
        digest = write(tmp_path, path, {"synthetic": True, "seed": seed})
        audit_digest = write(tmp_path, audit_path, dict(config=config, instance_sha256=digest))
        row = dict(path=path, sha256=digest, audit_path=audit_path, audit_sha256=audit_digest,
                   split=split, rung=rung, ship=ship, active_for_current_release=True,
                   release_epoch="v2" if rung == "small" else "v1_preserved")
        if rung == "small":
            row.update(SUPPORTED_SHIP_SETTINGS[ship])
        else:
            preserved.append({k: row[k] for k in ("path", "sha256", "audit_path", "audit_sha256", "split", "rung")})
        if split == "test":
            test_specs.append(config)
        else:
            showcase_records.append(dict(config=config, instance_sha256=digest))
        rows.append(row)
        hashes.update({path: digest, audit_path: audit_digest})
    registry = dict(format="stowlm-mvp-releases-v1", current_release="v2", status="partial_qualified",
                    ship_settings=copy.deepcopy(SUPPORTED_SHIP_SETTINGS), preserved_v1=preserved)
    hashes["releases.json"] = write(tmp_path, "releases.json", registry)
    hashes["test.requested.json"] = write(tmp_path, "test.requested.json", {"specs": test_specs})
    hashes["showcase/showcase-manifest.json"] = write(tmp_path, "showcase/showcase-manifest.json", {"records": showcase_records})
    manifest = dict(format="stowlm-mvp-corpus-v2", release=copy.deepcopy(registry), rule_freeze_v1_enabled=False,
                    files=rows, hashes=hashes)
    write(tmp_path, "manifest.json", manifest)
    return tmp_path, registry, manifest


def test_before_freeze_closed(tmp_path):
    assert release_ready(tmp_path)["ready"] is False


def test_partial_qualified_freeze_ready(frozen):
    result = release_ready(frozen[0])
    assert result["ready"], result
    assert result["qualified_ships"] == sorted(SUPPORTED_SHIP_SETTINGS)


@pytest.mark.parametrize("change", ["pending", "wrong_settings", "wrong_hash", "audit_hash", "preserved_hash", "missing_coverage", "metadata_hash"])
def test_bad_release_closed(frozen, change):
    root, registry, manifest = frozen
    if change == "pending":
        manifest["release"]["status"] = "pending_regeneration"
        write(root, "manifest.json", manifest)
    elif change == "wrong_settings":
        registry["ship_settings"]["feeder_500"]["booking_mass_fraction"] = 0.45
        write(root, "releases.json", registry)
    elif change in {"wrong_hash", "audit_hash", "preserved_hash"}:
        row = manifest["files"][-1] if change == "preserved_hash" else manifest["files"][0]
        write(root, row["audit_path"] if change == "audit_hash" else row["path"], {"changed": True})
    elif change == "missing_coverage":
        manifest["files"][0]["active_for_current_release"] = False
        write(root, "manifest.json", manifest)
    else:
        write(root, "test.requested.json", {"changed": True})
    result = release_ready(root)
    assert result["ready"] is False, result


def test_audit_settings_must_match_even_when_rehashed(frozen):
    root, registry, manifest = frozen
    row = manifest["files"][0]
    path = root / row["audit_path"]
    audit = json.loads(path.read_text())
    audit["config"]["booking_slack"] = 0.1
    digest = write(root, row["audit_path"], audit)
    row["audit_sha256"] = digest
    manifest["hashes"][row["audit_path"]] = digest
    write(root, "manifest.json", manifest)
    assert "settings mismatch" in release_ready(root)["reason"]


def test_unrelated_release_metadata_amendment_keeps_authorized_subset_ready(frozen):
    root, registry, manifest = frozen
    registry["current_release"] = "v3"
    registry["status"] = "pending_regeneration"
    registry["later_note"] = "unrelated medium-release planning"
    write(root, "releases.json", registry)
    for name in ("test.requested.json", "showcase/showcase-manifest.json"):
        metadata = json.loads((root / name).read_text())
        metadata["later_note"] = "informational amendment"
        write(root, name, metadata)
    result = release_ready(root)
    assert result["ready"], result
    assert result["qualified_ships"] == sorted(SUPPORTED_SHIP_SETTINGS)


@pytest.mark.parametrize("change", ["settings", "coverage", "bytes"])
def test_1700_requires_qualified_settings_and_frozen_coverage(frozen, change):
    root, registry, manifest = frozen
    if change == "settings":
        registry["ship_settings"]["feeder_1700"]["booking_slack"] = 0.25
        write(root, "releases.json", registry)
    else:
        row = next(r for r in manifest["files"] if r["ship"] == "feeder_1700" and r["rung"] == "small")
        if change == "coverage":
            row["active_for_current_release"] = False
            write(root, "manifest.json", manifest)
        else:
            write(root, row["path"], {"changed": True})
    assert release_ready(root)["ready"] is False


@pytest.mark.parametrize("split", ["test", "showcase"])
def test_authorized_metadata_changes_rejected(frozen, split):
    root, registry, manifest = frozen
    name, field = ("test.requested.json", "specs") if split == "test" else ("showcase/showcase-manifest.json", "records")
    metadata = json.loads((root / name).read_text())
    metadata[field].pop(0)
    write(root, name, metadata)
    assert release_ready(root)["ready"] is False
