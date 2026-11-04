"""Epoch fixtures only: no generator, planner or sealed instance is opened."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pytest
import NEWtest_v3_epoch as epoch


def fixture(tmp_path):
    root = tmp_path / 'live'
    for directory in ('src/stowage', 'params/fleet', 'data/raw/kcs', 'experiments/cache/stability', 'experiments/linear-model'):
        (root / directory).mkdir(parents=True)
    (root/'pyproject.toml').write_text('[project]\nname="fixture"\n')
    (root/'src/stowage/__init__.py').write_text('EPOCH = "current"\n')
    (root/'data/raw/kcs/kcs.fix').write_bytes(b'physical hull offsets')
    hull_hash = epoch.sha256(root/'data/raw/kcs/kcs.fix')
    (root/'params/fleet/feeder_500.toml').write_text(f'[hull_form]\nfamily="stowlm_offsets_v1"\noffsets_path="data/raw/kcs/kcs.fix"\noffsets_sha256="{hull_hash}"\n')
    (root/'experiments/cache/stability/table.npz').write_bytes(b'existing qualified artifact')
    return root


def test_snapshot_copies_current_params_hull_and_marker(tmp_path):
    root = fixture(tmp_path)
    target = tmp_path/'epoch'
    pin = epoch.snapshot(target, root)
    assert (target/'src/stowage/__init__.py').read_text() == 'EPOCH = "current"\n'
    assert not (target/'params/fleet/feeder_500.toml').is_symlink()
    assert (target/'data/raw/kcs/kcs.fix').read_bytes() == b'physical hull offsets'
    assert (target/'pyproject.toml').exists()
    assert pin['rule_freeze_v1_enabled'] is False
    assert epoch.verify(target) == pin
    assert epoch.snapshot(target, root) == pin


def test_epoch_remains_current_snapshot_after_live_source_edit(tmp_path):
    root = fixture(tmp_path)
    target = tmp_path/'epoch'
    epoch.snapshot(target, root)
    (root/'src/stowage/__init__.py').write_text('EPOCH = "later"\n')
    epoch.verify(target)
    assert (target/'src/stowage/__init__.py').read_text() == 'EPOCH = "current"\n'


def test_changed_epoch_source_and_linked_cache_fail_closed(tmp_path):
    root = fixture(tmp_path)
    target = tmp_path/'epoch'
    epoch.snapshot(target, root)
    (root/'experiments/cache/stability/table.npz').write_bytes(b'changed cache')
    with pytest.raises(ValueError, match='cache input changed'):
        epoch.verify(target)


def test_bad_current_hull_pin_rejected(tmp_path):
    root = fixture(tmp_path)
    (root/'data/raw/kcs/kcs.fix').write_bytes(b'wrong offsets')
    with pytest.raises(ValueError, match='invalid current hull pin'):
        epoch.snapshot(tmp_path/'epoch', root)


def test_does_not_overwrite_unpinned_directory(tmp_path):
    root = fixture(tmp_path)
    target = tmp_path/'epoch'
    target.mkdir()
    with pytest.raises(ValueError, match='refusing to overwrite'):
        epoch.snapshot(target, root)
