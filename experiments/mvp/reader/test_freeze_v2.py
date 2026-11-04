"""Hash-only freeze integrity checks on temporary synthetic files."""
import pytest

from experiments.mvp.reader.freeze_v2 import record, verify


def test_freeze_detects_a_changed_input(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text('{}')
    freeze = {'files_sha256': record([path], tmp_path)}
    verify(freeze, tmp_path)
    path.write_text('{"changed":true}')
    with pytest.raises(ValueError, match='changed or missing'):
        verify(freeze, tmp_path)


def test_freeze_detects_a_missing_input_and_rejects_empty(tmp_path):
    path = tmp_path / 'weights.bin'
    path.write_bytes(b'synthetic')
    freeze = {'files_sha256': record([path], tmp_path)}
    path.unlink()
    with pytest.raises(ValueError, match='changed or missing'):
        verify(freeze, tmp_path)
    with pytest.raises(ValueError, match='empty'):
        verify({'files_sha256': {}}, tmp_path)
