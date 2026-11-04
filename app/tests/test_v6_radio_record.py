"""Shot 4's noise line comes from the take's channel record, so a copy without the WAV (D309) still says it."""
import shutil

import pytest

from app import v6


def _take():
    path, sha = v6._radio_take()
    record = path.with_name(path.name.removesuffix(".wav") + ".audio.json")
    if not record.is_file():
        pytest.skip(f"no channel record for the take in this copy: {record}")
    return path, sha, record


def test_noise_line_survives_without_the_wav(monkeypatch, tmp_path):
    path, sha, record = _take()
    expected = v6._radio_channel()[2]["radio_snr_db"]
    shutil.copy(record, tmp_path / record.name)  # the record, never the audio
    gone = tmp_path / path.name
    monkeypatch.setattr(v6, "_radio_take", lambda: (gone, sha))
    assert not gone.exists()
    source, _, values = v6._radio_channel()
    assert values == {"radio_snr_db": expected} and source == tmp_path / record.name
    assert expected == "12"


def test_a_present_wav_is_still_hash_checked(monkeypatch, tmp_path):
    path, sha, record = _take()
    shutil.copy(record, tmp_path / record.name)
    fake = tmp_path / path.name
    fake.write_bytes(b"RIFF not the take")
    monkeypatch.setattr(v6, "_radio_take", lambda: (fake, sha or "0" * 64))
    with pytest.raises(ValueError, match="sha256"):
        v6._radio_channel()
