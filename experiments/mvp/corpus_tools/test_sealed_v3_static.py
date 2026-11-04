"""Synthetic release contracts; no actual corpus maps or generator/planner calls."""
import json
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sealed_v3


@pytest.fixture
def synthetic_release(tmp_path, monkeypatch):
    out = tmp_path / "synthetic-v3"
    pinned = tmp_path / "epoch"
    out.mkdir()
    pinned.mkdir()
    sources = {"src/stowage/producer.py": "a" * 64, "params/fleet.toml": "b" * 64}
    pin = {"source_sha256": {**sources, "pyproject.toml": "c" * 64}}
    (pinned / "source-pin.json").write_text(json.dumps(pin))
    (pinned / "vessel-cache-acceleration.json").write_text(json.dumps({
        "purpose": "synthetic cache receipt; no numerical execution"}))
    (out / "requested.json").write_text(json.dumps({"assumption": "synthetic fixture"}))
    monkeypatch.setattr(sealed_v3, "OUT", out)
    monkeypatch.setattr(sealed_v3, "EPOCH", pinned)
    monkeypatch.setattr(sealed_v3.epoch, "verify", lambda _: pin)
    monkeypatch.setattr(sealed_v3, "snapshot_sources", lambda: sources)
    monkeypatch.setattr(sealed_v3.identity, "exclusion_inventory", lambda *_: {
        "seeds": set(), "identities": set(), "instance_sha256": set(), "proof": {}})
    monkeypatch.setattr(sealed_v3, "path_for", lambda spec: out / f"seed-{spec['seed']}.json")
    specs = []
    for i in range(45):
        rung = "tiny" if i < 15 else "small"
        j = i if rung == "tiny" else i - 15
        ship_index = j % 3
        family_index = ((j // 3) + ship_index + (rung == "small")) % 2
        spec = dict(rung=rung, vessel=sealed_v3.identity.SHIPS[ship_index],
                    generator=sealed_v3.identity.FAMILIES[family_index],
                    seed=187_000_000 + (100_000 if rung == "small" else 0) + j)
        specs.append(spec)
        path = sealed_v3.path_for(spec)
        path.write_bytes(f"synthetic map bytes {i}".encode())
        audit = dict(config=spec, accepted=True, rule_freeze_v1_enabled=False,
                     c2_screened=False, background_checks=1,
                     background_last={"accepted": True, "physics_sha256": "d" * 64},
                     instance_sha256=sealed_v3.build.sha256(path), instance_versions={},
                     schema_version="synthetic", source_files=sources,
                     stage_environment={}, elapsed_generation_s=0.1)
        path.with_suffix(".audit.json").write_text(json.dumps(audit))
    return out, specs


def test_seal_rejects_changed_raw_bytes_without_parsing(synthetic_release):
    _, specs = synthetic_release
    sealed_v3.path_for(specs[0]).write_bytes(b"changed synthetic bytes")
    with pytest.raises((AssertionError, RuntimeError, ValueError)):
        sealed_v3.seal(specs, time.monotonic())


def test_seal_rejects_historical_epoch_receipt(synthetic_release):
    _, specs = synthetic_release
    receipt = sealed_v3.path_for(specs[0]).with_suffix(".audit.json")
    audit = json.loads(receipt.read_text())
    audit["source_files"] = {"src/stowage/producer.py": "f" * 64}
    receipt.write_text(json.dumps(audit))
    with pytest.raises((AssertionError, RuntimeError, ValueError)):
        sealed_v3.seal(specs, time.monotonic())


def test_successful_synthetic_seal_binds_all_files_counts_and_readonly(synthetic_release):
    out, specs = synthetic_release
    sealed_v3.seal(specs, time.monotonic())
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["sealed"] is True and manifest["status"] == "sealed"
    assert manifest["count"] == 45 and manifest["counts"] == {"tiny": 15, "small": 30}
    assert manifest["planner_runs"] == 0 and manifest["plan_quality_measured"] is False
    assert manifest["generator_checks_only"] is True
    assert len(manifest["files"]) == 45
    counts = {(r["ship"], r["rung"], r["family"]): r["count"]
              for r in manifest["counts_per_ship_rung_family"]}
    for ship in sealed_v3.identity.SHIPS:
        assert sum(counts[ship, "tiny", f] for f in sealed_v3.identity.FAMILIES) == 5
        assert [counts[ship, "small", f] for f in sealed_v3.identity.FAMILIES] == [5, 5]
    expected_paths = {"manifest.json", "README.md", "SEALED.json", "requested.json"}
    expected_paths |= {row[key] for row in manifest["files"] for key in ("path", "audit_path")}
    listed = {}
    for line in (out / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        assert name not in listed
        assert sealed_v3.build.sha256(out / name) == digest
        listed[name] = digest
    assert set(listed) == expected_paths and len(listed) == 94
    for name in expected_paths | {"SHA256SUMS"}:
        assert (out / name).stat().st_mode & 0o222 == 0
    assert manifest["acceleration_receipt_sha256"] == sealed_v3.build.sha256(
        sealed_v3.EPOCH / "vessel-cache-acceleration.json")
    assert json.loads((out / "SEALED.json").read_text())["status"] == "sealed"


def test_worker_refuses_existing_sealed_manifest(synthetic_release, monkeypatch):
    out, _ = synthetic_release
    (out / "manifest.json").write_text(json.dumps({"sealed": True}))
    def forbidden_generation(*_):
        pytest.fail("Worker invoked a generator after sealing")
    monkeypatch.setattr(sealed_v3.build, "generate_one", forbidden_generation)
    with pytest.raises(RuntimeError, match="sealed release cannot be reopened"):
        sealed_v3.worker(0)
