"""Offline tests: every archive here is generated locally, never downloaded."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import warnings
import zipfile

import pytest

from stowage import data_setup


def _archive(path: Path, member: bytes = b"synthetic KCS geometry\n", **extras: bytes):
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("kcs.fix", member)
        for name, payload in extras.items():
            bundle.writestr(name, payload)
    return {
        "archive_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "member_sha256": hashlib.sha256(member).hexdigest(),
    }


def test_local_mock_installs_only_verified_member_and_provenance(tmp_path):
    archive = tmp_path / "mock.zip"
    pins = _archive(archive, **{"../escape.py": b"raise RuntimeError", "kcs.prf": b"ignored"})
    root = tmp_path / "checkout"
    manifest = data_setup.setup_kcs(root, archive=archive, **pins)
    output = root / "data/raw/kcs"
    assert (output / "kcs.fix").read_bytes() == b"synthetic KCS geometry\n"
    assert sorted(path.name for path in output.iterdir()) == ["SETUP-MANIFEST.json", "kcs.fix"]
    assert not (root / "escape.py").exists()
    assert manifest == json.loads((output / "SETUP-MANIFEST.json").read_text())
    assert manifest["source"] == data_setup.KCS_SOURCE_URL
    assert manifest["licence"] == "unstated; not redistributed"
    assert manifest["redistributed"] is False
    assert manifest["method"] == "local_archive"
    assert str(tmp_path) not in json.dumps(manifest)


@pytest.mark.parametrize("which", ["archive_sha256", "member_sha256"])
def test_hash_mismatch_preserves_existing_asset_and_manifest(tmp_path, which):
    archive = tmp_path / "mock.zip"
    pins = _archive(archive)
    root = tmp_path / "checkout"
    output = root / "data/raw/kcs"
    output.mkdir(parents=True)
    (output / "kcs.fix").write_bytes(b"previous")
    (output / "SETUP-MANIFEST.json").write_bytes(b"previous manifest")
    pins[which] = "0" * 64
    with pytest.raises(data_setup.DataSetupError, match="SHA256 mismatch"):
        data_setup.setup_kcs(root, archive=archive, **pins)
    assert (output / "kcs.fix").read_bytes() == b"previous"
    assert (output / "SETUP-MANIFEST.json").read_bytes() == b"previous manifest"
    assert len(list(output.iterdir())) == 2


@pytest.mark.parametrize("members", [["folder/kcs.fix"], ["kcs.fix", "kcs.fix"], ["other"]])
def test_exactly_one_root_member_is_required(tmp_path, members):
    archive = tmp_path / "mock.zip"
    with warnings.catch_warnings():
        # A duplicate is intentional invalid input, not a warning from our code.
        warnings.filterwarnings("ignore", message="Duplicate name:.*", category=UserWarning)
        with zipfile.ZipFile(archive, "w") as bundle:
            for name in members:
                bundle.writestr(name, b"data")
    root = tmp_path / "checkout"
    with pytest.raises(data_setup.DataSetupError, match="exactly one root-level"):
        data_setup.setup_kcs(root, archive=archive, archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    assert not root.exists()


def test_cli_pins_real_hashes_and_reports_local_failure_without_network(tmp_path, capsys):
    archive = tmp_path / "mock.zip"
    _archive(archive)
    assert data_setup.main(["--archive", str(archive), "--root", str(tmp_path / "root")]) == 1
    assert "KCS archive SHA256 mismatch" in capsys.readouterr().err
    assert not (tmp_path / "root").exists()


def test_download_uses_pinned_url_and_same_validation_without_network(tmp_path, monkeypatch):
    import io

    archive = tmp_path / "mock.zip"
    pins = _archive(archive)
    calls = []

    class Response(io.BytesIO):
        def geturl(self):
            return data_setup.KCS_SOURCE_URL

    def fake_urlopen(url, *, timeout):
        calls.append((url, timeout))
        return Response(archive.read_bytes())

    monkeypatch.setattr(data_setup, "urlopen", fake_urlopen)
    manifest = data_setup.setup_kcs(tmp_path / "root", **pins)
    assert calls == [(data_setup.KCS_SOURCE_URL, 30)]
    assert manifest["method"] == "https_download"


def test_oversized_member_is_not_installed(tmp_path, monkeypatch):
    archive = tmp_path / "mock.zip"
    pins = _archive(archive)
    monkeypatch.setattr(data_setup, "MAX_MEMBER_BYTES", 3)
    with pytest.raises(data_setup.DataSetupError, match="safety limit"):
        data_setup.setup_kcs(tmp_path / "root", archive=archive, **pins)
    assert not (tmp_path / "root").exists()


def test_malformed_zip_reports_failure_without_creating_destination(tmp_path):
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a ZIP archive")
    with pytest.raises(data_setup.DataSetupError, match="not a zip file"):
        data_setup.setup_kcs(
            tmp_path / "root", archive=archive,
            archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        )
    assert not (tmp_path / "root").exists()
