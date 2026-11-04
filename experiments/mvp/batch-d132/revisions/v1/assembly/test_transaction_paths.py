"""State path guards on synthetic roots only; no production invocation."""
import importlib.util
from pathlib import Path
import shutil

import pytest

spec = importlib.util.spec_from_file_location(
    'd132_transaction_fixture', Path(__file__).with_name('test_batch_tool.py'))
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
setup = fixture_module.setup

STATE = Path('experiments/mvp/batch-d132/assembly/install-state')


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns,
                                      p.stat().st_mode)
            for p in root.rglob('*') if p.is_file()}


def test_fresh_install_check_only_makes_no_metadata_or_source_writes(setup):
    tool, root, _, _, _ = setup
    before = snapshot(root)
    result = tool.execute('install', root, check_only=True)
    assert result['writes'] is result['metadata_writes'] is result['source_writes'] is False
    assert not (root / STATE).exists()
    assert snapshot(root) == before


def test_owned_rollback_check_only_is_metadata_free(setup):
    tool, root, _, _, _ = setup
    tool.execute('install', root)
    before = snapshot(root)
    result = tool.execute('rollback', root, check_only=True)
    assert result['metadata_writes'] is result['source_writes'] is False
    assert snapshot(root) == before


@pytest.mark.parametrize('ancestor', [False, True])
def test_root_and_ancestor_symlink_are_rejected_before_state_write(setup, tmp_path, ancestor):
    tool, root, _, _, _ = setup
    alias = tmp_path / 'alias'
    alias.symlink_to(root.parent if ancestor else root, target_is_directory=True)
    supplied = alias / root.name if ancestor else alias
    with pytest.raises(RuntimeError, match='symlink'):
        tool.execute('install', supplied)
    assert not (root / STATE).exists()


@pytest.mark.parametrize('relative', [
    Path('experiments'),
    Path('experiments/mvp'),
    STATE.parent,
    STATE,
    STATE / 'backup',
    STATE / 'backup/src',
    STATE / 'backup/src/stowage',
])
def test_state_and_backup_ancestor_links_never_redirect_writes(setup, tmp_path, relative):
    tool, root, _, _, _ = setup
    external = tmp_path / 'external-state'
    external.mkdir()
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(external, target_is_directory=True)
    with pytest.raises(RuntimeError, match='symlink'):
        tool.execute('install', root)
    assert not list(external.iterdir())
    assert not (root / 'src/stowage/new.py').exists()


@pytest.mark.parametrize('filename', ['transaction.lock', 'state.json'])
@pytest.mark.parametrize('check_only', [False, True])
def test_state_file_symlinks_are_rejected_even_for_preflight(setup, tmp_path, filename, check_only):
    tool, root, _, _, _ = setup
    external = tmp_path / 'external-file'
    external.write_bytes(b'foreign state must survive')
    state = root / STATE
    state.mkdir(parents=True)
    (state / filename).symlink_to(external)
    before = tool.source_info(root)
    with pytest.raises(RuntimeError, match='symlink'):
        tool.execute('install', root, check_only=check_only)
    assert external.read_bytes() == b'foreign state must survive'
    assert tool.source_info(root) == before
    assert sorted(p.name for p in state.iterdir()) == [filename]


def test_rollback_rejects_backup_root_link_before_restoration(setup, tmp_path):
    tool, root, _, manifest, _ = setup
    tool.execute('install', root)
    backup = root / STATE / 'backup'
    external = tmp_path / 'redirected-backup'
    shutil.move(str(backup), external)
    backup.symlink_to(external, target_is_directory=True)
    before = snapshot(external)
    with pytest.raises(RuntimeError, match='symlink'):
        tool.execute('rollback', root)
    assert snapshot(external) == before
    assert tool.source_info(root) == manifest['src_stowage_after']


def test_safe_checks_its_root_itself(setup, tmp_path):
    tool, root, _, _, _ = setup
    alias = tmp_path / 'linked-root'
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(RuntimeError, match='symlink'):
        tool.safe(alias, 'src/stowage/old.py')
