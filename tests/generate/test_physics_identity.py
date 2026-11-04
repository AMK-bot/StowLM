"""Input changes invalidate saved physics evidence without changing verdicts."""
import json
import os
from pathlib import Path

import pytest

from stowage.generate import physics_identity as identity
from stowage.params import repo_root

_REAL_ARTIFACT_DIRECTORIES = identity._artifact_directories


@pytest.fixture(autouse=True)
def isolated_numeric_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(identity, "_artifact_directories", lambda root: [tmp_path])
    identity._ARTIFACT_HASHES.clear()
    yield tmp_path
    identity._ARTIFACT_HASHES.clear()


def test_identity_roundtrips_through_saved_json():
    current = identity.current_physics_identity()
    assert json.loads(json.dumps(current)) == current
    assert identity.current_physics_identity() == current


def test_separately_relocated_liquid_manifest_is_bound(monkeypatch, tmp_path):
    from stowage.checker import stability_liquid
    # Restore the real directory selector rather than the unit fixture's one.
    relocated = tmp_path / "manifests"
    relocated.mkdir()
    monkeypatch.setattr(stability_liquid, "CACHE_DIRECTORY", relocated)
    assert relocated.resolve() in _REAL_ARTIFACT_DIRECTORIES(repo_root())


def test_actual_physics_environment_invalidates_identity(monkeypatch):
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "1")
    before = identity.current_physics_identity()
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "3")
    assert identity.current_physics_identity()["sha256"] != before["sha256"]


@pytest.mark.parametrize("relative", ["src/stowage/solver/ballast.py",
                                     "src/stowage/rules/imdg/data/dgl.json"])
def test_actual_solver_and_rule_bytes_invalidate_identity(monkeypatch, relative):
    target = repo_root() / relative
    assert target.is_file()
    before = identity.current_physics_identity()
    original = Path.read_bytes

    def changed(path):
        raw = original(path)
        return raw + b"\n" if path == target else raw

    # A read hook avoids changing another owner's source on the shared machine.
    monkeypatch.setattr(Path, "read_bytes", changed)
    after = identity.current_physics_identity()
    assert after["sources"][relative] != before["sources"][relative]
    assert after["sha256"] != before["sha256"]


@pytest.mark.parametrize("suffix", [".npz", ".certificate.json", ".qualified.json"])
def test_numeric_and_qualification_bytes_invalidate_even_with_restored_mtime(
        isolated_numeric_artifacts, suffix):
    artifact = isolated_numeric_artifacts / ("table" + suffix)
    artifact.write_bytes(b"first bytes")
    before = identity.current_physics_identity()
    times = artifact.stat()
    artifact.write_bytes(b"other bytes")
    os.utime(artifact, ns=(times.st_atime_ns, times.st_mtime_ns))
    after = identity.current_physics_identity()
    assert after["sha256"] != before["sha256"]
    artifact.unlink()
    assert identity.current_physics_identity()["sha256"] != after["sha256"]


def test_pinned_raw_source_missing_fails_closed(monkeypatch):
    original = Path.read_bytes
    target = repo_root() / "data/raw/kcs/kcs.fix"

    def missing(path):
        if path == target:
            raise FileNotFoundError("isolated missing hull source")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", missing)
    with pytest.raises(identity.PhysicsInputError, match="pinned hull source invalid"):
        identity.current_physics_identity()


def test_referenced_qualification_evidence_bytes_and_availability_are_bound(monkeypatch, tmp_path):
    evidence = tmp_path / "external" / "measured-evidence.json"
    rule_file = repo_root() / "src/stowage/rules/imdg/data/dgl.json"
    original = Path.read_bytes
    document = json.dumps({"path": str(evidence), "sha256": "0" * 64}).encode()
    monkeypatch.setattr(Path, "read_bytes",
        lambda path: document if path == rule_file else original(path))
    missing = identity.current_physics_identity()
    evidence.parent.mkdir()
    evidence.write_text('{"samples": 4}')
    available = identity.current_physics_identity()
    assert available["sha256"] != missing["sha256"]
    evidence.write_text('{"samples": 5}')
    assert identity.current_physics_identity()["sha256"] != available["sha256"]
